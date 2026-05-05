"""Per-step modules for the onboarding wizard.

Each step is a single ``_step_*`` function that takes a ``_WizardContext``
and returns a ``StepResult``. The driver in ``pythinker.cli.onboard``
imports them and registers them in ``_WIZARD_STEPS`` (registration order
mirrors the wizard's user-visible flow).

Step modules import shared globals (``console``, ``httpx``,
``get_config_path``, ``save_config``, etc.) through
``pythinker.cli.onboard`` so test patches at that dotted path keep
working — the lazy ``from pythinker.cli import onboard as _onboard``
inside each step body resolves the patched attribute, not this file's
local symbol.
"""

from __future__ import annotations
