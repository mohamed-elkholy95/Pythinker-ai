import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useHotkey } from "@/hooks/useHotkey";

function clearBody() {
  while (document.body.firstChild) {
    document.body.removeChild(document.body.firstChild);
  }
}

function fireKey(key: string, opts: KeyboardEventInit = {}) {
  document.dispatchEvent(
    new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...opts }),
  );
}

describe("useHotkey", () => {
  beforeEach(() => {
    clearBody();
  });
  afterEach(() => {
    clearBody();
  });

  it("fires the handler for mod+k", () => {
    const handler = vi.fn();
    renderHook(() => useHotkey("mod+k", handler));
    fireKey("k", { metaKey: true });
    fireKey("k", { ctrlKey: true });
    expect(handler).toHaveBeenCalled();
  });

  it("does not fire when an input is focused (default opts)", () => {
    const handler = vi.fn();
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();
    renderHook(() => useHotkey("mod+k", handler));
    fireKey("k", { metaKey: true });
    fireKey("k", { ctrlKey: true });
    expect(handler).not.toHaveBeenCalled();
  });

  it("does not fire when a textarea is focused", () => {
    const handler = vi.fn();
    const ta = document.createElement("textarea");
    document.body.appendChild(ta);
    ta.focus();
    renderHook(() => useHotkey("mod+/", handler));
    fireKey("/", { metaKey: true });
    fireKey("/", { ctrlKey: true });
    expect(handler).not.toHaveBeenCalled();
  });

  it("does not fire when contenteditable is focused", () => {
    const handler = vi.fn();
    const div = document.createElement("div");
    div.setAttribute("contenteditable", "true");
    div.tabIndex = 0;
    document.body.appendChild(div);
    div.focus();
    renderHook(() => useHotkey("mod+k", handler));
    fireKey("k", { metaKey: true });
    fireKey("k", { ctrlKey: true });
    expect(handler).not.toHaveBeenCalled();
  });

  it("fires Esc even when an input is focused", () => {
    const handler = vi.fn();
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();
    renderHook(() => useHotkey("esc", handler));
    fireKey("Escape");
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("fires '?' for help", () => {
    const handler = vi.fn();
    renderHook(() => useHotkey("?", handler));
    fireKey("?", { shiftKey: true });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("fires for mod+up and mod+down arrows", () => {
    const upHandler = vi.fn();
    const downHandler = vi.fn();
    renderHook(() => useHotkey("mod+up", upHandler));
    renderHook(() => useHotkey("mod+down", downHandler));
    fireKey("ArrowUp", { metaKey: true });
    fireKey("ArrowDown", { metaKey: true });
    fireKey("ArrowUp", { ctrlKey: true });
    fireKey("ArrowDown", { ctrlKey: true });
    expect(upHandler).toHaveBeenCalled();
    expect(downHandler).toHaveBeenCalled();
  });

  it("does not fire when modifier is missing", () => {
    const handler = vi.fn();
    renderHook(() => useHotkey("mod+k", handler));
    fireKey("k");
    expect(handler).not.toHaveBeenCalled();
  });

  it("removes the listener on unmount", () => {
    const handler = vi.fn();
    const { unmount } = renderHook(() => useHotkey("mod+k", handler));
    unmount();
    fireKey("k", { metaKey: true });
    fireKey("k", { ctrlKey: true });
    expect(handler).not.toHaveBeenCalled();
  });

  it("respects allowInInputs override", () => {
    const handler = vi.fn();
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();
    renderHook(() => useHotkey("mod+k", handler, { allowInInputs: true }));
    fireKey("k", { metaKey: true });
    fireKey("k", { ctrlKey: true });
    expect(handler).toHaveBeenCalled();
  });

  it("passes the event so handlers can preventDefault", () => {
    const handler = vi.fn((e: KeyboardEvent) => e.preventDefault());
    renderHook(() => useHotkey("mod+k", handler));
    // Fire both modifier variants so the test passes on Linux (ctrl) and Mac (meta).
    fireKey("k", { metaKey: true });
    fireKey("k", { ctrlKey: true });
    expect(handler).toHaveBeenCalled();
    const evt = handler.mock.calls[0]?.[0] as KeyboardEvent;
    expect(evt.defaultPrevented).toBe(true);
  });
});
