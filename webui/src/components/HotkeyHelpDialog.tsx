import { useTranslation } from "react-i18next";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface HotkeyHelpDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const IS_MAC =
  typeof navigator !== "undefined" &&
  /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent || "");
const MOD = IS_MAC ? "⌘" : "Ctrl";

interface Binding {
  keys: string[];
  i18nKey: string;
}

const BINDINGS: Binding[] = [
  { keys: [MOD, "K"], i18nKey: "hotkeys.newChat" },
  { keys: [MOD, "/"], i18nKey: "hotkeys.toggleSearch" },
  { keys: [MOD, "↑"], i18nKey: "hotkeys.sidebarUp" },
  { keys: [MOD, "↓"], i18nKey: "hotkeys.sidebarDown" },
  { keys: ["Esc"], i18nKey: "hotkeys.stop" },
  { keys: ["?"], i18nKey: "hotkeys.help" },
];

export function HotkeyHelpDialog({ open, onOpenChange }: HotkeyHelpDialogProps) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t("hotkeys.title")}</DialogTitle>
          <DialogDescription className="sr-only">
            {t("hotkeys.title")}
          </DialogDescription>
        </DialogHeader>
        <ul className="flex flex-col gap-2 pt-2 text-sm">
          {BINDINGS.map(({ keys, i18nKey }) => (
            <li
              key={i18nKey}
              className="flex items-center justify-between gap-4"
            >
              <span className="text-muted-foreground">{t(i18nKey)}</span>
              <span className="flex items-center gap-1">
                {keys.map((k, i) => (
                  <kbd
                    key={i}
                    className="rounded border border-border/60 bg-muted/60 px-1.5 py-0.5 font-mono text-[11px] text-foreground/80"
                  >
                    {k}
                  </kbd>
                ))}
              </span>
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  );
}
