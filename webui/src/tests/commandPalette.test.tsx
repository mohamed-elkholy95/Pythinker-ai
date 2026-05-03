import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { I18nextProvider } from "react-i18next";

import i18n from "@/i18n";
import { CommandPalette } from "@/components/CommandPalette";

const COMMANDS = [
  { name: "/help", summary: "Show available commands", usage: "" },
  { name: "/stop", summary: "Stop the current task", usage: "" },
  {
    name: "/dream-log",
    summary: "Show what the last Dream changed",
    usage: "/dream-log [sha]",
  },
];

function r(props: Partial<React.ComponentProps<typeof CommandPalette>> = {}) {
  return render(
    <I18nextProvider i18n={i18n}>
      <CommandPalette
        open
        commands={COMMANDS}
        query=""
        onSelect={() => {}}
        onClose={() => {}}
        anchorRef={{ current: document.body } as React.RefObject<HTMLElement>}
        {...props}
      />
    </I18nextProvider>,
  );
}

describe("CommandPalette", () => {
  it("renders a row per command with name and summary", () => {
    r();
    expect(screen.getByText("/help")).toBeInTheDocument();
    expect(screen.getByText(/Show available commands/i)).toBeInTheDocument();
    expect(screen.getByText("/stop")).toBeInTheDocument();
  });

  it("filters by substring match on name + summary, case-insensitive", () => {
    r({ query: "DREAM" });
    expect(screen.queryByText("/help")).toBeNull();
    expect(screen.getByText("/dream-log")).toBeInTheDocument();
  });

  it("renders empty-state copy when no command matches", () => {
    r({ query: "xyz-nope" });
    expect(screen.getByText(/No commands match/i)).toBeInTheDocument();
  });

  it("clicking a row fires onSelect with the canonical name", () => {
    const onSelect = vi.fn();
    r({ onSelect });
    fireEvent.click(screen.getByText("/stop"));
    expect(onSelect).toHaveBeenCalledWith("/stop");
  });

  it("Enter on the listbox fires onSelect with the highlighted row", () => {
    const onSelect = vi.fn();
    r({ onSelect });
    const list = screen.getByRole("listbox");
    fireEvent.keyDown(list, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("/help");
  });

  it("ArrowDown moves the highlight, Enter fires the new selection", () => {
    const onSelect = vi.fn();
    r({ onSelect });
    const list = screen.getByRole("listbox");
    fireEvent.keyDown(list, { key: "ArrowDown" });
    fireEvent.keyDown(list, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("/stop");
  });

  it("Tab is an alias for Enter (accept selection)", () => {
    const onSelect = vi.fn();
    r({ onSelect });
    const list = screen.getByRole("listbox");
    fireEvent.keyDown(list, { key: "Tab" });
    expect(onSelect).toHaveBeenCalledWith("/help");
  });

  it("Escape fires onClose without onSelect", () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();
    r({ onSelect, onClose });
    const list = screen.getByRole("listbox");
    fireEvent.keyDown(list, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onSelect).not.toHaveBeenCalled();
  });
});
