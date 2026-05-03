"""`pythinker doctor` — diagnose install, config, and authentication state.

Every check returns a ``CheckResult``; the runner prints them grouped by
section and exits non-zero when anything is wrong.  Designed to be the
single command a user runs when something isn't working — the output
doubles as a paste-ready support snippet.
"""
from __future__ import annotations

import asyncio
import importlib.util
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from rich.console import Console

from pythinker import __logo__, __version__

Status = str  # "ok" | "warn" | "error"


@dataclass(frozen=True)
class CheckResult:
    status: Status
    label: str
    detail: str = ""
    fix: str = ""


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


MIN_PYTHON = (3, 11)


def _check_python_version() -> CheckResult:
    major, minor = sys.version_info[:2]
    version = f"{major}.{minor}.{sys.version_info.micro}"
    if (major, minor) >= MIN_PYTHON:
        return CheckResult("ok", "Python", version)
    return CheckResult(
        "error",
        "Python",
        f"{version} (requires >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]})",
        fix="Install Python 3.11+ (e.g. `uv python install 3.13`) then reinstall pythinker.",
    )


def _check_install_location() -> CheckResult:
    # shutil.which() only finds executables on PATH — if it returns a path, by
    # definition the parent directory *is* on PATH, even if the binary itself is
    # a symlink into a different directory.  No separate "is it on PATH?" check
    # needed; that was a false positive waiting to happen.
    binary = shutil.which("pythinker")
    if binary:
        return CheckResult("ok", "pythinker", f"{__version__} at {binary}")
    # invoked via `python -m pythinker doctor` with the console script not on PATH
    return CheckResult(
        "warn",
        "pythinker",
        f"{__version__} (console script not on PATH)",
        fix="Run `uv tool update-shell` (uv), `pipx ensurepath` (pipx), "
        "or symlink the venv binary into a PATH directory.",
    )


def _check_config() -> CheckResult:
    try:
        from pythinker.config.loader import get_config_path, load_config
    except ImportError as e:
        return CheckResult("error", "Config", "cannot import config loader", fix=str(e))

    path = get_config_path()
    if not path.exists():
        return CheckResult(
            "error",
            "Config",
            f"missing at {path}",
            fix="Run `pythinker onboard` to create a default config.",
        )
    try:
        load_config(path)
    except Exception as e:  # noqa: BLE001 — surfacing any config error is the point
        return CheckResult(
            "error",
            "Config",
            f"{path} fails to load",
            fix=f"Fix the config or delete it and re-run `pythinker onboard`. Details: {e}",
        )
    return CheckResult("ok", "Config", str(path))


def _check_workspace() -> CheckResult:
    try:
        from pythinker.config.loader import load_config
    except ImportError:
        return CheckResult("error", "Workspace", "cannot import config loader")
    try:
        config = load_config()
    except Exception as e:  # noqa: BLE001
        return CheckResult("error", "Workspace", "config invalid", fix=str(e))
    workspace = config.workspace_path
    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return CheckResult(
            "error",
            "Workspace",
            f"{workspace} cannot be created",
            fix=f"Check filesystem permissions. ({e})",
        )
    probe = workspace / ".doctor-probe"
    try:
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        return CheckResult(
            "error",
            "Workspace",
            f"{workspace} not writable",
            fix=f"Fix permissions on the workspace directory. ({e})",
        )
    return CheckResult("ok", "Workspace", str(workspace))


def _check_default_model() -> CheckResult:
    try:
        from pythinker.config.loader import load_config

        config = load_config()
    except Exception:  # noqa: BLE001
        return CheckResult("warn", "Default model", "(skipped — config invalid)")
    model = config.agents.defaults.model
    if not model:
        return CheckResult(
            "error",
            "Default model",
            "not set",
            fix="Set agents.defaults.model in ~/.pythinker/config.json.",
        )
    return CheckResult("ok", "Default model", model)


def _check_default_provider_auth() -> list[CheckResult]:
    """Check auth status for the default provider (and warn on other OAuth providers)."""
    try:
        from pythinker.config.loader import load_config
        from pythinker.providers.registry import PROVIDERS

        config = load_config()
    except Exception:  # noqa: BLE001
        return [CheckResult("warn", "Provider auth", "(skipped — config invalid)")]

    model = (config.agents.defaults.model or "").lower()

    # Delegate to Config.get_provider_name() so doctor and the runtime agree on the
    # resolved provider for the same model (prefix wins, keyword match, etc.).
    resolved_name: str | None = None
    try:
        resolved_name = config.get_provider_name()
    except Exception:  # noqa: BLE001
        resolved_name = None

    default_spec = None
    if resolved_name:
        default_spec = next((s for s in PROVIDERS if s.name == resolved_name), None)

    # Runtime resolution above only returns a spec when credentials are present.
    # For OAuth providers that's "token on disk" — which is exactly what doctor
    # should be checking. So fall back to resolving by prefix / keyword so that
    # a missing token still reports as an auth error on the *right* provider.
    if default_spec is None:
        prefix = model.split("/", 1)[0] if "/" in model else ""
        prefix_name = prefix.replace("-", "_")
        for spec in PROVIDERS:
            if prefix_name and spec.name == prefix_name:
                default_spec = spec
                break
        if default_spec is None:
            for spec in PROVIDERS:
                for kw in spec.keywords:
                    kw_lc = kw.lower()
                    if kw_lc in model or kw_lc.replace("-", "_") in model.replace("-", "_"):
                        default_spec = spec
                        break
                if default_spec is not None:
                    break

    results: list[CheckResult] = []

    if default_spec is None:
        results.append(
            CheckResult(
                "warn",
                "Default provider",
                f"could not resolve from model {model!r}",
                fix="Set agents.defaults.provider explicitly in the config.",
            )
        )
        return results

    results.append(_auth_check_for_spec(default_spec, config, is_default=True))

    # Surface only *authenticated* secondary OAuth providers — unauthenticated
    # ones aren't a problem for the current config, so don't clutter the report.
    for spec in PROVIDERS:
        if spec is default_spec or not spec.is_oauth:
            continue
        token = _safe_token(spec)
        if token and token.access:
            results.append(CheckResult("ok", spec.label, "OAuth token present"))

    return results


def _auth_check_for_spec(spec, config, *, is_default: bool) -> CheckResult:
    label = f"{spec.label} (default)" if is_default else spec.label

    if spec.is_oauth:
        token = _safe_token(spec)
        if token and token.access:
            return CheckResult("ok", label, "OAuth token present")
        severity = "error" if is_default else "warn"
        hint_name = spec.name.replace("_", "-")
        return CheckResult(
            severity,
            label,
            "not authenticated",
            fix=f"pythinker provider login {hint_name}",
        )

    # API-key provider
    provider_cfg = getattr(config.providers, spec.name, None)
    if provider_cfg and getattr(provider_cfg, "api_key", None):
        return CheckResult("ok", label, "API key configured")

    if spec.is_local:
        base = getattr(provider_cfg, "api_base", None) if provider_cfg else None
        if base:
            return CheckResult("ok", label, f"api_base {base}")
        return CheckResult("ok", label, "(local provider — no key needed)")

    severity = "error" if is_default else "warn"
    env_hint = f"or set {spec.env_key}" if spec.env_key else ""
    fix = (
        f"Add an API key under providers.{spec.name}.apiKey in ~/.pythinker/config.json"
        f"{(' ' + env_hint) if env_hint else ''}."
    )
    return CheckResult(severity, label, "API key not set", fix=fix)


def _safe_token(spec):
    if not spec.is_oauth:
        return None
    try:
        from oauth_cli_kit.storage import FileTokenStorage

        storage = FileTokenStorage(
            token_filename=spec.token_filename or "oauth.json",
            app_name=spec.token_app_name or "oauth-cli-kit",
            import_codex_cli=(spec.name == "openai_codex"),
        )
        return storage.load()
    except Exception:  # noqa: BLE001 — doctor must never crash on a missing dep
        return None


def _check_browser() -> list[CheckResult]:
    try:
        from pythinker.config.loader import load_config

        config = load_config()
    except Exception:  # noqa: BLE001
        return [CheckResult("warn", "Browser", "(skipped — config invalid)")]

    browser = config.tools.web.browser
    if not config.tools.web.enable:
        return [CheckResult("ok", "Browser", "disabled (web tools disabled)")]
    if not browser.enable:
        return [CheckResult("ok", "Browser", "disabled")]

    results: list[CheckResult] = []
    if importlib.util.find_spec("playwright") is None:
        return [
            CheckResult(
                "error",
                "Browser",
                "Playwright package is not installed",
                fix="Upgrade or reinstall pythinker-ai, then retry `python -m playwright install chromium` if launch mode needs Chromium.",
            )
        ]

    results.append(CheckResult("ok", "Browser", f"enabled ({browser.mode})"))

    cdp_configured = browser.cdp_url.rstrip("/") != "http://127.0.0.1:9222"
    if browser.mode == "cdp" or (browser.mode == "auto" and cdp_configured):
        from pythinker.agent.browser.transport import cdp_healthcheck

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            ok, err = asyncio.run(cdp_healthcheck(browser.cdp_url))
            status = "ok" if ok else ("warn" if browser.mode == "auto" else "error")
            detail = (
                f"{browser.cdp_url} reachable"
                if ok
                else f"{browser.cdp_url} unreachable: {err}"
            )
            fix = (
                ""
                if ok or browser.mode == "auto"
                else "Start the configured pythinker-browser/Chromium service or set tools.web.browser.mode='launch'."
            )
            results.append(CheckResult(status, "Browser CDP", detail, fix=fix))
        else:
            # Doctor was invoked from inside a running event loop
            # (programmatic call from another async surface). Sync probing
            # would raise; surface that explicitly instead of crashing.
            results.append(
                CheckResult(
                    "warn",
                    "Browser CDP",
                    "skipped CDP probe (doctor invoked inside an event loop)",
                    fix="Run `pythinker doctor` from a fresh shell to probe CDP reachability.",
                )
            )

    if browser.mode == "cdp":
        return results

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            executable = browser.executable_path or pw.chromium.executable_path
    except Exception as e:  # noqa: BLE001
        results.append(
            CheckResult(
                "warn",
                "Browser Chromium",
                f"could not inspect Playwright Chromium ({e})",
                fix="Run `python -m playwright install chromium` if browser launch fails.",
            )
        )
        return results

    executable_path = Path(executable).expanduser()
    if executable_path.exists():
        results.append(CheckResult("ok", "Browser Chromium", str(executable_path)))
        return results

    if browser.executable_path:
        results.append(
            CheckResult(
                "error",
                "Browser Chromium",
                f"configured executable not found: {executable_path}",
                fix="Fix tools.web.browser.executablePath or remove it to use Playwright-managed Chromium.",
            )
        )
    else:
        severity = "warn" if browser.auto_provision else "error"
        detail = "not installed"
        if browser.auto_provision:
            detail += " (first browser use will try to install it)"
        results.append(
            CheckResult(
                severity,
                "Browser Chromium",
                detail,
                fix="Run `python -m playwright install chromium`.",
            )
        )
    return results


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _check_updates() -> CheckResult:
    """Surface PyPI update state inside ``pythinker doctor``."""
    try:
        from pythinker.utils.update import (
            check_for_update_sync,
            suggested_upgrade_command,
        )
    except Exception as e:  # noqa: BLE001
        return CheckResult("warn", "Updates", "(skipped — updater not loadable)", fix=str(e))

    try:
        info = check_for_update_sync()
    except Exception as e:  # noqa: BLE001
        return CheckResult("warn", "Updates", f"(skipped — {e})")

    if info.is_yanked and info.latest:
        return CheckResult(
            "error",
            "Updates",
            f"{info.current} was yanked (latest: {info.latest})",
            fix=f"Run: {suggested_upgrade_command(info.install_method)}",
        )
    if info.update_available and info.latest:
        return CheckResult(
            "warn",
            "Updates",
            f"{info.current} (latest: {info.latest})",
            fix=f"Run: {suggested_upgrade_command(info.install_method)}",
        )
    if not info.checked_ok:
        msg = info.error_message or info.error_kind or "unknown"
        return CheckResult("warn", "Updates", f"could not reach PyPI ({msg})")
    return CheckResult("ok", "Updates", f"{info.current} (up to date)")


_SECTIONS: list[tuple[str, Iterable[Callable[[], CheckResult | list[CheckResult]]]]] = [
    ("Environment", (_check_python_version, _check_install_location)),
    ("Configuration", (_check_config, _check_workspace, _check_default_model)),
    ("Providers", (_check_default_provider_auth,)),
    ("Tools", (_check_browser,)),
    ("Updates", (_check_updates,)),
]


def run(*, non_interactive: bool = False) -> int:
    """Run every check; print results; return an exit code (0/1/2).

    ``non_interactive`` switches output to plain ASCII without Rich markup so
    it pipes cleanly to logs, tickets, and CI artefacts.
    """
    if non_interactive:
        console = Console(
            file=sys.stdout,
            highlight=False,
            force_terminal=False,
            no_color=True,
            markup=False,
            emoji=False,
        )
        console.print("pythinker doctor\n")
    else:
        console = Console(file=sys.stdout, highlight=False)
        console.print(f"{__logo__} pythinker doctor\n")

    errors = 0
    warnings = 0

    for section, checks in _SECTIONS:
        if non_interactive:
            console.print(f"{section}")
        else:
            console.print(f"[bold cyan]{section}[/bold cyan]")
        for fn in checks:
            raw = fn()
            results = raw if isinstance(raw, list) else [raw]
            for r in results:
                _print_result(console, r, non_interactive=non_interactive)
                if r.status == "error":
                    errors += 1
                elif r.status == "warn":
                    warnings += 1
        console.print()

    if errors:
        if non_interactive:
            tail = f"{errors} error{'s' if errors != 1 else ''}"
            if warnings:
                tail += f", {warnings} warning{'s' if warnings != 1 else ''}"
            console.print(tail)
        else:
            summary = f"[red]{errors} error{'s' if errors != 1 else ''}[/red]"
            if warnings:
                summary += f" · [yellow]{warnings} warning{'s' if warnings != 1 else ''}[/yellow]"
            console.print(summary)
        return 1
    if warnings:
        msg = f"{warnings} warning{'s' if warnings != 1 else ''}"
        if non_interactive:
            console.print(msg)
        else:
            console.print(f"[yellow]{msg}[/yellow]")
        return 2
    if non_interactive:
        console.print("All checks passed.")
    else:
        console.print("[green]All checks passed.[/green]")
    return 0


_GLYPHS_RICH = {
    "ok": "[green]✓[/green]",
    "warn": "[yellow]○[/yellow]",
    "error": "[red]✗[/red]",
}
_GLYPHS_PLAIN = {"ok": "[OK]  ", "warn": "[WARN]", "error": "[FAIL]"}


def _print_result(console: Console, r: CheckResult, *, non_interactive: bool) -> None:
    if non_interactive:
        glyph = _GLYPHS_PLAIN[r.status]
        line = f"  {glyph} {r.label}"
        if r.detail:
            line += f": {r.detail}"
        console.print(line)
        if r.fix and r.status != "ok":
            console.print(f"       fix: {r.fix}")
        return

    glyph = _GLYPHS_RICH[r.status]
    line = f"  {glyph} {r.label}"
    if r.detail:
        line += f": {r.detail}" if r.status != "ok" else f"  [dim]{r.detail}[/dim]"
    console.print(line)
    if r.fix and r.status != "ok":
        console.print(f"      [dim]→ {r.fix}[/dim]")
