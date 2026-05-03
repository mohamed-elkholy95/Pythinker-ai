import { fireEvent, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MessageBubble } from "@/components/MessageBubble";
import i18n from "@/i18n";
import type { UIMessage } from "@/lib/types";

beforeEach(() => {
  vi.clearAllMocks();
});

function userMsg(content: string): UIMessage {
  return { id: "u1", role: "user", content, createdAt: 1 };
}

describe("UserInlineEditor", () => {
  it("disables Save when the trimmed value equals the trimmed initial", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <MessageBubble
          message={userMsg("hello")}
          onRegenerate={() => {}}
          onEdit={() => {}}
        />
      </I18nextProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "hello   " } });
    expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();
  });

  it("enables Save when the trimmed value differs from the trimmed initial", () => {
    const onEdit = vi.fn();
    render(
      <I18nextProvider i18n={i18n}>
        <MessageBubble
          message={userMsg("hello")}
          onRegenerate={() => {}}
          onEdit={onEdit}
        />
      </I18nextProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "hello world" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(onEdit).toHaveBeenCalledWith("u1", "hello world");
  });
});
