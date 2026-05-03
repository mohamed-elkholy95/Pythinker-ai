import { describe, expect, it } from "vitest";
import { formatRelativeTime } from "@/lib/formatRelativeTime";

const NOW = new Date("2026-04-26T15:00:00Z").getTime();

describe("formatRelativeTime", () => {
  it("returns 'just now' under 60s", () => {
    expect(formatRelativeTime(NOW - 12_000, NOW, "en")).toBe("just now");
  });
  it("returns minutes when under 60m", () => {
    expect(formatRelativeTime(NOW - 5 * 60_000, NOW, "en")).toMatch(/5\s*min(ute)?s?\s*ago/i);
  });
  it("returns hours when under 24h", () => {
    expect(formatRelativeTime(NOW - 3 * 60 * 60_000, NOW, "en")).toMatch(/3\s*hour?s?\s*ago/i);
  });
  it("returns absolute date for >=24h", () => {
    const result = formatRelativeTime(NOW - 26 * 60 * 60_000, NOW, "en");
    // Should look like "Apr 25, 13:00" — exact format depends on locale,
    // but absolute output never contains "ago".
    expect(result).not.toMatch(/ago/i);
    expect(result).toMatch(/\d/);
  });
});
