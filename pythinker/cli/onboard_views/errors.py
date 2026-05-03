"""Actionable error renderer — three-line ``What / Why / How`` panels.

Mirrors pythinker's onboard error pattern (see ``infra/errors.ts``): every
user-visible failure carries:

  - **What** went wrong, in one line, blame-free.
  - **Why** it matters — what the failure prevents.
  - **How** to fix it — a concrete next step the user can act on now.

Replaces the bare ``traceback.format_exc`` calls that used to surface
deep internals to first-time users. Internal stack traces still go to
``loguru`` so support requests retain the diagnostic detail.
"""

from __future__ import annotations

from pythinker.cli.onboard_views import clack


def render_actionable(*, what: str, why: str, how: str) -> None:
    """Render a three-line actionable error panel.

    All three fields are required and one-line. The renderer is style-only;
    callers handle the control-flow side (returning abort, exiting, etc.)
    so this helper stays decoupled from the orchestrator.
    """
    body = [
        f"What:  {what}",
        f"Why:   {why}",
        f"How:   {how}",
    ]
    clack.note("Error", body)
    clack.bar_break()
