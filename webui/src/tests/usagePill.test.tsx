import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { UsagePill } from "@/components/UsagePill";

function r(used: number, limit: number) {
  return render(
    <I18nextProvider i18n={i18n}>
      <UsagePill used={used} limit={limit} />
    </I18nextProvider>,
  );
}

describe("UsagePill", () => {
  it("renders a thousands-formatted used / limit pair", () => {
    r(12_400, 200_000);
    expect(screen.getByText(/12\.4k\s*\/\s*200k\s*tokens/i)).toBeInTheDocument();
  });

  it("renders nothing when limit is zero (no active session)", () => {
    const { container } = r(0, 0);
    expect(container.firstChild).toBeNull();
  });

  it("uses the warning style above 75%", () => {
    r(160_000, 200_000); // 80%
    const root = screen.getByRole("status");
    expect(root.className).toMatch(/amber/);
  });

  it("uses the critical style above 90%", () => {
    r(190_000, 200_000); // 95%
    const root = screen.getByRole("status");
    expect(root.className).toMatch(/destructive/);
  });

  it("clamps the bar at 100% even when used exceeds limit", () => {
    r(300_000, 200_000); // 150% — render width must clamp
    const bar = screen.getByRole("status").querySelector("[style*='width']");
    expect(bar).not.toBeNull();
    const widthStyle = (bar as HTMLElement).style.width;
    // Either "100%" or stripped to "100%" — accept either.
    expect(widthStyle).toMatch(/^100/);
  });

  it("uses warning bar fill above 75%", () => {
    r(160_000, 200_000);
    const bar = screen.getByRole("status").querySelector("[style*='width']");
    expect(bar?.className).toMatch(/amber/);
  });

  it("uses critical bar fill above 90%", () => {
    r(190_000, 200_000);
    const bar = screen.getByRole("status").querySelector("[style*='width']");
    expect(bar?.className).toMatch(/destructive/);
  });
});
