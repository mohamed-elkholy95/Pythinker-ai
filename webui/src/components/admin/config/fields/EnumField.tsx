export type EnumFieldProps = {
  label: string;
  options: string[];
  value: string;
  onChange: (value: string) => void;
};

export function EnumField({ label, options, value, onChange }: EnumFieldProps) {
  return (
    <div className="space-y-2">
      <label className="text-sm font-medium text-foreground" htmlFor={`${label}-select`}>
        {label}
      </label>
      <select
        aria-label={label}
        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground outline-none ring-offset-background focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        id={`${label}-select`}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((option) => (
          <option key={option} value={option}>{option}</option>
        ))}
      </select>
    </div>
  );
}
