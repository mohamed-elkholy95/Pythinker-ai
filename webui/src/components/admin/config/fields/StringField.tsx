import { Input } from "@/components/ui/input";

export type StringFieldProps = {
  label: string;
  value: string;
  onChange: (value: string) => void;
};

export function StringField({ label, value, onChange }: StringFieldProps) {
  return (
    <div className="space-y-2">
      <label className="text-sm font-medium text-foreground" htmlFor={label}>
        {label}
      </label>
      <Input id={label} value={value} onChange={(event) => onChange(event.target.value)} />
    </div>
  );
}
