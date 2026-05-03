"""Step 3 — security disclaimer + acknowledgement.

Renders a clack `note` panel with the disclaimer text, then prompts the user
to confirm. `--yes-security` skips the confirm. `--non-interactive` without
`--yes-security` exits 1 with a hint.
"""

from __future__ import annotations

import sys

from pythinker.cli.onboard_views import clack
from pythinker.cli.onboard_views.panels import SECURITY_DISCLAIMER


def show_and_confirm(*, yes_security: bool, non_interactive: bool) -> bool:
    """Render the disclaimer panel and prompt for acknowledgement.

    Returns True if user accepts; False if they decline.
    Calls sys.exit(1) for non-interactive without --yes-security.
    """
    clack.note("Security disclaimer", SECURITY_DISCLAIMER)
    clack.bar_break()

    if yes_security:
        return True

    if non_interactive:
        sys.stderr.write("pythinker onboard --non-interactive requires --yes-security\n")
        sys.exit(1)

    # Use a select-style Yes/No instead of clack.confirm so the prompt
    # renders with the wizard's standard ● / ○ option-dot idiom (matches
    # every other interactive step) rather than questionary's bare "(y/N)"
    # text widget. Defaults to "no" — same conservative default as before.
    chosen = clack.select(
        "I understand this is personal-by-default and shared/multi-user "
        "use requires lock-down. Continue?",
        options=[
            ("yes", "Yes", ""),
            ("no", "No", ""),
        ],
        default="no",
    )
    return chosen == "yes"
