import { AlertTriangle, CheckCircle2, Copy, RotateCcw, Search, Shield } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { AdminConfigPayload, AdminSurfaces, JsonSchemaNode } from "@/lib/admin-api";
import { fetchAdminConfigBackups, fetchAdminConfigSchema } from "@/lib/admin-api";
import type { AdminConfigBackup } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

import { SecretRotationModal } from "./SecretRotationModal";
import { SchemaForm } from "./SchemaForm";
import { AgentsView } from "./services/AgentsView";
import { ChannelsView } from "./services/ChannelsView";
import { CliView } from "./services/CliView";
import { LoggingView } from "./services/LoggingView";
import { NetworkView } from "./services/NetworkView";
import { ProvidersView } from "./services/ProvidersView";
import { RuntimeView } from "./services/RuntimeView";
import { ToolsView } from "./services/ToolsView";
import { UpdatesView } from "./services/UpdatesView";

type ConfigWorkbenchProps = {
  token: string;
  surfaces: AdminSurfaces;
  onRefresh: () => void | Promise<void>;
};

type PendingChange = {
  path: string;
  value: unknown;
  unset?: boolean;
};

type WorkbenchMode = "guided" | "raw" | "diff" | "backups";

type PathEntry = {
  path: string;
  title: string;
  description?: string;
  type?: string;
};

const MODE_LABELS: Record<WorkbenchMode, string> = {
  guided: "Guided",
  raw: "Raw JSON",
  diff: "Diff",
  backups: "Backups",
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function getPathValue(root: Record<string, unknown>, path: string): unknown {
  return path.split(".").reduce<unknown>((current, segment) => {
    if (!current || typeof current !== "object" || Array.isArray(current)) return undefined;
    return (current as Record<string, unknown>)[segment];
  }, root);
}

function setPathValue(
  root: Record<string, unknown>,
  path: string,
  value: unknown,
): Record<string, unknown> {
  const [head, ...tail] = path.split(".");
  if (!head) return root;
  if (tail.length === 0) return { ...root, [head]: value };
  return {
    ...root,
    [head]: setPathValue(asRecord(root[head]), tail.join("."), value),
  };
}

function unsetPathValue(root: Record<string, unknown>, path: string): Record<string, unknown> {
  const [head, ...tail] = path.split(".");
  if (!head) return root;
  if (tail.length === 0) {
    const rest = { ...root };
    delete rest[head];
    return rest;
  }
  return {
    ...root,
    [head]: unsetPathValue(asRecord(root[head]), tail.join(".")),
  };
}

function redactConfig(value: unknown, secretPaths: string[], path = ""): unknown {
  if (secretPaths.includes(path)) return "********";
  if (Array.isArray(value)) {
    return value.map((item, index) => redactConfig(item, secretPaths, `${path}.${index}`));
  }
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).map(([key, child]) => {
      const childPath = path ? `${path}.${key}` : key;
      return [key, redactConfig(child, secretPaths, childPath)];
    }),
  );
}

function flattenEnv(value: unknown, prefix = "PYTHINKER"): string[] {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return [`${prefix}=${JSON.stringify(value)}`];
  }
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) => {
    const nextPrefix = `${prefix}_${key.replace(/[A-Z]/g, (letter) => `_${letter}`).toUpperCase()}`;
    if (child && typeof child === "object" && !Array.isArray(child)) {
      return flattenEnv(child, nextPrefix);
    }
    return [`${nextPrefix}=${JSON.stringify(child)}`];
  });
}

function titleFor(path: string): string {
  return path.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function schemaType(node: JsonSchemaNode): string | undefined {
  return Array.isArray(node.type) ? node.type.find((item) => item !== "null") : node.type;
}

function typeLabel(node?: JsonSchemaNode): string {
  if (!node) return "unknown";
  if (node.enum) return "enum";
  const variants = node.anyOf ?? node.oneOf;
  if (variants) return variants.map((item) => schemaType(item)).filter(Boolean).join(" | ");
  if (node.additionalProperties) return "map";
  return schemaType(node) ?? "unknown";
}

function flattenSchema(node: JsonSchemaNode | null, base = ""): PathEntry[] {
  if (!node?.properties) return [];
  return Object.entries(node.properties).flatMap(([key, child]) => {
    const path = base ? `${base}.${key.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`)}` : key;
    const entry = {
      path,
      title: child.title ?? titleFor(key),
      description: child.description,
      type: typeLabel(child),
    };
    return [entry, ...flattenSchema(child, path)];
  });
}

function displayValue(value: unknown, secret = false): string {
  if (secret) return "********";
  if (value === undefined) return "Not set";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function serviceSection(
  root: string,
  config: Record<string, unknown>,
  surfaces: AdminSurfaces,
  onFocusPath: (path: string) => void,
  onStage: (path: string, value: unknown) => void,
) {
  const props = { config, surfaces: surfaces as unknown as Record<string, unknown>, onFocusPath, onStage };
  if (root === "agents") return <AgentsView {...props} />;
  if (root === "channels") return <ChannelsView {...props} />;
  if (root === "providers") return <ProvidersView {...props} />;
  if (root === "gateway" || root === "api") return <NetworkView {...props} />;
  if (root === "tools") return <ToolsView {...props} />;
  if (root === "runtime") return <RuntimeView {...props} />;
  if (root === "logging") return <LoggingView {...props} />;
  if (root === "updates") return <UpdatesView {...props} />;
  if (root === "cli") return <CliView {...props} />;
  return null;
}

function requiresRestart(path: string, paths: string[]): boolean {
  return paths.some((candidate) => candidate === "*" || path === candidate || path.startsWith(`${candidate}.`));
}

export function ConfigWorkbench({ token, surfaces, onRefresh }: ConfigWorkbenchProps) {
  const { client } = useClient();
  const config = surfaces.config.config;
  const [schema, setSchema] = useState<JsonSchemaNode | null>(null);
  const [secretPaths, setSecretPaths] = useState<string[]>(surfaces.config.secret_paths);
  const [activeRoot, setActiveRoot] = useState("agents");
  const [mode, setMode] = useState<WorkbenchMode>("guided");
  const [query, setQuery] = useState("");
  const [focusedPath, setFocusedPath] = useState("agents");
  const [pending, setPending] = useState<PendingChange[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [secretPath, setSecretPath] = useState<string | null>(null);
  const [backups, setBackups] = useState<AdminConfigBackup[]>([]);
  const [restoreBackup, setRestoreBackup] = useState<AdminConfigBackup | null>(null);
  const [unsetPath, setUnsetPath] = useState<string | null>(null);
  const [restartRequired, setRestartRequired] = useState(false);
  const [applying, setApplying] = useState(false);
  const configMeta = surfaces.config as AdminConfigPayload;
  const restartPaths = configMeta.restart_required_paths ?? [];

  useEffect(() => {
    let cancelled = false;
    fetchAdminConfigSchema(token)
      .then((payload) => {
        if (cancelled) return;
        setSchema(payload.schema);
        setSecretPaths(payload.secret_paths);
      })
      .catch((error: Error) => {
        if (!cancelled) setMessage(error.message);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  useEffect(() => {
    let cancelled = false;
    fetchAdminConfigBackups(token)
      .then((items) => {
        if (!cancelled) setBackups(items.slice(0, 5));
      })
      .catch(() => {
        if (!cancelled) setBackups([]);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const rootEntries = useMemo(
    () => Object.keys(schema?.properties ?? config).sort(),
    [config, schema],
  );
  const pathEntries = useMemo(() => flattenSchema(schema), [schema]);
  const activeSchema = schema?.properties?.[activeRoot];
  const effectiveConfig = useMemo(
    () => pending.reduce((current, item) => {
      if (item.unset) return unsetPathValue(current, item.path);
      return setPathValue(current, item.path, item.value);
    }, config),
    [config, pending],
  );
  const redactedEffective = useMemo(
    () => redactConfig(effectiveConfig, secretPaths),
    [effectiveConfig, secretPaths],
  );
  const filteredPaths = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return [];
    return pathEntries
      .filter((entry) => {
        const haystack = `${entry.path} ${entry.title} ${entry.description ?? ""} ${entry.type ?? ""}`.toLowerCase();
        return haystack.includes(needle);
      })
      .slice(0, 12);
  }, [pathEntries, query]);
  const dashboard = serviceSection(activeRoot, effectiveConfig, surfaces, focusPath, stage);
  const focusedIsSecret = secretPaths.includes(focusedPath);
  const focusedCurrent = getPathValue(config, focusedPath);
  const focusedEffective = getPathValue(effectiveConfig, focusedPath);
  const focusedMeta = pathEntries.find((entry) => entry.path === focusedPath);

  function focusPath(path: string): void {
    const root = path.split(".")[0] || activeRoot;
    if (rootEntries.includes(root)) setActiveRoot(root);
    setFocusedPath(path);
    setMode("guided");
    setMessage(`Focused ${path}.`);
  }

  function stage(path: string, value: unknown): void {
    const root = path.split(".")[0] || activeRoot;
    if (rootEntries.includes(root)) setActiveRoot(root);
    setFocusedPath(path);
    setPending((items) => [...items.filter((item) => item.path !== path), { path, value }]);
    setMessage(null);
  }

  function confirmUnset(): void {
    if (!unsetPath) return;
    setPending((items) => [
      ...items.filter((item) => item.path !== unsetPath),
      { path: unsetPath, value: undefined, unset: true },
    ]);
    setMessage(`Staged unset for ${unsetPath}.`);
    setUnsetPath(null);
  }

  function removePending(path: string): void {
    setPending((items) => items.filter((item) => item.path !== path));
  }

  function replaceSecret(path: string): void {
    setFocusedPath(path);
    setSecretPath(path);
  }

  async function copyJson(): Promise<void> {
    await navigator.clipboard.writeText(JSON.stringify(redactedEffective, null, 2));
    setMessage("Copied redacted JSON.");
  }

  async function copyPath(path: string): Promise<void> {
    await navigator.clipboard.writeText(path);
    setMessage(`Copied ${path}.`);
  }

  async function exportEnv(): Promise<void> {
    await navigator.clipboard.writeText(flattenEnv(redactedEffective).join("\n"));
    setMessage("Copied redacted env lines.");
  }

  async function applyPending(): Promise<void> {
    setApplying(true);
    try {
      let needsRestart = false;
      for (const item of pending) {
        if (secretPaths.includes(item.path)) {
          const result = await client.replaceAdminSecret(item.path, String(item.value));
          needsRestart ||= result.restartRequired;
        } else if (item.unset) {
          const result = await client.unsetAdminConfig(item.path);
          needsRestart ||= result.restartRequired;
        } else {
          const result = await client.setAdminConfig(item.path, item.value);
          needsRestart ||= result.restartRequired;
        }
      }
      setPending([]);
      setRestartRequired(needsRestart);
      setMessage(needsRestart ? "Saved. Gateway restart required." : "Saved.");
      await onRefresh();
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setApplying(false);
    }
  }

  async function confirmRestore(): Promise<void> {
    if (!restoreBackup) return;
    const result = await client.restoreAdminConfigBackup(restoreBackup.id);
    setRestoreBackup(null);
    setRestartRequired(result.restartRequired);
    setMessage(result.restartRequired ? "Backup restored. Restart required after restore." : "Backup restored.");
    await onRefresh();
  }

  return (
    <section className="control-glass overflow-hidden rounded-3xl text-card-foreground">
      <SecretRotationModal
        currentPreview={secretPath ? getPathValue(config, secretPath) : undefined}
        onOpenChange={(open) => setSecretPath(open ? secretPath : null)}
        onReplace={async (path, value) => {
          const result = await client.replaceAdminSecret(path, value);
          setRestartRequired(result.restartRequired);
          setMessage(result.restartRequired ? "Secret replaced. Restart required." : "Secret replaced.");
          await onRefresh();
          return result;
        }}
        open={secretPath !== null}
        path={secretPath}
      />
      <Dialog open={restoreBackup !== null} onOpenChange={(open) => setRestoreBackup(open ? restoreBackup : null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Restore config backup</DialogTitle>
            <DialogDescription>
              Restore {restoreBackup?.id}. This replaces the active config and may require a restart.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => setRestoreBackup(null)}>Cancel</Button>
            <Button type="button" onClick={confirmRestore}>Confirm restore</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={unsetPath !== null} onOpenChange={(open) => setUnsetPath(open ? unsetPath : null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Unset config value</DialogTitle>
            <DialogDescription>
              Stage removal for {unsetPath}. The setting will fall back to its default or environment value.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => setUnsetPath(null)}>Cancel</Button>
            <Button type="button" variant="destructive" onClick={confirmUnset}>Stage unset</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <div className="border-b border-border/70 bg-card/55 p-4 lg:p-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
              <Shield className="h-3.5 w-3.5" /> Config Workbench
              {restartRequired ? (
                <span className="rounded-full border border-amber-400/40 bg-amber-500/10 px-2 py-0.5 text-amber-600">
                  Restart required
                </span>
              ) : null}
              {pending.length > 0 ? (
                <span className="rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-primary">
                  {pending.length} staged
                </span>
              ) : null}
            </div>
            <h2 className="mt-1 text-2xl font-semibold tracking-tight">Workspace configuration</h2>
            <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
              Guided edits are staged first. Secret values stay redacted and write-only; high-impact paths are marked before apply.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button onClick={copyJson} type="button" variant="secondary" className="gap-2">
              <Copy className="h-3.5 w-3.5" /> Copy as JSON
            </Button>
            <Button onClick={exportEnv} type="button" variant="secondary">Export as env</Button>
            <Button disabled={pending.length === 0 || applying} onClick={applyPending} type="button">
              {applying ? "Applying…" : `Apply ${pending.length || ""}`.trim()}
            </Button>
          </div>
        </div>

        <div className="mt-4 grid gap-3 xl:grid-cols-[minmax(18rem,0.9fr)_auto] xl:items-center">
          <label className="relative block" htmlFor="config-search">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              id="config-search"
              className="h-10 rounded-xl pl-9"
              placeholder="Search paths, labels, secrets, or descriptions…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <div className="inline-flex flex-wrap gap-1 rounded-xl border border-border/70 bg-background/55 p-1">
            {(Object.keys(MODE_LABELS) as WorkbenchMode[]).map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => setMode(item)}
                className={cn(
                  "rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
                  mode === item
                    ? "bg-primary/15 text-primary shadow-[inset_0_0_0_1px_hsl(var(--primary)/0.28)]"
                    : "text-muted-foreground hover:bg-accent/40 hover:text-foreground",
                )}
              >
                {MODE_LABELS[item]}
              </button>
            ))}
          </div>
        </div>
        {filteredPaths.length > 0 ? (
          <div className="mt-3 grid max-h-44 gap-1 overflow-auto rounded-xl border border-border/60 bg-background/70 p-2 scrollbar-thin sm:grid-cols-2 xl:grid-cols-3">
            {filteredPaths.map((entry) => (
              <button
                key={entry.path}
                type="button"
                onClick={() => focusPath(entry.path)}
                className="rounded-lg px-2 py-1.5 text-left text-xs hover:bg-accent/40"
              >
                <span className="block truncate font-mono text-foreground">{entry.path}</span>
                <span className="block truncate text-muted-foreground">{entry.title} · {entry.type}</span>
              </button>
            ))}
          </div>
        ) : null}
      </div>

      <div className="grid min-h-[620px] gap-0 xl:grid-cols-[220px_minmax(0,1fr)_330px]">
        <aside className="border-b border-border/70 bg-muted/20 p-3 xl:border-b-0 xl:border-r">
          <div className="flex gap-2 overflow-x-auto pb-1 scrollbar-thin xl:block xl:space-y-1 xl:overflow-visible xl:pb-0" aria-label="Config sections">
            {rootEntries.map((root) => {
              const rootPending = pending.filter((item) => item.path === root || item.path.startsWith(`${root}.`)).length;
              return (
                <Button
                  aria-current={activeRoot === root ? "page" : undefined}
                  className={cn(
                    "shrink-0 justify-start xl:w-full",
                    activeRoot === root && "border-primary bg-primary/10 text-primary",
                  )}
                  key={root}
                  onClick={() => {
                    setActiveRoot(root);
                    setFocusedPath(root);
                    setMode("guided");
                  }}
                  type="button"
                  variant={activeRoot === root ? "outline" : "ghost"}
                >
                  <span className="truncate">{titleFor(root)}</span>
                  {rootPending > 0 ? <span className="ml-auto rounded-full bg-primary/15 px-1.5 text-[10px] text-primary">{rootPending}</span> : null}
                </Button>
              );
            })}
          </div>
        </aside>

        <ScrollArea className="min-h-[620px] bg-background/45 p-4 scrollbar-thin">
          <div className="space-y-5">
            {message ? (
              <div className="flex items-center gap-2 rounded-xl border border-border bg-card/80 p-3 text-sm text-muted-foreground">
                <CheckCircle2 className="h-4 w-4 text-primary" /> {message}
              </div>
            ) : null}
            {restartRequired ? (
              <div className="flex items-start gap-2 rounded-xl border border-amber-400/40 bg-amber-500/10 p-3 text-sm text-amber-600">
                <AlertTriangle className="mt-0.5 h-4 w-4" />
                <span>Saved changes require a gateway restart before they fully take effect.</span>
              </div>
            ) : null}
            {mode === "guided" ? (
              <>
                {dashboard}
                <section className="rounded-2xl border border-border/70 bg-card/65 p-4">
                  <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">Schema editor</p>
                      <h3 className="text-lg font-semibold">{titleFor(activeRoot)}</h3>
                    </div>
                    <Button type="button" variant="outline" size="sm" onClick={() => setUnsetPath(focusedPath)}>
                      Unset focused path
                    </Button>
                  </div>
                  {activeSchema ? (
                    <SchemaForm
                      canonicalPath={activeRoot}
                      displayPath={activeRoot}
                      onReplaceSecret={replaceSecret}
                      onStage={stage}
                      envReferences={configMeta.env_references}
                      fieldDefaults={configMeta.field_defaults}
                      schemaNode={activeSchema}
                      secretPaths={secretPaths}
                      value={asRecord(effectiveConfig)[activeRoot]}
                    />
                  ) : (
                    <pre className="text-xs text-muted-foreground">Schema loading…</pre>
                  )}
                </section>
              </>
            ) : null}
            {mode === "raw" ? (
              <section className="rounded-2xl border border-border/70 bg-card/65 p-4">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">Raw JSON</p>
                    <h3 className="text-lg font-semibold">Redacted effective config</h3>
                  </div>
                  <Button type="button" variant="outline" size="sm" onClick={copyJson}>Copy</Button>
                </div>
                <pre className="max-h-[34rem] overflow-auto rounded-xl border border-border/60 bg-background/75 p-4 text-xs leading-5 scrollbar-thin">
                  {JSON.stringify(redactedEffective, null, 2)}
                </pre>
              </section>
            ) : null}
            {mode === "diff" ? <DiffView config={config} pending={pending} secretPaths={secretPaths} onRemove={removePending} /> : null}
            {mode === "backups" ? <BackupsView backups={backups} onRestore={setRestoreBackup} /> : null}
          </div>
        </ScrollArea>

        <aside className="border-t border-border/70 bg-muted/20 p-4 xl:border-l xl:border-t-0">
          <div className="sticky top-4 space-y-4">
            <section className="rounded-2xl border border-border/70 bg-card/70 p-4">
              <div className="mb-3 flex items-start justify-between gap-2">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">Inspector</p>
                  <h3 className="break-all font-mono text-sm">{focusedPath}</h3>
                </div>
                <Button type="button" variant="ghost" size="icon" onClick={() => void copyPath(focusedPath)} aria-label="Copy focused path">
                  <Copy className="h-4 w-4" />
                </Button>
              </div>
              <div className="space-y-3 text-xs">
                <InspectorRow label="Label" value={focusedMeta?.title ?? titleFor(focusedPath.split(".").at(-1) ?? focusedPath)} />
                <InspectorRow label="Type" value={focusedMeta?.type ?? "object"} />
                <InspectorRow label="Source" value={configMeta.env_references?.[focusedPath] ? `env: ${configMeta.env_references[focusedPath].env_var}` : "config/default"} />
                <InspectorRow label="Restart" value={requiresRestart(focusedPath, restartPaths) ? "Required" : "Hot reload or next turn"} tone={requiresRestart(focusedPath, restartPaths) ? "amber" : "default"} />
                <InspectorRow label="Current" value={displayValue(focusedCurrent, focusedIsSecret)} mono />
                <InspectorRow label="Effective" value={displayValue(focusedEffective, focusedIsSecret)} mono />
              </div>
              {focusedMeta?.description ? <p className="mt-3 text-xs text-muted-foreground">{focusedMeta.description}</p> : null}
              <div className="mt-4 flex gap-2">
                <Button type="button" variant="outline" size="sm" onClick={() => setUnsetPath(focusedPath)}>Unset</Button>
                {focusedIsSecret ? <Button type="button" size="sm" onClick={() => replaceSecret(focusedPath)}>Replace secret</Button> : null}
              </div>
            </section>

            <section className="rounded-2xl border border-border/70 bg-card/70 p-4">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">Pending changes</p>
                  <h3 className="font-semibold">Staged diff</h3>
                </div>
                <Button type="button" variant="ghost" size="icon" disabled={pending.length === 0} onClick={() => setPending([])} aria-label="Clear pending changes">
                  <RotateCcw className="h-4 w-4" />
                </Button>
              </div>
              <PendingList config={config} pending={pending} secretPaths={secretPaths} onRemove={removePending} compact />
              <Button className="mt-4 w-full" disabled={pending.length === 0 || applying} onClick={applyPending} type="button">
                {applying ? "Saving…" : `Save staged ${pending.length || ""}`.trim()}
              </Button>
            </section>

            <section className="rounded-2xl border border-border/70 bg-card/70 p-4">
              <div className="mb-3">
                <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">Backups</p>
                <h3 className="font-semibold">Recent restore points</h3>
              </div>
              <div className="space-y-2">
                {backups.length === 0 ? <p className="text-sm text-muted-foreground">No backups found.</p> : backups.map((backup, index) => (
                  <div className="rounded-lg border border-border bg-background/70 p-2 text-xs" key={backup.id}>
                    <div className="break-all font-mono font-semibold">version {index + 1}: {backup.id}</div>
                    <Button className="mt-2 h-7 px-2 text-xs" onClick={() => setRestoreBackup(backup)} type="button" variant="outline">
                      Restore backup {index + 1}
                    </Button>
                  </div>
                ))}
              </div>
            </section>
          </div>
        </aside>
      </div>
    </section>
  );
}

function InspectorRow({
  label,
  value,
  mono = false,
  tone = "default",
}: {
  label: string;
  value: string;
  mono?: boolean;
  tone?: "default" | "amber";
}) {
  return (
    <div className="rounded-lg border border-border/60 bg-background/60 p-2">
      <div className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">{label}</div>
      <div className={cn("mt-1 break-all", mono && "font-mono", tone === "amber" && "text-amber-600")}>{value}</div>
    </div>
  );
}

function PendingList({
  config,
  pending,
  secretPaths,
  onRemove,
  compact = false,
}: {
  config: Record<string, unknown>;
  pending: PendingChange[];
  secretPaths: string[];
  onRemove: (path: string) => void;
  compact?: boolean;
}) {
  if (pending.length === 0) {
    return <p className="text-sm text-muted-foreground">No staged edits yet.</p>;
  }
  return (
    <div className="space-y-3">
      {pending.map((item) => {
        const isSecret = secretPaths.includes(item.path);
        return (
          <div className="rounded-lg border border-border bg-background/70 p-3 text-xs" key={item.path}>
            <div className="flex items-start justify-between gap-2">
              <div className="break-all font-mono font-semibold">{item.path}</div>
              <Button className="h-6 px-2 text-[11px]" onClick={() => onRemove(item.path)} size="sm" type="button" variant="ghost">
                Remove
              </Button>
            </div>
            <div className={cn("mt-2 grid gap-1 text-muted-foreground", !compact && "sm:grid-cols-2")}>
              <span>Before: {displayValue(getPathValue(config, item.path), isSecret)}</span>
              <span>After: {item.unset ? "Unset" : displayValue(item.value, isSecret)}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function DiffView({
  config,
  pending,
  secretPaths,
  onRemove,
}: {
  config: Record<string, unknown>;
  pending: PendingChange[];
  secretPaths: string[];
  onRemove: (path: string) => void;
}) {
  return (
    <section className="rounded-2xl border border-border/70 bg-card/65 p-4">
      <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">Review before apply</p>
      <h3 className="mb-4 text-lg font-semibold">Pending diff</h3>
      <PendingList config={config} pending={pending} secretPaths={secretPaths} onRemove={onRemove} />
    </section>
  );
}

function BackupsView({
  backups,
  onRestore,
}: {
  backups: AdminConfigBackup[];
  onRestore: (backup: AdminConfigBackup) => void;
}) {
  return (
    <section className="rounded-2xl border border-border/70 bg-card/65 p-4">
      <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">Backups</p>
      <h3 className="mb-4 text-lg font-semibold">Restore points</h3>
      <div className="space-y-2">
        {backups.length === 0 ? <p className="text-sm text-muted-foreground">No backups found.</p> : backups.map((backup, index) => (
          <div className="flex flex-col gap-3 rounded-xl border border-border bg-background/70 p-3 text-xs sm:flex-row sm:items-center sm:justify-between" key={backup.id}>
            <div className="min-w-0">
              <div className="break-all font-mono font-semibold">version {index + 1}: {backup.id}</div>
              <div className="mt-1 text-muted-foreground">{backup.kind} · {new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(backup.mtime_ms))}</div>
            </div>
            <Button onClick={() => onRestore(backup)} type="button" variant="outline">Restore backup {index + 1}</Button>
          </div>
        ))}
      </div>
    </section>
  );
}
