import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { I18nextProvider } from "react-i18next";

import i18n from "@/i18n";
import { HotkeyHelpDialog } from "@/components/HotkeyHelpDialog";

describe("HotkeyHelpDialog", () => {
  it("lists every binding when open", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <HotkeyHelpDialog open onOpenChange={() => {}} />
      </I18nextProvider>,
    );
    expect(screen.getByText(/new chat/i)).toBeInTheDocument();
    expect(screen.getByText(/toggle search/i)).toBeInTheDocument();
    expect(screen.getByText(/previous chat/i)).toBeInTheDocument();
    expect(screen.getByText(/next chat/i)).toBeInTheDocument();
    expect(screen.getByText(/stop generating/i)).toBeInTheDocument();
    expect(screen.getByText(/show this help/i)).toBeInTheDocument();
  });

  it("renders the dialog title from i18n", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <HotkeyHelpDialog open onOpenChange={() => {}} />
      </I18nextProvider>,
    );
    expect(
      screen.getByRole("heading", { name: /keyboard shortcuts/i }),
    ).toBeInTheDocument();
  });

  it("renders nothing when closed", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <HotkeyHelpDialog open={false} onOpenChange={() => {}} />
      </I18nextProvider>,
    );
    expect(screen.queryByText(/new chat/i)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: /keyboard shortcuts/i }),
    ).not.toBeInTheDocument();
  });

  it("calls onOpenChange(false) when Esc closes the dialog", () => {
    const onOpenChange = vi.fn();
    render(
      <I18nextProvider i18n={i18n}>
        <HotkeyHelpDialog open onOpenChange={onOpenChange} />
      </I18nextProvider>,
    );
    // Radix dispatches onEscapeKeyDown when Escape is fired on the dialog
    // content. Target the dialog itself rather than document.body so Radix's
    // FocusScope notices the event regardless of jsdom focus quirks.
    const dialog = screen.getByRole("dialog");
    fireEvent.keyDown(dialog, { key: "Escape", code: "Escape" });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
