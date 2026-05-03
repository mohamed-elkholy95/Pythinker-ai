import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export type TagInputProps = {
  label: string;
  value: string[];
  onChange: (value: string[]) => void;
};

export function TagInput({ label, value, onChange }: TagInputProps) {
  const [draft, setDraft] = useState("");

  const addDraft = () => {
    const next = draft.trim();
    if (!next) return;
    onChange([...value, next]);
    setDraft("");
  };

  return (
    <div className="space-y-2">
      <label className="text-sm font-medium text-foreground" htmlFor={`${label}-tag-input`}>
        {label}
      </label>
      <div className="flex flex-wrap gap-2">
        {value.map((tag) => (
          <button
            className="rounded-full border border-border px-2 py-1 text-xs text-muted-foreground"
            key={tag}
            onClick={() => onChange(value.filter((item) => item !== tag))}
            type="button"
          >
            {tag} ×
          </button>
        ))}
      </div>
      <div className="flex gap-2">
        <Input id={`${label}-tag-input`} value={draft} onChange={(event) => setDraft(event.target.value)} />
        <Button onClick={addDraft} type="button" variant="secondary">
          Add
        </Button>
      </div>
    </div>
  );
}
