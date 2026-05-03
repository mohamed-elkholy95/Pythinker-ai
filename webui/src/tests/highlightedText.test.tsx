import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HighlightedText } from "@/components/HighlightedText";

describe("HighlightedText", () => {
  it("returns the text unchanged when query is empty", () => {
    render(<HighlightedText text="hello world" query="" idPrefix="m1" />);
    expect(screen.getByText("hello world")).toBeInTheDocument();
    expect(document.querySelectorAll("mark").length).toBe(0);
  });

  it("wraps a single substring match in a <mark>", () => {
    render(<HighlightedText text="hello world" query="world" idPrefix="m1" />);
    const marks = document.querySelectorAll("mark");
    expect(marks.length).toBe(1);
    expect(marks[0].textContent).toBe("world");
  });

  it("matches case-insensitively", () => {
    render(<HighlightedText text="Hello World" query="world" idPrefix="m1" />);
    const marks = document.querySelectorAll("mark");
    expect(marks.length).toBe(1);
    expect(marks[0].textContent).toBe("World"); // preserves original casing
  });

  it("wraps every occurrence in a separate <mark>", () => {
    render(<HighlightedText text="ab ab ab" query="ab" idPrefix="m1" />);
    const marks = document.querySelectorAll("mark");
    expect(marks.length).toBe(3);
    expect(marks[0].getAttribute("data-match-id")).toBe("m1:0");
    expect(marks[2].getAttribute("data-match-id")).toBe("m1:2");
  });

  it("flags the active match with a stronger style", () => {
    render(
      <HighlightedText
        text="ab ab"
        query="ab"
        idPrefix="m1"
        activeMatchId="m1:1"
      />,
    );
    const marks = document.querySelectorAll("mark");
    expect(marks[0].className).not.toMatch(/active/);
    expect(marks[1].className).toMatch(/active/);
  });

  it("renders nothing weird when text contains regex metacharacters", () => {
    render(<HighlightedText text="a.b+c" query=".+" idPrefix="m1" />);
    // Substring match on the literal sequence ".+" doesn't appear, so 0 marks.
    expect(document.querySelectorAll("mark").length).toBe(0);
  });
});
