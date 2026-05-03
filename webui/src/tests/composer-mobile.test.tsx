import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ThreadComposer } from "@/components/thread/ThreadComposer";

/**
 * Phase 5 / Task 7 — mobile + iOS polish acceptance tests.
 *
 * Concrete iOS issues guarded here:
 * 1. Textareas with `font-size < 16px` trigger zoom-on-focus on iOS Safari.
 *    The thread variant must therefore include a 16px-or-larger token (we
 *    use `text-base` on touch and downshift to `text-sm` at `sm:` and above).
 * 2. Apple HIG / WCAG 2.5.5 require tap targets ≥44×44px. The paperclip
 *    button must therefore expose `h-11 w-11` (44px) at touch breakpoints.
 *    The hero variant is desktop-first, so we only assert on the thread
 *    variant where mobile users actually compose.
 */
describe("composer mobile/iOS pass", () => {
  it("textarea has at least 16px effective font-size at touch breakpoint to prevent iOS zoom", () => {
    const { container } = render(
      <ThreadComposer onSend={vi.fn()} placeholder="msg" variant="thread" />,
    );
    const textarea = container.querySelector("textarea");
    expect(textarea).toBeTruthy();
    const cls = textarea!.className;
    // Must include a base/16px-or-larger token; downshifting at `sm:` is fine.
    expect(cls).toMatch(/(?:^|\s)text-base(?:\s|$)|text-\[1[6-9]px\]|text-lg/);
  });

  it("paperclip button is at least 44x44px on touch (h-11 w-11 or larger)", () => {
    const { container } = render(
      <ThreadComposer onSend={vi.fn()} placeholder="msg" variant="thread" />,
    );
    const attach = container.querySelector(
      'button[aria-label*="Attach" i], button[aria-label*="image" i]',
    );
    expect(attach).toBeTruthy();
    const cls = attach!.className;
    // Either always-large, or breakpoint-gated to large on touch.
    expect(cls).toMatch(/(?:^|\s)h-11(?:\s|$)|min-h-\[44px\]|(?:^|\s)h-12(?:\s|$)/);
    expect(cls).toMatch(/(?:^|\s)w-11(?:\s|$)|min-w-\[44px\]|(?:^|\s)w-12(?:\s|$)/);
  });
});
