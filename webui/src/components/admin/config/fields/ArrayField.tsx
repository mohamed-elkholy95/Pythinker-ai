import { TagInput } from "../primitives/TagInput";

export type ArrayFieldProps = {
  label: string;
  value: string[];
  onChange: (value: string[]) => void;
};

export function ArrayField({ label, value, onChange }: ArrayFieldProps) {
  return <TagInput label={label} value={value} onChange={onChange} />;
}
