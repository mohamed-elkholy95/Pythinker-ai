"""Result + context types used across the onboarding wizard.

Split out of ``pythinker/cli/onboard.py`` so step modules under
``pythinker/cli/onboard_steps/`` can import them without dragging the
whole driver module along. ``onboard.py`` re-exports each of these names
for backwards compatibility — tests still patch and import them via
``pythinker.cli.onboard``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

from pythinker.config.schema import Config


@dataclass
class OnboardResult:
    """Result of an onboarding session."""

    config: Config
    should_save: bool


@dataclass(frozen=True)
class StepResult:
    """Result of a single wizard step.

    ``status`` values:

    - ``"continue"`` — proceed to the next step (or to ``next_step`` if set).
    - ``"skip"`` — same as continue; semantic marker that the step had nothing
      to do (e.g. step gated on QuickStart but flow is Manual).
    - ``"abort"`` — stop the wizard, do not save. ``message`` is shown to the
      user via ``clack.abort``.
    - ``"back"`` — request the orchestrator to re-run the previous step.
      Used by Task 5 (back-navigation across steps); the Task 1 orchestrator
      treats this the same as ``"continue"`` so steps can start emitting it
      ahead of the orchestrator support landing.

    ``next_step`` is reserved for the back-nav work — when non-empty, the
    orchestrator jumps to the named step instead of the default next-in-list.
    Today it's accepted but unused.
    """

    status: Literal["continue", "skip", "abort", "back"]
    message: str = ""
    next_step: str = ""


@dataclass
class _WizardContext:
    """Shared state threaded through every wizard step.

    Field groupings (kept stable so phases 2–4 can extend without churn):

    - **flow control**: ``flow``, ``non_interactive``, ``yes_security``,
      ``use_existing``, ``use_existing_committed``, ``started_from_existing``.
    - **provider/auth choice**: ``auth``, ``auth_method``.
    - **runtime intents**: ``start_gateway``, ``skip_gateway``,
      ``gateway_started``, ``open_webui``.
    - **reset / migration**: ``reset_pending``, ``reset_scope``.
    - **path overrides**: ``workspace_override``.
    - **deferred side-effects** (``deferred``): callables a step registers to
      run once the orchestrator finishes successfully — e.g. opening a
      browser tab after the gateway is up. Drained by ``_run_linear_wizard``
      after the last step returns; exceptions are logged, not raised.

    Carries the linear-wizard state across steps at the data-shape level.
    """

    draft: Config
    flow: str = "manual"
    non_interactive: bool = False
    yes_security: bool = False
    auth: str | None = None
    auth_method: str | None = None
    start_gateway: bool | None = None
    skip_gateway: bool = False
    started_from_existing: bool = False
    use_existing: bool = False
    use_existing_committed: bool = False
    reset_pending: bool = False
    reset_scope: object | None = None
    workspace_override: str | None = None
    open_webui: bool = False
    gateway_started: bool = False
    # Multi-agent layout (`.agents/plans/2026-05-05-onboard-phase-2-multi-agent.md`).
    # ``None`` keeps the legacy single-config behavior; set by ``_step_agent_id``
    # when the user is on a host that already has ``~/.pythinker/agents/``. The
    # step also calls ``set_config_path()`` so the rest of the wizard reads /
    # writes the chosen agent's ``config.json``.
    agent_id: str | None = None
    deferred: list[Callable[[], None]] = field(default_factory=list)

    def register_deferred(self, fn: Callable[[], None]) -> None:
        """Register a fire-and-forget callback to run after the orchestrator
        finishes successfully (i.e. no abort, no exception). Used by steps
        that need work to land *after* a later step — e.g. opening the WebUI
        browser tab once the gateway has bound its port. Order is registration
        order; an exception in one deferred does not stop the others."""
        self.deferred.append(fn)
