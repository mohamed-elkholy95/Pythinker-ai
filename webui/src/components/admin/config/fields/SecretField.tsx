import { Button } from "@/components/ui/button";

export type SecretFieldProps = {
  label: string;
  canonicalPath: string;
  onReplaceSecret: (canonicalPath: string) => void;
};

export function SecretField({ label, canonicalPath, onReplaceSecret }: SecretFieldProps) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-border p-3">
      <div>
        <div className="text-sm font-medium text-foreground">{label}</div>
        <div className="text-xs text-muted-foreground">Secret value hidden</div>
      </div>
      <Button onClick={() => onReplaceSecret(canonicalPath)} type="button" variant="secondary">
        Replace secret
      </Button>
    </div>
  );
}
