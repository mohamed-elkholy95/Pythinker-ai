import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

export type SecretRotationModalProps = {
  path: string | null;
  currentPreview: unknown;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onReplace: (path: string, value: string) => Promise<{ restartRequired?: boolean } | void>;
};

function preview(value: unknown): string {
  if (value === undefined || value === null || value === "") return "Not set";
  return "********";
}

export function SecretRotationModal({
  path,
  currentPreview,
  open,
  onOpenChange,
  onReplace,
}: SecretRotationModalProps) {
  const [value, setValue] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  async function submit(): Promise<void> {
    if (!path || !value) return;
    const result = await onReplace(path, value);
    setValue("");
    setNotice(result?.restartRequired ? "Secret replaced. Restart required." : "Secret replaced.");
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-label="Replace secret">
        <DialogHeader>
          <DialogTitle>Replace secret</DialogTitle>
          <DialogDescription>
            Secret values are write-only. Existing values stay redacted.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <label className="grid gap-1 text-sm">
            <span>Canonical path</span>
            <Input readOnly value={path ?? ""} />
          </label>
          <div className="rounded-lg border border-border bg-muted/40 p-3 text-sm">
            Current value: {preview(currentPreview)}
          </div>
          <label className="grid gap-1 text-sm" htmlFor="new-secret-value">
            <span>New secret</span>
            <Input
              id="new-secret-value"
              type="password"
              value={value}
              onChange={(event) => setValue(event.target.value)}
            />
          </label>
          {notice ? <p className="text-sm text-muted-foreground">{notice}</p> : null}
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button type="button" disabled={!value} onClick={() => void submit()}>Replace secret</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
