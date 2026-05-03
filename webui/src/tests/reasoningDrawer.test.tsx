import { fireEvent, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { describe, expect, it } from "vitest";

import { ReasoningDrawer } from "@/components/ReasoningDrawer";
import i18n from "@/i18n";

function r(reasoning: string) {
  return render(
    <I18nextProvider i18n={i18n}>
      <ReasoningDrawer reasoning={reasoning} />
    </I18nextProvider>,
  );
}

describe("ReasoningDrawer", () => {
  it("renders nothing when reasoning is empty", () => {
    const { container } = r("");
    expect(container.firstChild).toBeNull();
  });

  it("starts collapsed (reasoning text not visible)", () => {
    r("private chain of thought");
    expect(screen.queryByText(/private chain of thought/)).toBeNull();
  });

  it("expands on click and shows the reasoning text", () => {
    r("private chain of thought");
    fireEvent.click(screen.getByRole("button", { name: /show reasoning/i }));
    expect(screen.getByText(/private chain of thought/)).toBeInTheDocument();
  });
});
