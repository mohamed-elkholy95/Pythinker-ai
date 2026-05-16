"""Shared prompt-toolkit styles for onboarding prompts."""

from __future__ import annotations

from prompt_toolkit.styles import Style

# Match the green active-row treatment used by the settings field editor.
ONBOARD_GREEN_HOVER = "fg:ansigreen bold noinherit"
ONBOARD_CYAN_QUESTION = "fg:#9277c4"

ONBOARD_QUESTIONARY_STYLE = Style.from_dict(
    {
        "qmark": ONBOARD_CYAN_QUESTION,
        "question": ONBOARD_CYAN_QUESTION,
        "pointer": ONBOARD_GREEN_HOVER,
        "highlighted": ONBOARD_GREEN_HOVER,
        "selected": ONBOARD_GREEN_HOVER,
        "answer": ONBOARD_GREEN_HOVER,
        "instruction": "fg:#888888",
    }
)

ONBOARD_SELECT_WITH_BACK_STYLE = Style.from_dict(
    {
        "cursor-row": ONBOARD_GREEN_HOVER,
        "hint": "fg:#888888",
        "question": ONBOARD_CYAN_QUESTION,
    }
)
