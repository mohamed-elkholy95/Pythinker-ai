"""Interactive onboarding questionnaire for pythinker."""

from __future__ import annotations

import asyncio
import json
import os
import types
import webbrowser
from functools import lru_cache
from typing import Any, Callable, Literal, NamedTuple, get_args, get_origin

import httpx

try:
    import questionary
except ModuleNotFoundError:  # pragma: no cover - exercised in environments without wizard deps
    questionary = None
from loguru import logger
from pydantic import BaseModel
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pythinker.agent.tools.web import WebSearchTool
from pythinker.cli.models import (
    format_token_count,
    get_model_context_limit,
    get_model_suggestions,
)

# Compatibility re-exports: tests patch and import the wizard's result types
# off ``pythinker.cli.onboard``. The dataclasses live in ``onboard_types``
# now; keep the names importable from this module.
from pythinker.cli.onboard_auth import (  # noqa: F401
    _login_via_oauth_remote,
    _set_provider_api_key,
)
from pythinker.cli.onboard_options import (  # noqa: F401
    _build_provider_options,
    _format_provider_hint,
    _model_belongs_to_provider,
    _normalize_provider_id,
    _provider_picker_bucket,
    _resolve_model_route_hint,
)
from pythinker.cli.onboard_types import (  # noqa: F401
    OnboardResult,
    StepResult,
    _WizardContext,
)
from pythinker.config.loader import (
    get_config_path,
    load_config,
    save_config,  # noqa: F401  # patched on this module by tests
)
from pythinker.config.schema import (
    Config,
    WebSearchConfig,
    WebSearchProviderConfig,
)

console = Console()


_WIZARD_STEPS: list[Callable[[_WizardContext], StepResult]] = []


# Per-step documentation pointers. Each step that calls ``_emit_docs_link``
# uses one of these keys to print a dim "Docs: …" footer after the main
# prompt — every section ends with a docs URL the user can paste into a
# browser.
_DOCS_BASE = "https://github.com/mohamed-elkholy95/pythinker/blob/main/docs"
_DOCS_LINKS: dict[str, str] = {
    "provider": f"{_DOCS_BASE}/configuration.md#providers",
    "channels": f"{_DOCS_BASE}/chat-apps.md",
    "search": f"{_DOCS_BASE}/configuration.md#search",
    "model": f"{_DOCS_BASE}/configuration.md#models",
    "workspace": f"{_DOCS_BASE}/configuration.md#workspace",
    "security": f"{_DOCS_BASE}/security.md",
}


def _emit_docs_link(key: str) -> None:
    """Print a one-line dim ``Docs: <url>`` footer for the given step key.
    Silently no-ops when the key isn't in ``_DOCS_LINKS`` (the calling step
    is then untouched — failing closed is fine for a UX accessory)."""
    from pythinker.cli.onboard_views import clack

    url = _DOCS_LINKS.get(key)
    if url:
        clack.print_status(f"Docs: {url}")


def _run_linear_wizard(ctx: _WizardContext) -> OnboardResult:
    """Run each registered step. Supports back-navigation.

    Walker semantics (mirrors pythinker's wizard session walker):

    - ``continue`` — push current step onto the history stack, advance.
    - ``skip``     — advance without pushing (a skipped step has nothing
                     to "go back to").
    - ``back``     — pop the history stack and re-run that step. When the
                     stack is empty we are at the beginning, so back is a
                     no-op (the banner step then advances normally).
    - ``next_step`` — explicit jump by step function name. Pushes the
                     current step onto history first so a subsequent
                     ``back`` returns here.
    - ``abort`` — close the wizard, do not save.

    Caught exceptions:
      - ``clack.WizardCancelled`` (Ctrl-C/Esc inside a prompt) → abort cleanly.
      - Any other ``Exception`` → log and abort. Draft is never saved.
    """
    from pythinker.cli.onboard_views import clack

    history: list[int] = []
    idx = 0
    n = len(_WIZARD_STEPS)
    while idx < n:
        step = _WIZARD_STEPS[idx]
        try:
            result = step(ctx)
        except clack.WizardCancelled:
            clack.abort(f"cancelled at {step.__name__}")
            return OnboardResult(config=ctx.draft, should_save=False)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Onboard step {} failed", step.__name__)
            clack.abort(f"{step.__name__} failed: {exc}")
            return OnboardResult(config=ctx.draft, should_save=False)

        if result.status == "abort":
            clack.abort(result.message or "user aborted")
            return OnboardResult(config=ctx.draft, should_save=False)

        if result.status == "back":
            if history:
                idx = history.pop()
                continue
            # At the very first step — back has nowhere to go. Re-run the
            # step instead of advancing so the user can re-make the choice.
            continue

        if result.next_step:
            target_idx = next(
                (i for i, s in enumerate(_WIZARD_STEPS) if s.__name__ == result.next_step),
                None,
            )
            if target_idx is not None:
                history.append(idx)
                idx = target_idx
                continue

        # continue / skip both advance; only continue records history.
        if result.status == "continue":
            history.append(idx)
        idx += 1

    # Drain any side-effects registered via ``ctx.register_deferred``. These
    # run for both should_save=True and use_existing paths since both finish
    # the wizard successfully.
    for fn in ctx.deferred:
        try:
            fn()
        except Exception:  # noqa: BLE001
            logger.exception("Deferred onboard side-effect failed")

    if ctx.use_existing:
        return OnboardResult(config=ctx.draft, should_save=False)

    return OnboardResult(config=ctx.draft, should_save=True)


# --- Banner + Intro + Outro Steps ---
# Step bodies live in ``onboard_steps.banner``; we re-export them so tests
# importing them off ``pythinker.cli.onboard`` keep working.
from pythinker.cli.onboard_steps.banner import (  # noqa: F401, E402
    _step_banner,
    _step_intro,
    _step_outro,
)

_WIZARD_STEPS.extend([_step_banner, _step_intro])
# _step_outro and _step_start_gateway are registered together at the
# bottom of the file: outro first (its message must survive the exec),
# then start_gateway which replaces the wizard process when accepted.


# --- Step 2.5: Agent-id picker (only renders when ~/.pythinker/agents/ exists) ---
# Phase 2 PR-3. On a single-config install (no agents/ dir) the step short-
# circuits to ``skip`` so the rest of the wizard is byte-identical to today.
from pythinker.cli.onboard_steps.agent_id import _step_agent_id  # noqa: F401, E402

_WIZARD_STEPS.append(_step_agent_id)


# --- Step 3: Security Disclaimer ---
from pythinker.cli.onboard_steps.security_disclaimer import (  # noqa: F401, E402
    _step_security_disclaimer,
)

_WIZARD_STEPS.append(_step_security_disclaimer)


# --- Step 4: Existing Config Detection ---
from pythinker.cli.onboard_steps.existing_config import (  # noqa: F401, E402
    _step_existing_config,
)

_WIZARD_STEPS.append(_step_existing_config)


# --- Step 5: Flow Picker (QuickStart vs Manual) + Step 6: QuickStart summary ---
from pythinker.cli.onboard_steps.flow_picker import (  # noqa: F401, E402
    _step_flow_picker,
    _step_quickstart_summary,
)

_WIZARD_STEPS.append(_step_flow_picker)
_WIZARD_STEPS.append(_step_quickstart_summary)


# --- Step 7: Provider Picker, Step 8: Auth Method Picker, Step 9: Run Auth ---
from pythinker.cli.onboard_steps.provider_picker import (  # noqa: F401, E402
    _step_auth_method_picker,
    _step_provider_picker,
    _step_run_auth,
)

_WIZARD_STEPS.append(_step_provider_picker)
_WIZARD_STEPS.append(_step_auth_method_picker)
_WIZARD_STEPS.append(_step_run_auth)


# --- Step 10: Default Model Picker ---


_KEEP_KEY = "__keep__"
_MANUAL_KEY = "__manual__"
_BACK_KEY = "__back__"


# --- Step 10: Default model picker, Step 11: Workspace ---
from pythinker.cli.onboard_steps.default_model import (  # noqa: F401, E402
    _step_default_model,
    _step_workspace,
)

_WIZARD_STEPS.append(_step_default_model)
_WIZARD_STEPS.append(_step_workspace)


def _channel_is_enabled(cfg: Config, channel_name: str) -> bool:
    """True if ``cfg.channels.<channel_name>`` exists and has ``enabled=True``."""
    ch = getattr(cfg.channels, channel_name, None)
    if ch is None:
        return False
    if isinstance(ch, dict):
        return bool(ch.get("enabled", False))
    return bool(getattr(ch, "enabled", False))


def _set_channel_enabled(cfg: Config, channel_name: str, enabled: bool) -> None:
    """Set ``cfg.channels.<channel_name>.enabled`` in-place, working for both
    dict-shaped and pydantic-model-shaped channel sub-configs."""
    ch = getattr(cfg.channels, channel_name, None)
    if ch is None:
        return
    if isinstance(ch, dict):
        ch["enabled"] = enabled
    else:
        try:
            setattr(ch, "enabled", enabled)
        except (AttributeError, TypeError):
            # Frozen / unsupported model — caller's job to surface this elsewhere.
            pass


def _prompt_configured_action(
    label: str,
    *,
    supports_disable: bool = True,
    update_text: str = "Modify settings",
    disable_text: str = "Disable (keeps config)",
    skip_text: str = "Skip (leave as-is)",
) -> str:
    """Action picker for an already-configured target.

    Used by every onboard step that may re-encounter an already-configured
    target — channels, providers, search backends, workspace dirs — so the
    user is never silently dropped back into the per-field editor when they
    might have only wanted to disable or skip.

    Returns one of: ``"update"`` (re-run the editor / reconfigure), ``"disable"``
    (turn the feature off but keep its config — only when ``supports_disable``
    is True), ``"skip"`` (no-op, leave as-is). The ``delete`` action pythinker
    exposes is intentionally omitted in pythinker — clearing a pydantic
    sub-block can't reliably round-trip without losing schema-resident
    defaults, so we leave clearing to the editor itself.
    """
    from pythinker.cli.onboard_views import clack

    options: list[tuple[str, str, str]] = [("update", update_text, "")]
    if supports_disable:
        options.append(("disable", disable_text, ""))
    options.append(("skip", skip_text, ""))

    return clack.select(
        f"{label} already configured. What do you want to do?",
        options=options,
        default="update",
    )


def _prompt_configured_channel_action(label: str) -> str:
    """Channel-specific shim around :func:`_prompt_configured_action`. Retained
    so existing tests and call sites continue to work unchanged."""
    return _prompt_configured_action(label, supports_disable=True)


# --- Step 12: Channels picker, Step 13: Search provider picker ---
from pythinker.cli.onboard_steps.channels import _step_channels  # noqa: F401, E402
from pythinker.cli.onboard_steps.search_provider import (  # noqa: F401, E402
    _step_search_provider,
)

_WIZARD_STEPS.append(_step_channels)
_WIZARD_STEPS.append(_step_search_provider)


# --- Step 14: Summary + save, Step 15: Post-save health, Step 16: Start gateway ---
from pythinker.cli.onboard_steps.post_save_health import (  # noqa: F401, E402
    _check_gateway_port_free,
    _step_post_save_health,
)
from pythinker.cli.onboard_steps.start_gateway import (  # noqa: F401, E402
    _step_start_gateway,
)
from pythinker.cli.onboard_steps.summary_confirm import (  # noqa: F401, E402
    _step_summary_confirm,
)

_WIZARD_STEPS.append(_step_summary_confirm)
_WIZARD_STEPS.append(_step_post_save_health)
# Outro before start_gateway: start_gateway execs into the gateway and never
# returns, so any post-exec step (including the outro's next-steps message)
# would silently be dropped if the order were reversed.
_WIZARD_STEPS.extend([_step_outro, _step_start_gateway])


# --- Field Hints for Select Fields ---
# Maps field names to (choices, hint_text)
# To add a new select field with hints, add an entry:
#   "field_name": (["choice1", "choice2", ...], "hint text for the field")
_SELECT_FIELD_HINTS: dict[str, tuple[list[str], str]] = {
    "reasoning_effort": (
        ["low", "medium", "high"],
        "low / medium / high - enables LLM thinking mode",
    ),
}

# --- Key Bindings for Navigation ---

_BACK_PRESSED = object()  # Sentinel value for back navigation


def _get_questionary():
    """Return questionary or raise a clear error when wizard deps are unavailable."""
    if questionary is None:
        raise RuntimeError(
            "Interactive onboarding requires the optional 'questionary' dependency. "
            "Install project dependencies and rerun with 'pythinker onboard'."
        )
    return questionary


def _select_with_back(
    prompt: str, choices: list[str], default: str | None = None
) -> str | None | object:
    """Select with Escape/Left arrow support for going back.

    Args:
        prompt: The prompt text to display.
        choices: List of choices to select from. Must not be empty.
        default: The default choice to pre-select. If not in choices, first item is used.

    Returns:
        _BACK_PRESSED sentinel if user pressed Escape or Left arrow
        The selected choice string if user confirmed
        None if user cancelled (Ctrl+C)
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    # Validate choices
    if not choices:
        logger.warning("Empty choices list provided to _select_with_back")
        return None

    # Find default index
    selected_index = 0
    if default and default in choices:
        selected_index = choices.index(default)

    # State holder for the result
    state: dict[str, str | None | object] = {"result": None}

    # Build menu items (uses closure over selected_index)
    def get_menu_text():
        items = []
        for i, choice in enumerate(choices):
            if i == selected_index:
                # Wrap the active row with connected dashes (┄) so it reads as
                # a hollow striped highlight instead of a filled background.
                items.append(("class:cursor-row", f"┄ {choice} ┄┄┄\n"))
            else:
                items.append(("", f"  {choice}\n"))
        return items

    # Create layout
    menu_control = FormattedTextControl(get_menu_text)
    menu_window = Window(content=menu_control, height=len(choices))

    prompt_control = FormattedTextControl(lambda: [("class:question", f"◆  {prompt}")])
    # always_hide_cursor: prompt_toolkit otherwise parks its block cursor on the
    # first cell of the focused control, which lands on top of the ● glyph and
    # reads as a hover/highlight artefact.
    prompt_window = Window(content=prompt_control, height=1, always_hide_cursor=True)

    layout = Layout(HSplit([prompt_window, menu_window]))

    # Key bindings
    bindings = KeyBindings()

    @bindings.add(Keys.Up)
    def _up(event):
        nonlocal selected_index
        selected_index = (selected_index - 1) % len(choices)
        event.app.invalidate()

    @bindings.add(Keys.Down)
    def _down(event):
        nonlocal selected_index
        selected_index = (selected_index + 1) % len(choices)
        event.app.invalidate()

    @bindings.add(Keys.Enter)
    def _enter(event):
        state["result"] = choices[selected_index]
        event.app.exit()

    @bindings.add("escape")
    def _escape(event):
        state["result"] = _BACK_PRESSED
        event.app.exit()

    @bindings.add(Keys.Left)
    def _left(event):
        state["result"] = _BACK_PRESSED
        event.app.exit()

    @bindings.add(Keys.ControlC)
    def _ctrl_c(event):
        state["result"] = None
        event.app.exit()

    # Style. Use a custom class name (not "selected") because prompt_toolkit's
    # built-in `selected` class adds reverse-video, which renders as the green
    # filled box. `noinherit` makes sure no parent rule re-introduces a bg.
    style = Style.from_dict({
        "cursor-row": "fg:ansigreen bold noinherit",
        "question": "fg:cyan",
    })

    app = Application(layout=layout, key_bindings=bindings, style=style)
    try:
        app.run()
    except Exception:
        logger.exception("Error in select prompt")
        return None

    return state["result"]

# --- Type Introspection ---


class FieldTypeInfo(NamedTuple):
    """Result of field type introspection."""

    type_name: str
    inner_type: Any


def _get_field_type_info(field_info) -> FieldTypeInfo:
    """Extract field type info from Pydantic field."""
    annotation = field_info.annotation
    if annotation is None:
        return FieldTypeInfo("str", None)

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is types.UnionType:
        non_none_args = [a for a in args if a is not type(None)]
        if len(non_none_args) == 1:
            annotation = non_none_args[0]
            origin = get_origin(annotation)
            args = get_args(annotation)

    simple_types: dict[type, str] = {bool: "bool", int: "int", float: "float"}

    if origin is list or (hasattr(origin, "__name__") and origin.__name__ == "List"):
        return FieldTypeInfo("list", args[0] if args else str)
    if origin is dict or (hasattr(origin, "__name__") and origin.__name__ == "Dict"):
        return FieldTypeInfo("dict", None)
    for py_type, name in simple_types.items():
        if annotation is py_type:
            return FieldTypeInfo(name, None)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return FieldTypeInfo("model", annotation)
    if origin is Literal:
        return FieldTypeInfo("literal", list(args))
    return FieldTypeInfo("str", None)


def _get_field_display_name(field_key: str, field_info) -> str:
    """Get display name for a field."""
    if field_info and field_info.description:
        return field_info.description
    name = field_key
    suffix_map = {
        "_s": " (seconds)",
        "_ms": " (ms)",
        "_url": " URL",
        "_path": " Path",
        "_id": " ID",
        "_key": " Key",
        "_token": " Token",
    }
    for suffix, replacement in suffix_map.items():
        if name.endswith(suffix):
            name = name[: -len(suffix)] + replacement
            break
    return name.replace("_", " ").title()


# --- Sensitive Field Masking ---

_SENSITIVE_KEYWORDS = frozenset({"api_key", "token", "secret", "password", "credentials"})


def _is_sensitive_field(field_name: str) -> bool:
    """Check if a field name indicates sensitive content."""
    return any(kw in field_name.lower() for kw in _SENSITIVE_KEYWORDS)


def _mask_value(value: str) -> str:
    """Mask a sensitive value, showing only the last 4 characters."""
    if len(value) <= 4:
        return "****"
    return "*" * (len(value) - 4) + value[-4:]


def _mask_token(value: str | None) -> str:
    """Mask an API token / key for display. Don't leak short values.

    Long enough (≥ 12 chars) → first 4 + '...' + last 4.
    Anything shorter (or None / empty) → '***'.
    Used by the env-var detection step in _configure_provider so we can
    prompt 'Use this key?' without echoing the whole secret.
    """
    if not value or len(value) < 12:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


# --- Value Formatting ---


def _format_value(value: Any, rich: bool = True, field_name: str = "") -> str:
    """Single recursive entry point for safe value display. Handles any depth."""
    if value is None or value == "" or value == {} or value == []:
        return "[dim]not set[/dim]" if rich else "[not set]"
    if _is_sensitive_field(field_name) and isinstance(value, str):
        masked = _mask_value(value)
        return f"[dim]{masked}[/dim]" if rich else masked
    if isinstance(value, BaseModel):
        parts = []
        for fname, _finfo in type(value).model_fields.items():
            fval = getattr(value, fname, None)
            formatted = _format_value(fval, rich=False, field_name=fname)
            if formatted != "[not set]":
                parts.append(f"{fname}={formatted}")
        return ", ".join(parts) if parts else ("[dim]not set[/dim]" if rich else "[not set]")
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        # Handle dicts containing BaseModel instances
        parts = []
        for k, v in value.items():
            formatted = _format_value(v, rich=False, field_name=str(k))
            parts.append(f"{k}: {formatted}")
        return ", ".join(parts) if parts else ("[dim]not set[/dim]" if rich else "[not set]")
    return str(value)


def _format_value_for_input(value: Any, field_type: str) -> str:
    """Format a value for use as input default."""
    if value is None or value == "":
        return ""
    if field_type == "list" and isinstance(value, list):
        return ",".join(str(v) for v in value)
    if field_type == "dict" and isinstance(value, dict):
        return json.dumps(value)
    return str(value)


def _validate_field_constraint(value: Any, field_info) -> str | None:
    """Validate a value against Pydantic Field constraints.

    Returns an error message string if validation fails, None if valid.
    Uses attribute-based detection to handle Pydantic v2 internal types.
    """
    if field_info is None or not hasattr(field_info, "metadata"):
        return None

    for m in field_info.metadata:
        if hasattr(m, "ge") and isinstance(value, (int, float)):
            if value < m.ge:
                return f"Value must be >= {m.ge}"
        if hasattr(m, "gt") and isinstance(value, (int, float)):
            if value <= m.gt:
                return f"Value must be > {m.gt}"
        if hasattr(m, "le") and isinstance(value, (int, float)):
            if value > m.le:
                return f"Value must be <= {m.le}"
        if hasattr(m, "lt") and isinstance(value, (int, float)):
            if value >= m.lt:
                return f"Value must be < {m.lt}"
        if hasattr(m, "min_length") and hasattr(value, "__len__"):
            if len(value) < m.min_length:
                return f"Length must be >= {m.min_length}"
        if hasattr(m, "max_length") and hasattr(value, "__len__"):
            if len(value) > m.max_length:
                return f"Length must be <= {m.max_length}"

    return None


def _get_constraint_hint(field_info) -> str:
    """Derive a human-readable constraint hint from field metadata.

    Returns a string like "(0-10)" or "(>= 0)" to append to field display names.
    """
    if field_info is None or not hasattr(field_info, "metadata"):
        return ""

    ge_val = None
    le_val = None
    for m in field_info.metadata:
        if hasattr(m, "ge"):
            ge_val = m.ge
        if hasattr(m, "le"):
            le_val = m.le

    if ge_val is not None and le_val is not None:
        return f" ({ge_val}-{le_val})"
    if ge_val is not None:
        return f" (>= {ge_val})"
    if le_val is not None:
        return f" (<= {le_val})"
    return ""


# --- Rich UI Components ---


def _show_config_panel(display_name: str, model: BaseModel, fields: list) -> None:
    """Display current configuration as a rich table."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="cyan")
    table.add_column("Value")

    for fname, field_info in fields:
        value = getattr(model, fname, None)
        display = _get_field_display_name(fname, field_info)
        formatted = _format_value(value, rich=True, field_name=fname)
        table.add_row(display, formatted)

    console.print(Panel(table, title=f"[bold]{display_name}[/bold]", border_style="blue"))


def _show_section_header(title: str, subtitle: str = "") -> None:
    """Display a section header."""
    console.print()
    if subtitle:
        console.print(
            Panel(f"[dim]{subtitle}[/dim]", title=f"[bold]{title}[/bold]", border_style="blue")
        )
    else:
        console.print(Panel("", title=f"[bold]{title}[/bold]", border_style="blue"))


# --- Input Handlers ---


def _input_bool(display_name: str, current: bool | None) -> bool | None:
    """Get boolean input via confirm dialog."""
    return _get_questionary().confirm(
        display_name,
        default=bool(current) if current is not None else False,
    ).ask()


def _input_text(display_name: str, current: Any, field_type: str, field_info=None) -> Any:
    """Get text input and parse based on field type."""
    default = _format_value_for_input(current, field_type)

    value = _get_questionary().text(f"{display_name}:", default=default).ask()

    if value is None or value == "":
        return None

    if field_type == "int":
        try:
            parsed = int(value)
        except ValueError:
            console.print("[yellow]! Invalid number format, value not saved[/yellow]")
            return None
        if field_info:
            error = _validate_field_constraint(parsed, field_info)
            if error:
                console.print(f"[yellow]! {error}, value not saved[/yellow]")
                return None
        return parsed
    elif field_type == "float":
        try:
            parsed = float(value)
        except ValueError:
            console.print("[yellow]! Invalid number format, value not saved[/yellow]")
            return None
        if field_info:
            error = _validate_field_constraint(parsed, field_info)
            if error:
                console.print(f"[yellow]! {error}, value not saved[/yellow]")
                return None
        return parsed
    elif field_type == "list":
        return [v.strip() for v in value.split(",") if v.strip()]
    elif field_type == "dict":
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            console.print("[yellow]! Invalid JSON format, value not saved[/yellow]")
            return None

    return value


def _input_with_existing(
    display_name: str, current: Any, field_type: str, field_info=None
) -> Any:
    """Handle input with 'keep existing' option for non-empty values."""
    has_existing = current is not None and current != "" and current != {} and current != []

    if has_existing and not isinstance(current, list):
        choice = _get_questionary().select(
            display_name,
            choices=["Enter new value", "Keep existing value"],
            default="Keep existing value",
        ).ask()
        if choice == "Keep existing value" or choice is None:
            return None

    return _input_text(display_name, current, field_type, field_info=field_info)


# --- Pydantic Model Configuration ---


def _get_current_provider(model: BaseModel) -> str:
    """Get the current provider setting from a model (if available)."""
    if hasattr(model, "provider"):
        return getattr(model, "provider", "auto") or "auto"
    return "auto"


def _input_model_with_autocomplete(
    display_name: str, current: Any, provider: str
) -> str | None:
    """Get model input with autocomplete suggestions."""
    from prompt_toolkit.completion import Completer, Completion

    default = str(current) if current else ""

    class DynamicModelCompleter(Completer):
        """Completer that dynamically fetches model suggestions."""

        def __init__(self, provider_name: str):
            self.provider = provider_name

        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            suggestions = get_model_suggestions(text, provider=self.provider, limit=50)
            for model in suggestions:
                # Skip if model doesn't contain the typed text
                if text.lower() not in model.lower():
                    continue
                yield Completion(
                    model,
                    start_position=-len(text),
                    display=model,
                )

    value = _get_questionary().autocomplete(
        f"{display_name}:",
        choices=[""],  # Placeholder, actual completions from completer
        completer=DynamicModelCompleter(provider),
        default=default,
        qmark="●",
    ).ask()

    return value if value else None


def _input_context_window_with_recommendation(
    display_name: str, current: Any, model_obj: BaseModel
) -> int | None:
    """Get context window input with option to fetch recommended value."""
    current_val = current if current else ""

    choices = ["Enter new value"]
    if current_val:
        choices.append("Keep existing value")
    choices.append("[?] Get recommended value")

    choice = _get_questionary().select(
        display_name,
        choices=choices,
        default="Enter new value",
    ).ask()

    if choice is None:
        return None

    if choice == "Keep existing value":
        return None

    if choice == "[?] Get recommended value":
        # Get the model name from the model object
        model_name = getattr(model_obj, "model", None)
        if not model_name:
            console.print("[yellow]! Please configure the model field first[/yellow]")
            return None

        provider = _get_current_provider(model_obj)
        context_limit = get_model_context_limit(model_name, provider)

        if context_limit:
            console.print(
                f"[green]+ Recommended context window: "
                f"{format_token_count(context_limit)} tokens[/green]"
            )
            return context_limit
        else:
            console.print("[yellow]! Could not fetch model info, please enter manually[/yellow]")
            # Fall through to manual input

    # Manual input
    value = _get_questionary().text(
        f"{display_name}:",
        default=str(current_val) if current_val else "",
    ).ask()

    if value is None or value == "":
        return None

    try:
        return int(value)
    except ValueError:
        console.print("[yellow]! Invalid number format, value not saved[/yellow]")
        return None


def _handle_model_field(
    working_model: BaseModel, field_name: str, field_display: str, current_value: Any
) -> None:
    """Handle the 'model' field with autocomplete and context-window auto-fill."""
    provider = _get_current_provider(working_model)
    new_value = _input_model_with_autocomplete(field_display, current_value, provider)
    if new_value is not None and new_value != current_value:
        setattr(working_model, field_name, new_value)
        _try_auto_fill_context_window(working_model, new_value)


def _handle_context_window_field(
    working_model: BaseModel, field_name: str, field_display: str, current_value: Any
) -> None:
    """Handle context_window_tokens with recommendation lookup."""
    new_value = _input_context_window_with_recommendation(
        field_display, current_value, working_model
    )
    if new_value is not None:
        setattr(working_model, field_name, new_value)


_FIELD_HANDLERS: dict[str, Any] = {
    "model": _handle_model_field,
    "context_window_tokens": _handle_context_window_field,
}


def _configure_pydantic_model(
    model: BaseModel,
    display_name: str,
    *,
    skip_fields: set[str] | None = None,
) -> BaseModel | None:
    """Configure a Pydantic model interactively.

    Returns the updated ``working_model`` in three cases — ``[Done]`` Enter,
    Esc, and Left arrow — all treated as "save and back". Returns ``None``
    only when the user hits Ctrl-C and explicitly confirms the discard at
    the follow-up prompt; a clean Ctrl-C with no pending edits also returns
    ``None``. Callers should ``setattr(parent, field, returned_model)``
    whenever the return is non-None.

    Why Esc/Left save instead of discard: the original behavior silently
    threw away every field the user had just edited (token, model name,
    etc.), then the on-disk default-only block via the schema backfill made
    it look like the wizard had saved something. Save-on-back matches the
    instinct people bring from every other settings UI.
    """
    skip_fields = skip_fields or set()
    working_model = model.model_copy(deep=True)
    original_dump = model.model_dump()  # frozen baseline for "is dirty?" check

    fields = [
        (name, info)
        for name, info in type(working_model).model_fields.items()
        if name not in skip_fields
    ]
    if not fields:
        console.print(f"[dim]{display_name}: No configurable fields[/dim]")
        return working_model

    def _is_dirty() -> bool:
        return working_model.model_dump() != original_dump

    def _confirm_discard() -> bool:
        """Ask before throwing away pending edits. True = user confirmed discard."""
        from pythinker.cli.onboard_views import clack

        try:
            return clack.confirm(
                f"Discard your edits to {display_name}?",
                default=False,
            )
        except clack.WizardCancelled:
            # Second Ctrl-C inside the confirm itself = honor the discard intent.
            return True

    def get_choices() -> list[str]:
        items = []
        for fname, finfo in fields:
            value = getattr(working_model, fname, None)
            display = _get_field_display_name(fname, finfo)
            formatted = _format_value(value, rich=False, field_name=fname)
            items.append(f"{display}: {formatted}")
        return items + ["[Done]"]

    while True:
        console.clear()
        _show_config_panel(display_name, working_model, fields)
        choices = get_choices()
        answer = _select_with_back("Select field to configure:", choices)

        # Esc / Left arrow: "save and back" — preserves edits up to the caller.
        if answer is _BACK_PRESSED:
            return working_model
        # Ctrl-C: clean exit if nothing changed; otherwise prompt before discarding.
        if answer is None:
            if _is_dirty() and not _confirm_discard():
                return working_model
            return None
        if answer == "[Done]":
            return working_model

        field_idx = next((i for i, c in enumerate(choices) if c == answer), -1)
        if field_idx < 0 or field_idx >= len(fields):
            return None

        field_name, field_info = fields[field_idx]
        current_value = getattr(working_model, field_name, None)
        ftype = _get_field_type_info(field_info)
        field_display = (
            _get_field_display_name(field_name, field_info) + _get_constraint_hint(field_info)
        )

        # Nested Pydantic model - recurse
        if ftype.type_name == "model":
            nested = current_value
            created = nested is None
            if nested is None and ftype.inner_type:
                nested = ftype.inner_type()
            if nested and isinstance(nested, BaseModel):
                updated = _configure_pydantic_model(nested, field_display)
                if updated is not None:
                    setattr(working_model, field_name, updated)
                elif created:
                    setattr(working_model, field_name, None)
            continue

        # Registered special-field handlers
        handler = _FIELD_HANDLERS.get(field_name)
        if handler:
            handler(working_model, field_name, field_display, current_value)
            continue

        # Select fields with hints (e.g. reasoning_effort)
        if field_name in _SELECT_FIELD_HINTS:
            choices_list, hint = _SELECT_FIELD_HINTS[field_name]
            select_choices = choices_list + ["(clear/unset)"]
            console.print(f"[dim]  Hint: {hint}[/dim]")
            new_value = _select_with_back(
                field_display, select_choices, default=current_value or select_choices[0]
            )
            if new_value is _BACK_PRESSED:
                continue
            if new_value == "(clear/unset)":
                setattr(working_model, field_name, None)
            elif new_value is not None:
                setattr(working_model, field_name, new_value)
            continue

        # Generic field input
        if ftype.type_name == "literal" and ftype.inner_type:
            select_choices = [str(v) for v in ftype.inner_type]
            default_choice = (
                str(current_value) if current_value in ftype.inner_type else select_choices[0]
            )
            new_value = _select_with_back(field_display, select_choices, default=default_choice)
            if new_value is _BACK_PRESSED:
                continue
            if new_value is not None:
                setattr(working_model, field_name, new_value)
            continue
        if ftype.type_name == "bool":
            new_value = _input_bool(field_display, current_value)
        else:
            new_value = _input_with_existing(
                field_display, current_value, ftype.type_name, field_info=field_info
            )
        if new_value is not None:
            setattr(working_model, field_name, new_value)


def _try_auto_fill_context_window(model: BaseModel, new_model_name: str) -> None:
    """Try to auto-fill context_window_tokens if it's at default value.

    Note:
        This function imports AgentDefaults from pythinker.config.schema to get
        the default context_window_tokens value. If the schema changes, this
        coupling needs to be updated accordingly.
    """
    # Check if context_window_tokens field exists
    if not hasattr(model, "context_window_tokens"):
        return

    current_context = getattr(model, "context_window_tokens", None)

    # Check if current value is the default
    # We only auto-fill if the user hasn't changed it from default
    from pythinker.config.schema import AgentDefaults

    default_context = AgentDefaults.model_fields["context_window_tokens"].default

    if current_context != default_context:
        return  # User has customized it, don't override

    provider = _get_current_provider(model)
    context_limit = get_model_context_limit(new_model_name, provider)

    if context_limit:
        setattr(model, "context_window_tokens", context_limit)
        console.print(
            f"[green]+ Auto-filled context window: "
            f"{format_token_count(context_limit)} tokens[/green]"
        )
    else:
        console.print("[dim](i) Could not auto-fill context window (model not in database)[/dim]")


# --- Per-provider pre-key hooks ---------------------------------------------

# Region URLs used by MiniMax pre-key hook. Two regions, two flavors → 4 bases.
_MINIMAX_REGION_BASES = {
    ("Global (api.minimax.io)", "minimax"): "https://api.minimax.io/v1",
    ("Global (api.minimax.io)", "minimax_anthropic"): "https://api.minimax.io/anthropic",
    ("Mainland China (api.minimaxi.com)", "minimax"): "https://api.minimaxi.com/v1",
    ("Mainland China (api.minimaxi.com)", "minimax_anthropic"): "https://api.minimaxi.com/anthropic",
}

_MINIMAX_REGION_SIGNUP_URLS = {
    "Global (api.minimax.io)": "https://platform.minimax.io/user-center/payment/token-plan",
    "Mainland China (api.minimaxi.com)": "https://platform.minimaxi.com/user-center/payment/token-plan",
}


def _detect_minimax_region(api_base: str | None) -> str:
    """Infer the region picker default from an existing api_base."""
    if api_base and "minimaxi.com" in api_base:
        return "Mainland China (api.minimaxi.com)"
    return "Global (api.minimax.io)"


def _minimax_pre_key(provider_config: BaseModel, provider_name: str) -> str:
    """Pre-key hook for MiniMax: pick region, set api_base, return signup URL.

    Runs before the env-detect / browser-open ladder so Mainland users are
    sent to platform.minimaxi.com rather than platform.minimax.io.
    """
    choices = [
        "Global (api.minimax.io)",
        "Mainland China (api.minimaxi.com)",
    ]
    default = _detect_minimax_region(getattr(provider_config, "api_base", None))
    region = _select_with_back("MiniMax region:", choices, default=default)
    if region is _BACK_PRESSED or region is None:
        region = default

    base_key = (region, provider_name)
    if base_key in _MINIMAX_REGION_BASES:
        provider_config.api_base = _MINIMAX_REGION_BASES[base_key]

    return _MINIMAX_REGION_SIGNUP_URLS[region]


# Dispatch table: provider_name -> hook(provider_config, provider_name) -> signup_url.
# Hook may mutate provider_config (e.g. set api_base) and may return a
# signup_url override that the browser-open step will use instead of
# spec.signup_url. Returning "" leaves spec.signup_url in effect.
_PRE_KEY_HOOKS = {
    "minimax": _minimax_pre_key,
    "minimax_anthropic": _minimax_pre_key,
}


# --- Provider Configuration ---


def _no_provider_key_set(config: Config) -> bool:
    """True iff no registered provider has a non-empty api_key on `config`.

    Drives the first-run LLM-provider nudge in run_onboard. Considers EVERY
    provider — gateways (OpenRouter etc.) and local backends (Ollama etc.)
    are valid first-provider choices, so signup_url_required() is the wrong
    filter here.
    """
    from pythinker.providers.registry import PROVIDERS

    return not any(
        (getattr(getattr(config.providers, spec.name, None), "api_key", "") or "")
        for spec in PROVIDERS
    )


@lru_cache(maxsize=1)
def _get_provider_info() -> dict[str, tuple[str, bool, bool, str]]:
    """Get provider info from registry (cached)."""
    from pythinker.providers.registry import PROVIDERS

    # Include OAuth providers (openai-codex, github-copilot) in the picker.
    # _configure_provider routes them to the OAuth login flow instead of an
    # API-key prompt. Filtering them out hid the Codex / Copilot login paths
    # from users who only see the wizard — they had no way to discover that
    # `pythinker provider login openai-codex` exists.
    return {
        spec.name: (
            spec.display_name or spec.name,
            spec.is_gateway,
            spec.is_local,
            spec.default_api_base,
        )
        for spec in PROVIDERS
    }


def _get_provider_names() -> dict[str, str]:
    """Get provider display names."""
    info = _get_provider_info()
    return {name: data[0] for name, data in info.items() if name}


def _run_oauth_login(spec) -> None:
    """Trigger the OAuth login flow for an `is_oauth=True` ProviderSpec.

    Same logic as the `pythinker provider login <name>` CLI command, but
    callable from inside the wizard. Status is rendered through the clack
    timeline helpers so each event (✓ authenticated, ✗ failure, paste
    prompts) sits on the persistent ``│`` bar, keeping the diamond column
    aligned with the rest of the wizard.

    Implementation notes:

    * ``prompt_fn`` uses ``input()`` instead of a nested questionary
      session, because questionary acquires the TTY exclusively while a
      picker is open — a nested prompt session inside the OAuth callback
      conflicts and silently aborts, sending the user back to the picker
      with no error printed.
    * ``clack.pause()`` at the end keeps the success / failure message on
      screen until the user acknowledges it; the wizard's next
      ``console.clear()`` would otherwise wipe the OAuth result.
    """
    from pythinker.cli.onboard_views import clack

    label = spec.label

    def _prompt(s: str) -> str:
        # Bypass questionary inside the OAuth callback so we don't fight
        # the outer picker for the TTY. Plain input() is enough for
        # paste-back codes, and surfaces Ctrl-C / Ctrl-D cleanly.
        try:
            return input(f"{s}: ") if not s.endswith(":") else input(s + " ")
        except (EOFError, KeyboardInterrupt):
            return ""

    if spec.name == "openai_codex":
        try:
            from oauth_cli_kit import get_token, login_oauth_interactive
        except ImportError:
            clack.failure("oauth_cli_kit not installed.", "Run: pip install oauth-cli-kit")
            clack.pause()
            return

        token = None
        try:
            token = get_token()
        except Exception:
            pass
        if token and getattr(token, "access", None):
            account = getattr(token, "account_id", "") or ""
            clack.success(f"Already authenticated with {label}", account or None)
            clack.pause()
            return

        clack.print_status(f"Starting OAuth login for {label}...")
        try:
            token = login_oauth_interactive(
                print_fn=lambda s: clack.print_status(str(s)),
                prompt_fn=_prompt,
            )
        except KeyboardInterrupt:
            clack.failure("Login cancelled.")
            clack.pause()
            return
        except Exception as exc:
            clack.failure("Authentication failed.", str(exc))
            clack.pause()
            return

        if token and getattr(token, "access", None):
            account = getattr(token, "account_id", None) or "OpenAI"
            clack.success(f"Authenticated with {label}", account)
        else:
            clack.failure("Authentication failed (no token returned).")
        clack.pause()
        return

    if spec.name == "github_copilot":
        try:
            from pythinker.providers.github_copilot_provider import login_github_copilot
        except ImportError as exc:
            clack.failure(f"Cannot start {label} login.", str(exc))
            clack.pause()
            return

        clack.print_status(f"Starting {label} device flow...")
        try:
            token = login_github_copilot(
                print_fn=lambda s: clack.print_status(str(s)),
                prompt_fn=_prompt,
            )
        except KeyboardInterrupt:
            clack.failure("Login cancelled.")
            clack.pause()
            return
        except Exception as exc:
            clack.failure("Authentication failed.", str(exc))
            clack.pause()
            return

        account = getattr(token, "account_id", None) or "GitHub"
        clack.success(f"Authenticated with {label}", account)
        clack.pause()
        return

    clack.failure(f"OAuth login not implemented for {label}.")
    clack.pause()


def _configure_provider(config: Config, provider_name: str) -> None:
    """Configure a single LLM provider with optional auto-open + MiniMax follow-up.

    OAuth providers (openai-codex, github-copilot) bypass the API-key prompt
    entirely and dispatch to the OAuth login flow — same handlers that
    `pythinker provider login <name>` uses, so the wizard and CLI surface
    the same auth experience.
    """
    from pythinker.providers.registry import PROVIDERS, signup_url_required

    spec = next((s for s in PROVIDERS if s.name == provider_name), None)

    # OAuth providers (codex, copilot) don't have an api_key field to fill.
    # Hand off to the same login handler the CLI uses; treat success as the
    # provider being "configured" (the OAuth token is persisted by oauth_cli_kit
    # under platformdirs, not in config.json).
    if spec and spec.is_oauth:
        _run_oauth_login(spec)
        return

    provider_config = getattr(config.providers, provider_name, None)
    if provider_config is None:
        console.print(f"[red]Unknown provider: {provider_name}[/red]")
        return
    display_name = _get_provider_names().get(provider_name, provider_name)
    info = _get_provider_info()
    default_api_base = info.get(provider_name, (None, None, None, None))[3]

    if default_api_base and not provider_config.api_base:
        provider_config.api_base = default_api_base

    # --- Snapshot for MiniMax follow-up undo (Findings 5 + 7) ---------------
    # Always built (cheap: two model_copy() calls) so the post-walker
    # dispatch can rely on the snapshot unconditionally — Tasks 5-7 use it
    # to undo walker writes when the user picks a different flavor.
    prev_key = provider_config.api_key or ""
    provider_snapshot = {
        "minimax": config.providers.minimax.model_copy(deep=True),
        "minimax_anthropic": config.providers.minimax_anthropic.model_copy(deep=True),
    }

    # --- Generic auto-open ladder (runs only when api_key is empty) ---------
    if not provider_config.api_key:
        # 0. Per-provider pre-key hook (region pick, etc.)
        signup_url_override = ""
        hook = _PRE_KEY_HOOKS.get(provider_name)
        if hook:
            signup_url_override = hook(provider_config, provider_name=provider_name)

        # 1. Env-var detection
        env_value = os.environ.get(spec.env_key, "") if (spec and spec.env_key) else ""
        env_used = False
        if env_value:
            confirmed = _get_questionary().confirm(
                f"Found {spec.env_key} in environment ({_mask_token(env_value)}). "
                f"Use this key?",
                default=True,
            ).ask()
            if confirmed:
                provider_config.api_key = env_value
                env_used = True

        # 2. Browser open
        if not env_used:
            signup_url = signup_url_override or (
                spec.signup_url if (spec and signup_url_required(spec)) else ""
            )
            if signup_url:
                console.print(Panel(
                    f"[bold]{display_name}[/bold] needs an API key.\n"
                    f"Open [cyan]{signup_url}[/cyan] in your browser?"
                    + (
                        f"\n[dim]Learn more: {spec.docs_url}[/dim]"
                        if (spec and spec.docs_url) else ""
                    ),
                    title="API key",
                    border_style="cyan",
                ))
                if _get_questionary().confirm(
                    "Open browser now?", default=True,
                ).ask():
                    try:
                        webbrowser.open(signup_url)
                    except Exception as exc:  # noqa: BLE001 — never block onboarding
                        logger.debug("webbrowser.open({!r}) raised: {}", signup_url, exc)
                    console.print(f"[dim]URL: {signup_url}[/dim]")

    # --- Existing field walker ---------------------------------------------
    updated_provider = _configure_pydantic_model(
        provider_config,
        display_name,
    )
    if updated_provider is not None:
        setattr(config.providers, provider_name, updated_provider)

    # --- MiniMax-specific post-walk follow-up (Task 5+) ---------------------
    # Only MiniMax needs a post-walk follow-up; if a second provider arises,
    # mirror _PRE_KEY_HOOKS with a _POST_WALK_HOOKS dispatch table.
    if provider_name in {"minimax", "minimax_anthropic"}:
        _configure_minimax_followup(
            config, provider_name, prev_key, provider_snapshot,
        )


# Canonical region+flavor URLs used by the "stale config" predicate.
# Derived from _MINIMAX_REGION_BASES so the two cannot drift.
_MINIMAX_CANONICAL_BASES: dict[str, set[str]] = {}
for (_region, _pname), _url in _MINIMAX_REGION_BASES.items():
    _MINIMAX_CANONICAL_BASES.setdefault(_pname, set()).add(_url)
del _region, _pname, _url


def _minimax_followup_needs_offer(
    config: Config, provider_name: str
) -> bool:
    """Return True iff the existing MiniMax setup looks incomplete or stale."""
    other = "minimax_anthropic" if provider_name == "minimax" else "minimax"
    other_cfg = getattr(config.providers, other)
    this_cfg = getattr(config.providers, provider_name)
    counterpart_empty = not (other_cfg.api_key or "")
    base_off_canonical = (this_cfg.api_base or "") not in _MINIMAX_CANONICAL_BASES[provider_name]
    model_not_minimax = not (config.agents.defaults.model or "").startswith("MiniMax-")
    return counterpart_empty or base_off_canonical or model_not_minimax


def _minimax_followup_run_steps(
    config: Config,
    provider_name: str,
    provider_snapshot: dict,
) -> None:
    """Run region → flavor → plan-tier → validate. Each sub-step is its own
    function so tests can monkeypatch them independently."""
    region = _minimax_followup_region_step(config, provider_name)
    if provider_name == "minimax_anthropic":
        _warn_on_anthropic_env_overrides()
    validate_target = _minimax_followup_flavor_step(
        config, provider_name, provider_snapshot, region,
    )
    _minimax_followup_plan_tier_step(config, region)
    _minimax_followup_validate_step(config, validate_target)


def _warn_on_anthropic_env_overrides() -> None:
    """Surface ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL conflicts (opencode hint)."""
    leaks = [v for v in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN") if os.environ.get(v)]
    if not leaks:
        return
    console.print(
        f"[yellow]Warning: {', '.join(leaks)} set in your environment. "
        f"It may override the MiniMax Anthropic endpoint at runtime — "
        f"consider unsetting it.[/yellow]"
    )


def _minimax_followup_region_step(config: Config, provider_name: str) -> str:
    """Pick (or reuse) the region. Returns the chosen region label."""
    this_cfg = getattr(config.providers, provider_name)
    current_base = this_cfg.api_base or ""
    if current_base in _MINIMAX_CANONICAL_BASES[provider_name]:
        # Pre-key hook (or prior setup) already set a canonical base;
        # don't re-prompt. Just return the implied region.
        return _detect_minimax_region(current_base)

    choices = ["Global (api.minimax.io)", "Mainland China (api.minimaxi.com)"]
    default = _detect_minimax_region(current_base)
    region = _select_with_back("MiniMax region:", choices, default=default)
    if region is _BACK_PRESSED or region is None:
        region = default
    base_key = (region, provider_name)
    if base_key in _MINIMAX_REGION_BASES:
        this_cfg.api_base = _MINIMAX_REGION_BASES[base_key]
    return region


_FLAVOR_OPENAI = "OpenAI-compatible (default — supports reasoning_split thinking)"
_FLAVOR_ANTHROPIC = "Anthropic-compatible (native thinking blocks visible in stream)"
_FLAVOR_BOTH = "Both (recommended — same key, both flavors)"


def _minimax_followup_flavor_step(
    config: Config, provider_name: str, provider_snapshot: dict, region: str,
) -> str:
    """Pick endpoint flavor with snapshot-aware undo on swap.

    Returns the name of the provider slot that ended up holding the entered
    key after this step — ``provider_name`` for the no-swap (or "Both") case,
    and ``other`` (the opposite flavor) for the swap case. Callers use the
    return value to validate the right slot downstream.
    """
    other = "minimax_anthropic" if provider_name == "minimax" else "minimax"
    entered_cfg = getattr(config.providers, provider_name)
    entered_key = entered_cfg.api_key or ""

    choices = [_FLAVOR_OPENAI, _FLAVOR_ANTHROPIC, _FLAVOR_BOTH]
    pick = _select_with_back("Endpoint flavor:", choices, default=_FLAVOR_BOTH)
    if pick is _BACK_PRESSED or pick is None:
        pick = _FLAVOR_BOTH

    other_base = _MINIMAX_REGION_BASES[(region, other)]

    if pick == _FLAVOR_BOTH:
        # Preserve pre-existing fields on `other` (e.g. extra_headers) by
        # starting from snapshot and overlaying the key + base.
        other_cfg = provider_snapshot[other].model_copy(deep=True)
        other_cfg.api_key = entered_key
        other_cfg.api_base = other_base
        setattr(config.providers, other, other_cfg)
        return provider_name

    entered_is_openai = (provider_name == "minimax")
    pick_is_openai = (pick == _FLAVOR_OPENAI)
    if entered_is_openai == pick_is_openai:
        # Selection matches entered — leave `other` exactly at snapshot.
        setattr(config.providers, other, provider_snapshot[other].model_copy(deep=True))
        return provider_name

    # Swap: user wants the opposite flavor of the one they entered.
    # 1. Move key + base to `other`.
    other_cfg = provider_snapshot[other].model_copy(deep=True)
    other_cfg.api_key = entered_key
    other_cfg.api_base = other_base
    setattr(config.providers, other, other_cfg)
    # 2. Restore `entered` from snapshot (undoes walker writes).
    setattr(
        config.providers, provider_name,
        provider_snapshot[provider_name].model_copy(deep=True),
    )
    # 3. Visible note.
    console.print(
        f"[yellow]Removed the just-entered key from providers.{provider_name}; "
        f"only providers.{other} now holds the MiniMax key.[/yellow]"
    )
    return other


_TIER_STANDARD = "Standard plan (Starter/Plus/Max) — MiniMax-M2.7"
_TIER_HIGHSPEED = "Highspeed plan (Plus-Highspeed/Max-Highspeed/Ultra-Highspeed) — MiniMax-M2.7-highspeed"
_TIER_CUSTOM = "Custom (type a model name)"

_TIER_TO_MODEL = {
    _TIER_STANDARD: "MiniMax-M2.7",
    _TIER_HIGHSPEED: "MiniMax-M2.7-highspeed",
}

# Derived from the schema so a future change to AgentDefaults.model can't
# silently disable the no-clobber guard. Wrapped so a schema rename / nested
# restructure cannot kill the CLI at import time — we'd rather lose the guard
# (no-clobber falls through, user sees a hint instead of a write) than refuse
# to load the wizard.
try:
    _PRISTINE_DEFAULT_MODEL: str = (
        Config.model_fields["agents"].default_factory().defaults.model
    )
except (KeyError, AttributeError, TypeError):
    _PRISTINE_DEFAULT_MODEL = ""


def _minimax_followup_plan_tier_step(config: Config, region: str) -> None:
    """Pick plan tier and (no-clobber) write its model to agents.defaults.model."""
    choices = [_TIER_STANDARD, _TIER_HIGHSPEED, _TIER_CUSTOM]
    pick = _select_with_back("MiniMax plan tier:", choices, default=_TIER_STANDARD)
    if pick is _BACK_PRESSED or pick is None:
        pick = _TIER_STANDARD

    if pick == _TIER_CUSTOM:
        chosen_model = _input_model_with_autocomplete(
            "Default model", config.agents.defaults.model or "", "minimax",
        )
    else:
        chosen_model = _TIER_TO_MODEL[pick]

    if not chosen_model:
        return

    if config.agents.defaults.model == _PRISTINE_DEFAULT_MODEL:
        config.agents.defaults.model = chosen_model
    else:
        console.print(
            f"[dim]Tip: set [cyan]agents.defaults.model[/cyan] to "
            f"[bold]{chosen_model}[/bold] to use it as the default. "
            f"Current default: {config.agents.defaults.model}[/dim]"
        )


def _minimax_followup_validate_step(config: Config, provider_name: str) -> None:
    """Optional: list /models to check the key. 401 clears the key; other
    errors warn and proceed."""
    this_cfg = getattr(config.providers, provider_name)
    api_key = this_cfg.api_key or ""
    api_base = this_cfg.api_base or ""
    if not api_key or not api_base:
        return

    if not _get_questionary().confirm(
        "Verify the key by listing models?", default=True,
    ).ask():
        return

    # Always validate via OpenAI-flavor base — it's stable and key-only.
    if "minimaxi.com" in api_base:
        validate_base = "https://api.minimaxi.com/v1"
    else:
        validate_base = "https://api.minimax.io/v1"
    url = f"{validate_base}/models"

    try:
        resp = httpx.get(
            url, headers={"Authorization": f"Bearer {api_key}"}, timeout=10.0,
        )
    except httpx.HTTPError as exc:  # parent of TimeoutException, RequestError, InvalidURL, etc.
        console.print(
            f"[yellow]Couldn't verify the MiniMax key (network: {exc.__class__.__name__}); "
            f"proceeding anyway.[/yellow]"
        )
        return

    if resp.status_code == 401:
        console.print(
            "[red]Invalid MiniMax API key (HTTP 401). Cleared — please re-enter.[/red]"
        )
        this_cfg.api_key = ""
        return

    if resp.status_code != 200:
        console.print(
            f"[yellow]Couldn't verify the MiniMax key (HTTP {resp.status_code}); "
            f"proceeding anyway.[/yellow]"
        )
        return

    body = resp.json()
    data = body.get("data", []) if isinstance(body, dict) else []
    sample_ids = [
        (item.get("id", "?") if isinstance(item, dict) else "?")
        for item in data[:3]
    ]
    console.print(
        f"[green]✓[/green] MiniMax key valid. Sample models: "
        f"{', '.join(sample_ids) if sample_ids else '(none returned)'}"
    )


def _configure_minimax_followup(
    config: Config,
    provider_name: str,
    prev_key: str,
    provider_snapshot: dict,
) -> None:
    """Run the MiniMax-specific follow-up after the field walker.

    Trigger ladder:
      * auto-run when the key arrived or changed this pass
      * offer-run (default N) when an existing MiniMax setup is incomplete/stale
      * no-op otherwise
    """
    this_cfg = getattr(config.providers, provider_name)
    current_key = this_cfg.api_key or ""
    if not current_key:
        return  # walker still didn't get a key — nothing to follow up

    arrived_or_changed = prev_key == "" or prev_key != current_key
    if arrived_or_changed:
        _minimax_followup_run_steps(config, provider_name, provider_snapshot)
        return

    if _minimax_followup_needs_offer(config, provider_name):
        agreed = _get_questionary().confirm(
            "This MiniMax provider is already configured. "
            "Run the region/flavor/model setup again?",
            default=False,
        ).ask()
        if agreed:
            _minimax_followup_run_steps(config, provider_name, provider_snapshot)


def _configure_providers(config: Config) -> None:
    """Configure LLM providers."""

    def get_provider_choices() -> list[str]:
        """Build provider choices with config status indicators.

        OAuth providers (codex, copilot) are shown with an ``(OAuth)`` suffix
        and trigger the login flow when picked — they don't have an api_key
        slot to mark with ``*``.
        """
        from pythinker.providers.registry import PROVIDERS

        spec_by_name = {s.name: s for s in PROVIDERS}
        choices = []
        for name, display in _get_provider_names().items():
            spec = spec_by_name.get(name)
            if spec and spec.is_oauth:
                choices.append(f"{display} (OAuth)")
            else:
                provider = getattr(config.providers, name, None)
                if provider and provider.api_key:
                    choices.append(f"{display} *")
                else:
                    choices.append(display)
        return choices + ["<- Back"]

    while True:
        try:
            console.clear()
            _show_section_header(
                "LLM Providers", "Select a provider to configure API key and endpoint"
            )
            choices = get_provider_choices()
            answer = _select_with_back("Select provider:", choices)

            if answer is _BACK_PRESSED or answer is None or answer == "<- Back":
                break

            # Type guard: answer is now guaranteed to be a string
            assert isinstance(answer, str)
            # Extract provider name from choice (strip " *" / " (OAuth)" suffix)
            provider_name = answer.replace(" *", "").replace(" (OAuth)", "")
            # Find the actual provider key from display names
            for name, display in _get_provider_names().items():
                if display == provider_name:
                    _configure_provider(config, name)
                    break

        except KeyboardInterrupt:
            console.print("\n[dim]Returning to main menu...[/dim]")
            break


# --- Channel Configuration ---


@lru_cache(maxsize=1)
def _get_channel_info() -> dict[str, tuple[str, type[BaseModel]]]:
    """Get channel info (display name + config class) from channel modules."""
    import importlib

    from pythinker.channels.registry import discover_all

    result: dict[str, tuple[str, type[BaseModel]]] = {}
    for name, channel_cls in discover_all().items():
        try:
            mod = importlib.import_module(f"pythinker.channels.{name}")
            config_name = channel_cls.__name__.replace("Channel", "Config")
            config_cls = getattr(mod, config_name, None)
            if config_cls and isinstance(config_cls, type) and issubclass(config_cls, BaseModel):
                display_name = getattr(channel_cls, "display_name", name.capitalize())
                result[name] = (display_name, config_cls)
        except Exception:
            logger.warning("Failed to load channel module: {}", name)
    return result


def _get_channel_names() -> dict[str, str]:
    """Get channel display names."""
    return {name: info[0] for name, info in _get_channel_info().items()}


def _get_channel_config_class(channel: str) -> type[BaseModel] | None:
    """Get channel config class."""
    entry = _get_channel_info().get(channel)
    return entry[1] if entry else None


def _configure_channel(config: Config, channel_name: str) -> None:
    """Configure a single channel."""
    channel_dict = getattr(config.channels, channel_name, None)
    if channel_dict is None:
        channel_dict = {}
        setattr(config.channels, channel_name, channel_dict)

    display_name = _get_channel_names().get(channel_name, channel_name)
    config_cls = _get_channel_config_class(channel_name)

    if config_cls is None:
        console.print(f"[red]No configuration class found for {display_name}[/red]")
        return

    model = config_cls.model_validate(channel_dict) if channel_dict else config_cls()

    updated_channel = _configure_pydantic_model(
        model,
        display_name,
    )
    if updated_channel is not None:
        new_dict = updated_channel.model_dump(by_alias=True, exclude_none=True)
        setattr(config.channels, channel_name, new_dict)


def _configure_channels(config: Config) -> None:
    """Configure chat channels."""

    def get_channel_choices() -> list[str]:
        """Build channel choices with (configured) suffix on already-enabled ones.

        Mirrors the ' *' suffix pattern that ``_configure_providers`` uses so
        users can tell at a glance which channels already have a working setup.
        """
        choices = []
        for name in _get_channel_names().keys():
            ch = getattr(config.channels, name, None)
            enabled = bool(ch) and (
                ch.get("enabled") if isinstance(ch, dict)
                else getattr(ch, "enabled", False)
            )
            choices.append(f"{name} (configured)" if enabled else name)
        return choices + ["<- Back"]

    while True:
        try:
            console.clear()
            _show_section_header(
                "Chat Channels", "Select a channel to configure connection settings"
            )
            answer = _select_with_back("Select channel:", get_channel_choices())

            if answer is _BACK_PRESSED or answer is None or answer == "<- Back":
                break

            # Type guard: answer is now guaranteed to be a string
            assert isinstance(answer, str)
            # Strip the suffix to recover the channel registry key.
            picked = answer.split(" ", 1)[0]
            _configure_channel(config, picked)
        except KeyboardInterrupt:
            console.print("\n[dim]Returning to main menu...[/dim]")
            break


# --- General Settings ---

_SETTINGS_SECTIONS: dict[str, tuple[str, str, set[str] | None]] = {
    "Agent Settings": ("Agent Defaults", "Configure default model, temperature, and behavior", None),
    "Channel Common": (
        "Channel Common",
        "Configure cross-channel behavior: progress, tool hints, retries",
        None,
    ),
    "API Server": ("API Server", "Configure OpenAI-compatible API endpoint", None),
    "Gateway": ("Gateway Settings", "Configure server host, port, and heartbeat", None),
    "Tools": ("Tools Settings", "Configure web search, shell exec, and other tools", {"mcp_servers"}),
}

_SETTINGS_GETTER = {
    "Agent Settings": lambda c: c.agents.defaults,
    "Channel Common": lambda c: c.channels,
    "API Server": lambda c: c.api,
    "Gateway": lambda c: c.gateway,
    "Tools": lambda c: c.tools,
}

_SETTINGS_SETTER = {
    "Agent Settings": lambda c, v: setattr(c.agents, "defaults", v),
    "Channel Common": lambda c, v: setattr(c, "channels", v),
    "API Server": lambda c, v: setattr(c, "api", v),
    "Gateway": lambda c, v: setattr(c, "gateway", v),
    "Tools": lambda c, v: setattr(c, "tools", v),
}


def _configure_general_settings(config: Config, section: str) -> None:
    """Configure a general settings section (header + model edit + writeback)."""
    meta = _SETTINGS_SECTIONS.get(section)
    if not meta:
        return
    display_name, subtitle, skip = meta
    model = _SETTINGS_GETTER[section](config)
    updated = _configure_pydantic_model(model, display_name, skip_fields=skip)
    if updated is not None:
        _SETTINGS_SETTER[section](config, updated)


# --- Summary ---


def _summarize_model(obj: BaseModel) -> list[tuple[str, str]]:
    """Recursively summarize a Pydantic model. Returns list of (field, value) tuples."""
    items: list[tuple[str, str]] = []
    for field_name, field_info in type(obj).model_fields.items():
        value = getattr(obj, field_name, None)
        if value is None or value == "" or value == {} or value == []:
            continue
        display = _get_field_display_name(field_name, field_info)
        ftype = _get_field_type_info(field_info)
        if ftype.type_name == "model" and isinstance(value, BaseModel):
            for nested_field, nested_value in _summarize_model(value):
                items.append((f"{display}.{nested_field}", nested_value))
            continue
        formatted = _format_value(value, rich=False, field_name=field_name)
        if formatted != "[not set]":
            items.append((display, formatted))
    return items


def _print_summary_panel(rows: list[tuple[str, str]], title: str) -> None:
    """Build a two-column summary panel and print it."""
    if not rows:
        return
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Setting", style="cyan")
    table.add_column("Value")
    for field_name, value in rows:
        table.add_row(field_name, value)
    console.print(Panel(table, title=f"[bold]{title}[/bold]", border_style="blue"))


def _show_summary(config: Config) -> None:
    """Display configuration summary using rich."""
    console.print()

    # Providers
    provider_rows = []
    for name, display in _get_provider_names().items():
        provider = getattr(config.providers, name, None)
        status = (
            "[green]configured[/green]"
            if (provider and provider.api_key)
            else "[dim]not configured[/dim]"
        )
        provider_rows.append((display, status))
    _print_summary_panel(provider_rows, "LLM Providers")

    # Channels
    channel_rows = []
    for name, display in _get_channel_names().items():
        channel = getattr(config.channels, name, None)
        if channel:
            enabled = (
                channel.get("enabled", False)
                if isinstance(channel, dict)
                else getattr(channel, "enabled", False)
            )
            status = "[green]enabled[/green]" if enabled else "[dim]disabled[/dim]"
        else:
            status = "[dim]not configured[/dim]"
        channel_rows.append((display, status))
    _print_summary_panel(channel_rows, "Chat Channels")

    # Settings sections
    for title, model in [
        ("Agent Settings", config.agents.defaults),
        ("Channel Common", config.channels),
        ("API Server", config.api),
        ("Gateway", config.gateway),
        ("Tools", config.tools),
    ]:
        _print_summary_panel(_summarize_model(model), title)

    _pause()


def _pause() -> None:
    """Pause for user acknowledgement before clearing the screen."""
    _get_questionary().text("Press Enter to continue...", default="").ask()


# --- Web Search Configuration ---

_WEBSEARCH_PROVIDERS: tuple[str, ...] = (
    "duckduckgo", "brave", "tavily", "searxng", "jina", "kagi",
)
_WEBSEARCH_ENV_VARS: dict[str, str] = {
    "brave": "BRAVE_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "jina": "JINA_API_KEY",
    "kagi": "KAGI_API_KEY",
    "searxng": "SEARXNG_BASE_URL",
}


def _websearch_provider_status(provider: str, search_cfg: WebSearchConfig) -> str:
    """Return a bare status hint (no parens). Caller wraps in '(...)' for the label."""
    if provider == "duckduckgo":
        return "free, no key"

    slot = search_cfg.credentials_for(provider)
    is_searxng = provider == "searxng"
    has_slot = bool(slot.base_url) if is_searxng else bool(slot.api_key)
    if has_slot:
        return "✓ configured"

    env_var = _WEBSEARCH_ENV_VARS.get(provider, "")
    if env_var and os.environ.get(env_var, "").strip():
        return "✓ env var"

    return "needs key"


def _run_websearch_live_test(
    search_cfg: WebSearchConfig, proxy: str | None
) -> tuple[bool, str]:
    """Run a one-shot test query. Returns (ok, message).

    Wizard is sync; this bridges into asyncio with a one-shot event loop.
    Returns ``(True, "<first result title>")`` on success or
    ``(False, "<error message>")`` on failure/timeout.
    """
    tool = WebSearchTool(config=search_cfg, proxy=proxy)

    async def _run() -> str:
        return await asyncio.wait_for(
            tool.execute("pythinker test", count=1), timeout=10.0
        )

    try:
        result = asyncio.run(_run())
    except RuntimeError as exc:
        # asyncio.run() refuses if a loop is already running. The wizard
        # is sync, so this branch is unreachable today; let it surface
        # rather than silently swallow a programming error.
        msg = str(exc).lower()
        if "running event loop" in msg or "cannot be called from" in msg:
            raise
        return False, str(exc)
    except asyncio.TimeoutError as exc:
        return False, f"timed out after 10s ({exc})"
    except Exception as exc:
        return False, str(exc)

    if not isinstance(result, str):
        return False, f"unexpected result type: {type(result).__name__}"
    if result.startswith("Error") or result.startswith("No results for:"):
        # Strip leading "Error: " for cleaner display.
        msg = result[len("Error: "):] if result.startswith("Error: ") else result
        return False, msg

    # Pull the first numbered line ("1. <title>") from the formatted output.
    for line in result.splitlines():
        stripped = line.strip()
        if stripped.startswith("1. "):
            return True, stripped[len("1. "):]
    return True, ""  # success but no parseable hit; still treat as ok


def _configure_web_search(config: Config) -> None:
    """Guided web-search setup: pick provider, prompt for key, optional test."""
    search = config.tools.web.search

    # Step 1: Provider picker with status hints.
    choices: list[str] = []
    label_to_provider: dict[str, str] = {}
    for name in _WEBSEARCH_PROVIDERS:
        status = _websearch_provider_status(name, search)
        label = f"{name} ({status})"
        choices.append(label)
        label_to_provider[label] = name

    default_label = next(
        (lbl for lbl, prov in label_to_provider.items() if prov == search.provider),
        choices[0],
    )

    picked = _get_questionary().select(
        "Web search provider:",
        choices=choices,
        default=default_label,
    ).ask()
    if not picked:
        return

    provider = label_to_provider[picked]
    search.provider = provider

    # DuckDuckGo needs no further setup.
    if provider == "duckduckgo":
        return

    is_searxng = provider == "searxng"
    env_var = _WEBSEARCH_ENV_VARS[provider]
    env_value = os.environ.get(env_var, "").strip()

    # Step 2: Env-var detection.
    if env_value:
        use_env = _get_questionary().confirm(
            f"Detected {env_var} in env. Use it?",
            default=True,
        ).ask()
        if use_env is None:
            return  # user pressed Ctrl-C — abort the step entirely
        if use_env:
            # Leave the slot empty so runtime falls through to the env var.
            return

    # Step 3: Prompt for credential.
    if is_searxng:
        prompt = "SearXNG base URL (https://...):"
    else:
        prompt = f"{provider.capitalize()} API key:"

    while True:
        value = _get_questionary().text(prompt, default="").ask()
        if value is None:
            return
        value = value.strip()
        if not value:
            return  # user pressed Enter on empty — abort this step

        if is_searxng:
            # Reuse the URL validator from the web tool.
            from pythinker.agent.tools.web import _validate_url
            ok, err = _validate_url(value)
            if not ok:
                console.print(f"[yellow]! Invalid URL: {err}[/yellow]")
                continue
        break

    slot = search.providers.get(provider) or WebSearchProviderConfig()
    if is_searxng:
        slot.base_url = value
    else:
        slot.api_key = value
    search.providers[provider] = slot

    # Step 4: Optional live test.
    do_test = _get_questionary().confirm(
        "Run a test query to verify?", default=True
    ).ask()
    if not do_test:
        return

    proxy = config.tools.web.proxy
    ok, msg = _run_websearch_live_test(search, proxy=proxy)
    if ok:
        if msg:
            console.print(f"[green]✓ {provider} responded: {msg}[/green]")
        else:
            console.print(f"[green]✓ {provider} responded.[/green]")
    else:
        console.print(f"[red]✗ {provider} test failed: {msg}[/red]")


# --- Main Entry Point ---


def required_headless_flags(cfg: Config | None) -> list[str]:
    """Return the minimum flag set needed for ``pythinker onboard
    --non-interactive`` to complete without prompting, given the current
    state of ``cfg`` (or a fresh ``Config()`` when ``cfg`` is None).

    Flag rules (mirrors pythinker's headless-precondition checker):

    - ``--non-interactive`` and ``--yes-security`` are always required —
      the security disclaimer step exits 1 in headless mode otherwise.
    - ``--auth <provider>`` is required when no provider has a credential
      yet (api_key set, OAuth token on disk, or ``--auth skip``).
    - ``--skip-gateway`` is appended by default — without an explicit
      gateway choice, the wizard prompts. Replace with ``--start-gateway``
      if you want the gateway to start at the end of onboarding.
    """
    from pythinker.providers.registry import PROVIDERS

    flags: list[str] = ["--non-interactive", "--yes-security"]
    cfg = cfg or Config()

    has_provider = False
    for spec in PROVIDERS:
        provider_cfg = getattr(cfg.providers, spec.name, None)
        if provider_cfg is None:
            continue
        api_key = getattr(provider_cfg, "api_key", "") or ""
        if api_key:
            has_provider = True
            break
        if spec.is_oauth:
            try:
                from pythinker.auth import credential_source

                if credential_source(spec, provider_cfg) == "oauth":
                    has_provider = True
                    break
            except Exception:  # noqa: BLE001
                pass
    if not has_provider:
        flags.append("--auth <provider-name-or-skip>")

    flags.append("--skip-gateway")
    return flags


def run_onboard(
    initial_config: Config | None = None,
    *,
    non_interactive: bool = False,
    flow: str | None = None,
    yes_security: bool = False,
    auth: str | None = None,
    auth_method: str | None = None,
    start_gateway: bool | None = None,
    skip_gateway: bool = False,
    reset: str | None = None,
    workspace: str | None = None,
    open_webui: bool = False,
) -> OnboardResult:
    """Run the linear onboarding wizard.

    Replaces the legacy hub-and-spoke menu with the pythinker-style linear
    flow. Steps are registered in `_WIZARD_STEPS` and orchestrated by
    `_run_linear_wizard`.
    """
    if initial_config is not None:
        draft = initial_config.model_copy(deep=True)
    else:
        config_path = get_config_path()
        draft = load_config(config_path) if config_path.exists() else Config()

    ctx = _WizardContext(
        draft=draft,
        flow=flow or "manual",
        non_interactive=non_interactive,
        yes_security=yes_security,
        auth=auth,
        auth_method=auth_method,
        start_gateway=start_gateway,
        skip_gateway=skip_gateway,
        workspace_override=workspace,
        open_webui=open_webui,
    )
    if reset:
        from pythinker.cli.onboard_views.reset import SCOPE_LOOKUP

        ctx.reset_pending = True
        ctx.reset_scope = SCOPE_LOOKUP.get(reset)
    return _run_linear_wizard(ctx)
