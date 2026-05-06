"""Multi-agent picker step for the onboarding wizard.

Phase 2 PR-3 of `.agents/plans/2026-05-05-onboard-phase-2-multi-agent.md`.

Runs only when ``~/.pythinker/agents/`` already exists. On a single-config
install (no ``agents/`` dir) the step short-circuits to ``skip`` so the
legacy onboarding path is byte-identical to today's flow.

Three options when ``agents/`` exists:

  * ``Use <current>`` — accept the resolved active agent and continue.
  * ``Pick a different agent`` — fuzzy-pick from the existing per-agent dirs.
  * ``Create a new agent`` — prompt for an id, scaffold the dir + workspace.

The chosen id flows into ``_WizardContext.agent_id`` and is also set as
the loader's config-path override via ``set_config_path()`` so every
downstream step reads / writes the correct ``config.json``.
"""

from __future__ import annotations

from pathlib import Path

from pythinker.cli.onboard_types import StepResult, _WizardContext
from pythinker.config.loader import set_config_path
from pythinker.config.paths import agent_config_path, agent_dir, current_agent_id


def _agents_root() -> Path:
    return Path.home() / ".pythinker" / "agents"


def _list_existing_agents() -> list[str]:
    root = _agents_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


_USE_CURRENT = "__use_current__"
_PICK = "__pick__"
_CREATE = "__create__"


def _step_agent_id(ctx: _WizardContext) -> StepResult:
    """Pick or create the agent whose config the wizard will edit."""
    from pythinker.cli.onboard_views import clack

    existing = _list_existing_agents()
    if not existing:
        # Single-config install — no behavior change. Keep ctx.agent_id None
        # so summary panels keep rendering the legacy path.
        return StepResult(status="skip")

    active = current_agent_id()

    if ctx.non_interactive:
        # Honour the env-var / marker resolution silently.
        ctx.agent_id = active
        if active in existing:
            set_config_path(agent_config_path(active))
        return StepResult(status="continue")

    options: list[tuple[str, str, str]] = [
        (_USE_CURRENT, f"Use current agent: {active}", "the resolved active agent"),
        (_PICK, "Pick a different agent", f"{len(existing)} existing"),
        (_CREATE, "Create a new agent", "scaffold a new ~/.pythinker/agents/<id>/"),
    ]
    pick = clack.select(
        "Which agent are you onboarding?",
        options=options,
        default=_USE_CURRENT,
    )

    if pick == _USE_CURRENT:
        chosen = active
    elif pick == _PICK:
        agent_options = [(name, name, "") for name in existing]
        chosen = clack.select(
            "Pick an agent:",
            options=agent_options,
            default=active if active in existing else existing[0],
        )
    elif pick == _CREATE:
        entered = clack.text("New agent id:", default="")
        chosen = (entered or "").strip()
        if not chosen or "/" in chosen or "\\" in chosen or chosen in {".", ".."}:
            return StepResult(
                status="back",
                message=f"Invalid agent id: {chosen!r}",
            )
        target = agent_dir(chosen)
        if not target.exists():
            target.mkdir(parents=True)
            (target / "workspace").mkdir(exist_ok=True)
            (target / "config.json").write_text("{}\n", encoding="utf-8")
    else:
        # Defensive: cancelled/unknown picker outcome → bail to back-nav so
        # the user is not silently committed to a stale ctx.agent_id.
        return StepResult(status="back")

    ctx.agent_id = chosen
    set_config_path(agent_config_path(chosen))
    return StepResult(status="continue")
