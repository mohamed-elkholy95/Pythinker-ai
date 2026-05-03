import { Input } from "@/components/ui/input";

export type NumberFieldProps = {
  label: string;
  value: number;
  minimum?: number;
  maximum?: number;
  onChange: (value: number) => void;
};

export function NumberField({ label, value, minimum, maximum, onChange }: NumberFieldProps) {
  return (
    <div className="space-y-2">
      <label className="text-sm font-medium text-foreground" htmlFor={label}>
        {label}
      </label>
      <Input
        id={label}
        max={maximum}
        min={minimum}
        onChange={(event) => onChange(Number(event.target.value))}
        type="number"
        value={Number.isFinite(value) ? value : 0}
      />
    </div>
  );
}
