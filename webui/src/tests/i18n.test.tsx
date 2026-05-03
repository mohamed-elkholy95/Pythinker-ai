import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { ThreadComposer } from "@/components/thread/ThreadComposer";

describe("webui i18n", () => {
  it("renders the English composer and language switcher", async () => {
    const user = userEvent.setup();

    render(
      <>
        <LanguageSwitcher />
        <ThreadComposer onSend={vi.fn()} />
      </>,
    );

    expect(
      screen.getByPlaceholderText("Type your message…"),
    ).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("en");

    await user.click(screen.getByRole("button", { name: "Change language" }));
    expect(
      screen.getByRole("menuitemradio", { name: /English/i }),
    ).toBeInTheDocument();
  });
});
