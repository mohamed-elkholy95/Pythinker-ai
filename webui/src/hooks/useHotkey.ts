import { useEffect, useRef } from "react";

export interface HotkeyOpts {
  /** When true, fire even if the active element is editable. Default false. */
  allowInInputs?: boolean;
}

const IS_MAC =
  typeof navigator !== "undefined" &&
  /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent || "");

interface ParsedCombo {
  mod: boolean;
  shift: boolean;
  alt: boolean;
  key: string;
}

function parseCombo(combo: string): ParsedCombo {
  const parts = combo
    .toLowerCase()
    .split("+")
    .map((s) => s.trim());
  const out: ParsedCombo = { mod: false, shift: false, alt: false, key: "" };
  for (const p of parts) {
    if (p === "mod") out.mod = true;
    else if (p === "shift") out.shift = true;
    else if (p === "alt" || p === "option") out.alt = true;
    else out.key = p;
  }
  return out;
}

function eventMatches(e: KeyboardEvent, combo: ParsedCombo): boolean {
  if (combo.mod) {
    if (IS_MAC ? !e.metaKey : !e.ctrlKey) return false;
  }
  if (combo.shift && !e.shiftKey) return false;
  if (combo.alt && !e.altKey) return false;
  const key = e.key.toLowerCase();
  if (combo.key === "up") return key === "arrowup";
  if (combo.key === "down") return key === "arrowdown";
  if (combo.key === "left") return key === "arrowleft";
  if (combo.key === "right") return key === "arrowright";
  if (combo.key === "esc") return key === "escape";
  if (combo.key === "/") return key === "/";
  if (combo.key === "?") return key === "?" || (key === "/" && e.shiftKey);
  return key === combo.key;
}

function isEditableElement(el: Element | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (el.isContentEditable) return true;
  // jsdom does not always reflect the attribute through `isContentEditable`.
  const attr = el.getAttribute("contenteditable");
  if (attr !== null && attr.toLowerCase() !== "false") return true;
  return false;
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (isEditableElement(target as Element | null)) return true;
  // Real keyboard events bubble from the focused element so `target` matches
  // `activeElement`. But synthetic events dispatched on `document` arrive with
  // `target === document`, so fall back to `activeElement` to stay correct in
  // both contexts.
  if (typeof document !== "undefined") {
    return isEditableElement(document.activeElement);
  }
  return false;
}

/**
 * Bind a global keyboard combo to a handler. The listener attaches at the
 * document in the capture phase so a focused textarea's `onKeyDown` cannot
 * swallow the event before we see it; cleans up on unmount.
 *
 * Combo grammar: ``mod+k``, ``mod+/``, ``mod+up``, ``mod+down``, ``esc``, ``?``.
 * ``mod`` resolves to Cmd on macOS and Ctrl elsewhere (detected via
 * `navigator.platform`).
 *
 * By default the handler is **skipped** when an editable element (input,
 * textarea, contenteditable) is focused — except for ``esc``, which always
 * fires so users can cancel an in-flight stream while typing. Pass
 * `{ allowInInputs: true }` to override.
 *
 * Handlers receive the raw `KeyboardEvent`; call `event.preventDefault()` to
 * override browser defaults like ⌘K opening Chrome's quick search.
 */
export function useHotkey(
  combo: string,
  handler: (event: KeyboardEvent) => void,
  opts: HotkeyOpts = {},
): void {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  const { allowInInputs } = opts;

  useEffect(() => {
    const parsed = parseCombo(combo);
    const isEsc = parsed.key === "esc";
    const onKey = (e: KeyboardEvent) => {
      if (!eventMatches(e, parsed)) return;
      if (!isEsc && !allowInInputs && isEditableTarget(e.target)) return;
      handlerRef.current(e);
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [combo, allowInInputs]);
}
