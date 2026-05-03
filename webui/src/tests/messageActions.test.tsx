import { act, fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { I18nextProvider } from "react-i18next";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MessageActions } from "@/components/MessageActions";
import i18n from "@/i18n";

beforeEach(() => {
  vi.clearAllMocks();
});

function renderActions(
  props: Partial<ComponentProps<typeof MessageActions>> = {},
) {
  return render(
    <I18nextProvider i18n={i18n}>
      <MessageActions
        role="assistant"
        text="Hello world"
        onCopy={() => {}}
        onRegenerate={() => {}}
        onEdit={() => {}}
        {...props}
      />
    </I18nextProvider>,
  );
}

describe("MessageActions", () => {
  it("renders copy + regenerate for assistant messages, no edit", () => {
    renderActions({ role: "assistant" });
    expect(screen.getByRole("button", { name: /copy/i })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /regenerate/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /edit/i })).toBeNull();
  });

  it("renders copy + edit for user messages, no regenerate", () => {
    renderActions({ role: "user" });
    expect(screen.getByRole("button", { name: /copy/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /edit/i })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /regenerate/i }),
    ).toBeNull();
  });

  it("clicking copy writes the text to navigator.clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    // happy-dom exposes ``navigator.clipboard`` as a getter, so a plain
    // ``Object.assign`` is rejected; redefine the property directly.
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    renderActions({ text: "hi there" });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /copy/i }));
    });
    expect(writeText).toHaveBeenCalledWith("hi there");
  });

  it("clicking regenerate fires onRegenerate", () => {
    const onRegenerate = vi.fn();
    renderActions({ onRegenerate });
    fireEvent.click(screen.getByRole("button", { name: /regenerate/i }));
    expect(onRegenerate).toHaveBeenCalledTimes(1);
  });

  it("clicking edit fires onEdit", () => {
    const onEdit = vi.fn();
    renderActions({ role: "user", onEdit });
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    expect(onEdit).toHaveBeenCalledTimes(1);
  });
});
