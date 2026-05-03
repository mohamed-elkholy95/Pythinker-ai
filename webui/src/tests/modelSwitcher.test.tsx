import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { I18nextProvider } from "react-i18next";
import type { ComponentProps } from "react";

import i18n from "@/i18n";
import { ModelSwitcher } from "@/components/ModelSwitcher";

const MODELS = [
  { name: "anthropic/claude-3-5-sonnet-20241022", is_default: true },
  { name: "anthropic/claude-3-5-haiku-20241022", is_default: false },
];

function r(props: Partial<ComponentProps<typeof ModelSwitcher>> = {}) {
  return render(
    <I18nextProvider i18n={i18n}>
      <ModelSwitcher
        models={MODELS}
        currentModel="anthropic/claude-3-5-sonnet-20241022"
        override={null}
        onChange={() => {}}
        {...props}
      />
    </I18nextProvider>,
  );
}

describe("ModelSwitcher", () => {
  it("renders the active model leaf as the trigger label", () => {
    r();
    expect(screen.getByRole("button", { name: /claude-3-5-sonnet/i })).toBeInTheDocument();
  });

  it("uses the override leaf as the trigger when set", () => {
    r({ override: "anthropic/claude-3-5-haiku-20241022" });
    expect(screen.getByRole("button", { name: /claude-3-5-haiku/i })).toBeInTheDocument();
  });

  it("opens the menu and lists every model", () => {
    r();
    fireEvent.click(screen.getByRole("button", { name: /claude-3-5-sonnet/i }));
    expect(screen.getByText("anthropic/claude-3-5-sonnet-20241022")).toBeInTheDocument();
    expect(screen.getByText("anthropic/claude-3-5-haiku-20241022")).toBeInTheDocument();
  });

  it("clicking a non-default model fires onChange with that name", () => {
    const onChange = vi.fn();
    r({ onChange });
    fireEvent.click(screen.getByRole("button", { name: /claude-3-5-sonnet/i }));
    fireEvent.click(screen.getByText("anthropic/claude-3-5-haiku-20241022"));
    expect(onChange).toHaveBeenCalledWith("anthropic/claude-3-5-haiku-20241022");
  });

  it("'Use default' fires onChange with empty string to clear override", () => {
    const onChange = vi.fn();
    r({ override: "anthropic/claude-3-5-haiku-20241022", onChange });
    fireEvent.click(screen.getByRole("button", { name: /claude-3-5-haiku/i }));
    fireEvent.click(screen.getByText(/use default/i));
    expect(onChange).toHaveBeenCalledWith("");
  });
});
