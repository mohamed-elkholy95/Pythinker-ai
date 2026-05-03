import type { ReactNode } from "react";

import type { JsonSchemaNode } from "@/lib/admin-api";

import { Button } from "@/components/ui/button";

import { ArrayField } from "./fields/ArrayField";
import { BoolField } from "./fields/BoolField";
import { DynamicMapField } from "./fields/DynamicMapField";
import { EnumField } from "./fields/EnumField";
import { NumberField } from "./fields/NumberField";
import { SecretField } from "./fields/SecretField";
import { StringField } from "./fields/StringField";

export type FieldChange = (canonicalPath: string, value: unknown) => void;

export type SchemaFormProps = {
  schemaNode: JsonSchemaNode;
  value: unknown;
  displayPath: string;
  canonicalPath: string;
  secretPaths: string[];
  envReferences?: Record<string, { env_var: string; is_secret: boolean }>;
  fieldDefaults?: Record<string, unknown>;
  onStage: FieldChange;
  onReplaceSecret: (canonicalPath: string) => void;
};

type ResolvedSchema = {
  node: JsonSchemaNode;
  nullable: boolean;
};

export function toSnakeSegment(segment: string): string {
  return segment.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`);
}

export function joinPath(base: string, segment: string, canonical = true): string {
  const next = canonical ? toSnakeSegment(segment) : segment;
  return base ? `${base}.${next}` : next;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function labelFor(node: JsonSchemaNode, fallback: string): string {
  return node.title || fallback.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function schemaType(node: JsonSchemaNode): string | undefined {
  return Array.isArray(node.type) ? node.type.find((item) => item !== "null") : node.type;
}

function resolveNullable(node: JsonSchemaNode): ResolvedSchema {
  const variants = node.anyOf ?? node.oneOf;
  if (!variants) return { node, nullable: false };
  const nonNull = variants.find((item) => schemaType(item) !== "null");
  return { node: nonNull ?? node, nullable: variants.some((item) => schemaType(item) === "null") };
}

function stringOptions(values: unknown[] | undefined): string[] {
  return (values ?? []).filter((item): item is string => typeof item === "string");
}

function FieldShell({
  label,
  path,
  description,
  badges,
  children,
}: {
  label: string;
  path: string;
  description?: string;
  badges?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-border/70 bg-background/60 p-3 shadow-sm">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h4 className="text-sm font-semibold text-foreground">{label}</h4>
          <p className="mt-0.5 break-all font-mono text-[11px] text-muted-foreground">{path}</p>
          {description ? <p className="mt-1 text-xs text-muted-foreground">{description}</p> : null}
        </div>
        {badges}
      </div>
      {children}
    </section>
  );
}

export function SchemaForm({
  schemaNode,
  value,
  displayPath,
  canonicalPath,
  secretPaths,
  envReferences = {},
  fieldDefaults = {},
  onStage,
  onReplaceSecret,
}: SchemaFormProps) {
  const { node, nullable } = resolveNullable(schemaNode);
  const type = schemaType(node);
  const label = labelFor(node, displayPath.split(".").at(-1) || canonicalPath || "Config");
  const envRef = envReferences[canonicalPath];
  const defaultValue = fieldDefaults[canonicalPath];
  const differsFromDefault = defaultValue !== undefined && JSON.stringify(defaultValue) !== JSON.stringify(value);
  const badges = (
    <div className="flex flex-wrap gap-1 text-[11px]">
      {envRef ? <span className="rounded-full border border-border px-2 py-0.5 text-muted-foreground">${`{${envRef.env_var}}`}</span> : null}
      {differsFromDefault ? <span className="rounded-full border border-border px-2 py-0.5 text-muted-foreground">default: {JSON.stringify(defaultValue)}</span> : null}
    </div>
  );

  if (secretPaths.includes(canonicalPath)) {
    return (
      <FieldShell label={label} path={canonicalPath} description={node.description} badges={badges}>
        <SecretField canonicalPath={canonicalPath} label={label} onReplaceSecret={onReplaceSecret} />
      </FieldShell>
    );
  }

  const clearButton = nullable ? (
    <Button onClick={() => onStage(canonicalPath, null)} type="button" variant="secondary">
      Clear {label}
    </Button>
  ) : null;

  if (type === "object" && node.properties) {
    const record = asRecord(value);
    const fields = (
      <div className="space-y-4">
        {Object.entries(node.properties).map(([key, child]) => (
          <SchemaForm
            canonicalPath={joinPath(canonicalPath, key)}
            displayPath={joinPath(displayPath, key, false)}
            key={key}
            onReplaceSecret={onReplaceSecret}
            onStage={onStage}
            envReferences={envReferences}
            fieldDefaults={fieldDefaults}
            schemaNode={child}
            secretPaths={secretPaths}
            value={record[key]}
          />
        ))}
      </div>
    );
    if (canonicalPath === displayPath && canonicalPath.split(".").length === 1) return fields;
    return (
      <FieldShell label={label} path={canonicalPath} description={node.description} badges={badges}>
        {fields}
      </FieldShell>
    );
  }

  if (type === "object" && node.additionalProperties) {
    return (
      <FieldShell label={label} path={canonicalPath} description={node.description} badges={badges}>
        <DynamicMapField
          label={label}
          onChange={(next) => onStage(canonicalPath, next)}
          value={asRecord(value)}
        />
      </FieldShell>
    );
  }

  if (type === "boolean") {
    return (
      <FieldShell label={label} path={canonicalPath} description={node.description} badges={badges}>
        <BoolField
          label={label}
          onChange={(next) => onStage(canonicalPath, next)}
          value={typeof value === "boolean" ? value : false}
        />
      </FieldShell>
    );
  }

  if (node.enum) {
    return (
      <FieldShell label={label} path={canonicalPath} description={node.description} badges={badges}>
        <EnumField
          label={label}
          onChange={(next) => onStage(canonicalPath, next)}
          options={stringOptions(node.enum)}
          value={typeof value === "string" ? value : ""}
        />
      </FieldShell>
    );
  }

  if (type === "integer" || type === "number") {
    return (
      <FieldShell label={label} path={canonicalPath} description={node.description} badges={badges}>
        <div className="space-y-2">
          <NumberField
            label={label}
            maximum={node.maximum}
            minimum={node.minimum}
            onChange={(next) => onStage(canonicalPath, next)}
            value={typeof value === "number" ? value : 0}
          />
          {clearButton}
        </div>
      </FieldShell>
    );
  }

  if (type === "array") {
    const itemsAreStrings = schemaType(node.items ?? {}) === "string";
    if (itemsAreStrings) {
      return (
        <FieldShell label={label} path={canonicalPath} description={node.description} badges={badges}>
          <ArrayField
            label={label}
            onChange={(next) => onStage(canonicalPath, next)}
            value={Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []}
          />
        </FieldShell>
      );
    }
  }

  if (type === "string") {
    return (
      <FieldShell label={label} path={canonicalPath} description={node.description} badges={badges}>
        <div className="space-y-2">
          <StringField
            label={label}
            onChange={(next) => onStage(canonicalPath, next)}
            value={typeof value === "string" ? value : ""}
          />
          {clearButton}
        </div>
      </FieldShell>
    );
  }

  return (
    <pre className="rounded-lg border border-border bg-muted p-3 text-xs text-muted-foreground">
      Schema mismatch for {canonicalPath || displayPath}
    </pre>
  );
}
