import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import { Badge } from "../primitives/Badge";
import { SsrfCoverageBar } from "../visualizations/SsrfCoverageBar";
import { MetricCard, ServicePanel, recordAt, type WorkbenchServiceProps } from "./shared";

function currentWhitelist(config: Record<string, unknown>, surfaces: Record<string, unknown>): string[] {
  const configList = recordAt(config, "tools").ssrf_whitelist;
  if (Array.isArray(configList)) return configList.filter((item): item is string => typeof item === "string");
  const tools = surfaces.tools && typeof surfaces.tools === "object" ? (surfaces.tools as Record<string, unknown>) : {};
  const ssrf = tools.ssrf && typeof tools.ssrf === "object" ? (tools.ssrf as Record<string, unknown>) : {};
  return Array.isArray(ssrf.whitelist) ? ssrf.whitelist.filter((item): item is string => typeof item === "string") : [];
}

function blockedCategories(surfaces: Record<string, unknown>): string[] {
  const tools = surfaces.tools && typeof surfaces.tools === "object" ? (surfaces.tools as Record<string, unknown>) : {};
  const ssrf = tools.ssrf && typeof tools.ssrf === "object" ? (tools.ssrf as Record<string, unknown>) : {};
  return Array.isArray(ssrf.blocked_categories) ? ssrf.blocked_categories.filter((item): item is string => typeof item === "string") : [];
}

function cidrError(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  if (trimmed === "0.0.0.0/0" || trimmed === "::/0") return "Overbroad SSRF whitelist range.";
  const ipv4 = /^(\d{1,3}\.){3}\d{1,3}\/(\d|[12]\d|3[0-2])$/.test(trimmed);
  const ipv6 = /^[0-9a-fA-F:]+\/(\d|[1-9]\d|1[01]\d|12[0-8])$/.test(trimmed) && trimmed.includes(":");
  if (!ipv4 && !ipv6) return "Malformed CIDR range.";
  if (["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16"].includes(trimmed)) {
    return "Private-range supersets stay blocked by default.";
  }
  if (ipv4) {
    const [address] = trimmed.split("/");
    if (address.split(".").some((part) => Number(part) > 255)) return "Malformed CIDR range.";
  }
  return null;
}

export function RuntimeView({ config, surfaces, onFocusPath, onStage }: WorkbenchServiceProps) {
  const whitelist = useMemo(() => currentWhitelist(config, surfaces), [config, surfaces]);
  const blocked = useMemo(() => blockedCategories(surfaces), [surfaces]);
  const [draft, setDraft] = useState("");
  const error = cidrError(draft);
  const runtime = recordAt(config, "runtime");

  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-3">
        <MetricCard label="Policy" value={String(runtime.policy_enabled ?? false)} hint="Governed runtime switch" />
        <MetricCard label="Manifests" value={String(runtime.manifests_dir ?? "default")} />
        <MetricCard label="Sandbox" value="bubblewrap" hint="Linux shell isolation" />
      </div>
      <ServicePanel title="SSRF guardrails">
        <SsrfCoverageBar blocked={blocked} />
        <div className="mt-3 flex flex-wrap gap-1.5">
          {whitelist.map((item) => <Badge key={item}>{item}</Badge>)}
        </div>
        <label className="mt-3 grid gap-1 text-sm" htmlFor="ssrf-whitelist-cidr">
          <span>SSRF whitelist CIDR</span>
          <Input id="ssrf-whitelist-cidr" value={draft} onChange={(event) => setDraft(event.target.value)} />
        </label>
        {error ? <p className="mt-2 text-sm text-destructive">{error}</p> : null}
        <div className="mt-3 flex gap-2">
          <Button disabled={!draft.trim() || !!error} onClick={() => onStage("tools.ssrf_whitelist", [...whitelist, draft.trim()])} type="button">Stage SSRF whitelist</Button>
          <Button onClick={() => onFocusPath("tools.ssrf_whitelist")} type="button" variant="outline">Focus path</Button>
        </div>
      </ServicePanel>
    </div>
  );
}
