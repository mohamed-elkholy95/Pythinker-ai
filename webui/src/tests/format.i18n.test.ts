import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAppLanguage } from "@/i18n";
import { fmtDateTime, relativeTime } from "@/lib/format";

describe("localized format helpers", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("formats relative time using the active locale", async () => {
    const value = "2026-04-18T11:59:00Z";

    await setAppLanguage("en");

    expect(relativeTime(value)).toBe(
      new Intl.RelativeTimeFormat("en", { numeric: "auto" }).format(
        -1,
        "minute",
      ),
    );
  });

  it("formats date-time using the active locale", async () => {
    const value = "2026-04-18T08:30:00Z";
    const date = new Date(value);

    await setAppLanguage("en");

    expect(fmtDateTime(value)).toBe(
      new Intl.DateTimeFormat("en", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date),
    );
  });
});
