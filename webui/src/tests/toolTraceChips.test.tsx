import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { I18nextProvider } from "react-i18next";

import { ToolTraceChips } from "@/components/ToolTraceChips";
import i18n from "@/i18n";

function r(traces: string[]) {
  return render(
    <I18nextProvider i18n={i18n}>
      <ToolTraceChips traces={traces} />
    </I18nextProvider>,
  );
}

describe("ToolTraceChips", () => {
  it("renders one chip per unique tool verb (real hint format)", () => {
    r(["read /tmp/foo.py", "write /tmp/bar.py", 'search "rust"']);
    expect(screen.getByText("read")).toBeInTheDocument();
    expect(screen.getByText("write")).toBeInTheDocument();
    expect(screen.getByText("search")).toBeInTheDocument();
  });

  it("splits comma-joined multi-tool hint lines", () => {
    r(["read /tmp/foo, write /tmp/bar"]);
    expect(screen.getByText("read")).toBeInTheDocument();
    expect(screen.getByText("write")).toBeInTheDocument();
  });

  it("treats $-prefixed shell commands as 'exec'", () => {
    r(["$ ls -la", "$ pwd"]);
    expect(screen.getByText("exec")).toBeInTheDocument();
    expect(screen.getByText(/×2/)).toBeInTheDocument();
  });

  it("respects the backend × N repeat suffix when summing counts", () => {
    r(["read /tmp/foo × 3"]);
    expect(screen.getByText("read")).toBeInTheDocument();
    expect(screen.getByText(/×3/)).toBeInTheDocument();
  });

  it("preserves MCP server::tool namespacing", () => {
    r(['github::issues("123")']);
    expect(screen.getByText("github::issues")).toBeInTheDocument();
  });

  it("renders nothing when traces is empty", () => {
    const { container } = r([]);
    expect(container.firstChild).toBeNull();
  });

  it("expands to full trace lines when toggled", () => {
    r(["read /tmp/foo.py"]);
    const expandBtn = screen.getByRole("button", {
      name: /show tool details/i,
    });
    expect(screen.queryByText("read /tmp/foo.py")).toBeNull();
    fireEvent.click(expandBtn);
    expect(screen.getByText("read /tmp/foo.py")).toBeInTheDocument();
  });

  it("uses the fallback label when an atom is empty", () => {
    r([",,,"]);
    expect(screen.getByText(/^tool$/i)).toBeInTheDocument();
  });
});
