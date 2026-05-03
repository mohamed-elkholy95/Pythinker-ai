import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export type DynamicMapFieldProps = {
  label: string;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
};

export function DynamicMapField({ label, value, onChange }: DynamicMapFieldProps) {
  const [isAdding, setIsAdding] = useState(false);
  const [draftKey, setDraftKey] = useState("");
  const [draftValue, setDraftValue] = useState("");

  const save = () => {
    const key = draftKey.trim();
    if (!key) return;
    onChange({ ...value, [key]: draftValue });
    setDraftKey("");
    setDraftValue("");
    setIsAdding(false);
  };

  return (
    <div className="space-y-3 rounded-lg border border-border p-3">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm font-medium text-foreground">{label}</span>
        <Button onClick={() => setIsAdding(true)} type="button" variant="secondary">
          Add {label} entry
        </Button>
      </div>
      <div className="space-y-1 text-sm text-muted-foreground">
        {Object.entries(value).map(([key, item]) => (
          <div className="flex items-center justify-between gap-2" key={key}>
            <span>
              {key}: {String(item)}
            </span>
            <Button
              onClick={() => {
                const next = { ...value };
                delete next[key];
                onChange(next);
              }}
              size="sm"
              type="button"
              variant="ghost"
            >
              Remove
            </Button>
          </div>
        ))}
      </div>
      {isAdding ? (
        <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
          <label className="sr-only" htmlFor={`${label}-key`}>
            {label} key
          </label>
          <Input id={`${label}-key`} onChange={(event) => setDraftKey(event.target.value)} value={draftKey} />
          <label className="sr-only" htmlFor={`${label}-value`}>
            {label} value
          </label>
          <Input id={`${label}-value`} onChange={(event) => setDraftValue(event.target.value)} value={draftValue} />
          <Button disabled={!draftKey.trim()} onClick={save} type="button">
            Save {label} entry
          </Button>
        </div>
      ) : null}
    </div>
  );
}
