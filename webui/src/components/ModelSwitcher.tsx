import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { ModelRow } from "@/lib/api";
import { cn } from "@/lib/utils";

interface ModelSwitcherProps {
  models: ModelRow[];
  /** Configured default for the current chat (no override applied). */
  currentModel: string;
  /** Per-chat override; null means "use default". */
  override: string | null;
  /** Pass empty string to clear the override. */
  onChange: (modelOrEmpty: string) => void;
}

function leaf(name: string): string {
  return name.split("/").pop() || name;
}

/**
 * Inline switcher rendered as a small pill in the composer footer. Replaces
 * the passive ``<span>`` model-name pill (Phase 1/2 had read-only display).
 *
 * Selection writes the per-chat override via the parent's ``onChange``; an
 * empty string clears the override and reverts to ``currentModel``.
 */
export function ModelSwitcher({
  models,
  currentModel,
  override,
  onChange,
}: ModelSwitcherProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  const active = override ?? currentModel;
  const hasOverride = override !== null && override !== "";

  useEffect(() => {
    if (!open) return;
    const onDocClick = (ev: MouseEvent) => {
      if (!rootRef.current) return;
      if (!rootRef.current.contains(ev.target as Node)) setOpen(false);
    };
    const onEsc = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  const handleSelect = (name: string) => {
    setOpen(false);
    onChange(name === currentModel ? "" : name);
  };

  return (
    <div ref={rootRef} className="relative inline-block">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        title={t("model.switcher.label") + ": " + active}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "inline-flex min-w-0 items-center gap-1.5 rounded-full border px-2.5 py-1",
          "border-foreground/10 bg-foreground/[0.035] font-medium text-foreground/80",
          "hover:bg-foreground/[0.06] focus-visible:outline-none focus-visible:ring-2",
          "focus-visible:ring-ring text-[10.5px]",
        )}
      >
        <span
          aria-hidden
          className={cn(
            "h-1.5 w-1.5 flex-none rounded-full",
            hasOverride ? "bg-amber-500/80" : "bg-emerald-500/80",
          )}
        />
        <span className="truncate">{leaf(active)}</span>
        <ChevronDown size={11} className="text-muted-foreground/70" />
      </button>
      {open ? (
        <div
          role="menu"
          className={cn(
            "absolute left-0 bottom-full z-50 mb-1.5 min-w-[16rem] rounded-xl border",
            "border-border/70 bg-popover p-1 shadow-lg",
          )}
        >
          {models.length === 0 ? (
            <div className="px-3 py-2 text-[12px] text-muted-foreground">
              {t("model.switcher.empty")}
            </div>
          ) : (
            <ul className="flex flex-col">
              {models.map((m) => {
                const isActive = m.name === active;
                return (
                  <li key={m.name}>
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => handleSelect(m.name)}
                      className={cn(
                        "flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-1.5 text-left",
                        "hover:bg-accent",
                      )}
                    >
                      <span className="truncate text-[12px]">{m.name}</span>
                      {isActive ? <Check size={12} /> : null}
                    </button>
                  </li>
                );
              })}
              {hasOverride ? (
                <li>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setOpen(false);
                      onChange("");
                    }}
                    className={cn(
                      "mt-1 flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left",
                      "border-t border-border/50 pt-2 text-[11px] text-muted-foreground hover:bg-accent",
                    )}
                  >
                    {t("model.switcher.default")}
                  </button>
                </li>
              ) : null}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
