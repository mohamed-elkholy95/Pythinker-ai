export type BoolFieldProps = {
  label: string;
  value: boolean;
  onChange: (value: boolean) => void;
};

export function BoolField({ label, value, onChange }: BoolFieldProps) {
  const id = `${label}-switch`;
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-border/70 bg-card/45 p-3">
      <label className="text-sm font-medium text-foreground" htmlFor={id}>
        {label}
      </label>
      <button
        aria-checked={value}
        aria-label={label}
        className="relative h-6 w-11 rounded-full border border-border bg-muted transition-colors data-[state=checked]:bg-primary"
        data-state={value ? "checked" : "unchecked"}
        id={id}
        onClick={() => onChange(!value)}
        role="switch"
        type="button"
      >
        <span className="block h-5 w-5 translate-x-0.5 rounded-full bg-background shadow transition-transform data-[state=checked]:translate-x-5" data-state={value ? "checked" : "unchecked"} />
      </button>
    </div>
  );
}
