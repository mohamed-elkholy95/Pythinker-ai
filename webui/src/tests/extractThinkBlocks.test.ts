import { describe, expect, it } from "vitest";
import { extractThinkBlocks } from "@/lib/extractThinkBlocks";

describe("extractThinkBlocks", () => {
  it("returns input unchanged when no <think> tags present", () => {
    const r = extractThinkBlocks("Here is the answer.");
    expect(r.reasoning).toBe("");
    expect(r.visible).toBe("Here is the answer.");
  });

  it("extracts a single block and strips it from visible", () => {
    const input = "<think>I should add 2+2.</think>The answer is 4.";
    const r = extractThinkBlocks(input);
    expect(r.reasoning).toBe("I should add 2+2.");
    expect(r.visible.trim()).toBe("The answer is 4.");
  });

  it("concatenates multiple blocks with double newlines", () => {
    const input = "<think>step1</think>some text<think>step2</think>final";
    const r = extractThinkBlocks(input);
    expect(r.reasoning).toBe("step1\n\nstep2");
    expect(r.visible.replace(/\s+/g, " ").trim()).toBe("some text final");
  });

  it("handles multi-line think content (no `s` flag, uses [\\s\\S])", () => {
    const input = "<think>line 1\nline 2\nline 3</think>OK";
    const r = extractThinkBlocks(input);
    expect(r.reasoning).toBe("line 1\nline 2\nline 3");
    expect(r.visible.trim()).toBe("OK");
  });

  it("ignores an unclosed <think> tag (treat as visible)", () => {
    const input = "<think>oops never closed";
    const r = extractThinkBlocks(input);
    expect(r.reasoning).toBe("");
    expect(r.visible).toBe(input);
  });
});
