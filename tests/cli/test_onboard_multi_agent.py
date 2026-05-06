"""Phase 2 PR-3 — wizard multi-agent flow.

Three flows from the plan's acceptance gate:

  * Single-config install: ``_step_agent_id`` short-circuits to ``skip``
    so the rest of the wizard is byte-identical to today's flow (no new
    step renders).
  * Multi-agent install: the step renders, the chosen id is plumbed into
    ``_WizardContext.agent_id`` and ``set_config_path()`` overrides the
    loader for the rest of the wizard.
  * Pre-save diff title prefixes the agent id when non-default.
"""

from __future__ import annotations

import pytest

from pythinker.cli.onboard_steps.agent_id import _step_agent_id
from pythinker.cli.onboard_types import _WizardContext
from pythinker.config.schema import Config


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Pin ``Path.home()`` cross-platform; see ``tests/config/test_agent_paths.py``."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOMEDRIVE", tmp_path.drive or "")
    monkeypatch.setenv("HOMEPATH", str(tmp_path).removeprefix(tmp_path.drive or ""))
    monkeypatch.delenv("PYTHINKER_AGENT_ID", raising=False)
    from pythinker.config import loader

    loader._current_config_path = None  # noqa: SLF001
    yield


def test_step_skips_when_no_agents_dir(tmp_path):
    """Single-config install: step short-circuits, ctx.agent_id stays None."""
    ctx = _WizardContext(draft=Config())
    result = _step_agent_id(ctx)
    assert result.status == "skip"
    assert ctx.agent_id is None


def test_step_skips_when_agents_dir_empty(tmp_path):
    """Empty agents/ dir is treated like single-config — no behavior change."""
    (tmp_path / ".pythinker" / "agents").mkdir(parents=True)
    ctx = _WizardContext(draft=Config())
    result = _step_agent_id(ctx)
    assert result.status == "skip"


def test_step_non_interactive_uses_resolved_active(tmp_path, monkeypatch):
    """Non-interactive runs honour env-var resolution silently."""
    agents_root = tmp_path / ".pythinker" / "agents"
    (agents_root / "research").mkdir(parents=True)
    (agents_root / "research" / "config.json").write_text("{}")
    monkeypatch.setenv("PYTHINKER_AGENT_ID", "research")

    ctx = _WizardContext(draft=Config(), non_interactive=True)
    result = _step_agent_id(ctx)
    assert result.status == "continue"
    assert ctx.agent_id == "research"


def test_step_interactive_use_current(tmp_path, monkeypatch):
    """Picking 'Use current' plumbs the resolved active agent through."""
    agents_root = tmp_path / ".pythinker" / "agents"
    (agents_root / "default").mkdir(parents=True)
    (agents_root / "default" / "config.json").write_text("{}")

    ctx = _WizardContext(draft=Config())
    with monkeypatch.context() as m:
        m.setattr(
            "pythinker.cli.onboard_views.clack.select",
            lambda *a, **kw: "__use_current__",
        )
        result = _step_agent_id(ctx)
    assert result.status == "continue"
    assert ctx.agent_id == "default"


def test_step_interactive_pick_different(tmp_path, monkeypatch):
    """Picking 'Pick a different agent' opens a sub-picker over existing ids."""
    agents_root = tmp_path / ".pythinker" / "agents"
    for name in ("research", "coding"):
        (agents_root / name).mkdir(parents=True)
        (agents_root / name / "config.json").write_text("{}")

    ctx = _WizardContext(draft=Config())
    picks = iter(["__pick__", "coding"])
    with monkeypatch.context() as m:
        m.setattr(
            "pythinker.cli.onboard_views.clack.select",
            lambda *a, **kw: next(picks),
        )
        result = _step_agent_id(ctx)
    assert result.status == "continue"
    assert ctx.agent_id == "coding"


def test_step_interactive_create_scaffolds_dir(tmp_path, monkeypatch):
    """Picking 'Create' prompts for an id and creates the dir."""
    (tmp_path / ".pythinker" / "agents" / "research").mkdir(parents=True)
    (tmp_path / ".pythinker" / "agents" / "research" / "config.json").write_text("{}")

    ctx = _WizardContext(draft=Config())
    with monkeypatch.context() as m:
        m.setattr(
            "pythinker.cli.onboard_views.clack.select",
            lambda *a, **kw: "__create__",
        )
        m.setattr(
            "pythinker.cli.onboard_views.clack.text",
            lambda *a, **kw: "writing",
        )
        result = _step_agent_id(ctx)

    assert result.status == "continue"
    assert ctx.agent_id == "writing"
    new_dir = tmp_path / ".pythinker" / "agents" / "writing"
    assert new_dir.is_dir()
    assert (new_dir / "config.json").read_text() == "{}\n"
    assert (new_dir / "workspace").is_dir()


def test_step_create_invalid_id_returns_back(tmp_path, monkeypatch):
    """Invalid ids (path-separator etc.) round-trip via ``back``."""
    (tmp_path / ".pythinker" / "agents" / "research").mkdir(parents=True)
    (tmp_path / ".pythinker" / "agents" / "research" / "config.json").write_text("{}")

    ctx = _WizardContext(draft=Config())
    with monkeypatch.context() as m:
        m.setattr(
            "pythinker.cli.onboard_views.clack.select",
            lambda *a, **kw: "__create__",
        )
        m.setattr(
            "pythinker.cli.onboard_views.clack.text",
            lambda *a, **kw: "bad/id",
        )
        result = _step_agent_id(ctx)

    assert result.status == "back"


def test_step_sets_config_path_override(tmp_path, monkeypatch):
    """After picking an agent, get_config_path() resolves to its config.json."""
    from pythinker.config.loader import get_config_path

    (tmp_path / ".pythinker" / "agents" / "research").mkdir(parents=True)
    (tmp_path / ".pythinker" / "agents" / "research" / "config.json").write_text("{}")

    ctx = _WizardContext(draft=Config())
    with monkeypatch.context() as m:
        m.setattr(
            "pythinker.cli.onboard_views.clack.select",
            lambda *a, **kw: "__use_current__",
        )
        m.setenv("PYTHINKER_AGENT_ID", "research")
        result = _step_agent_id(ctx)

    assert result.status == "continue"
    assert get_config_path() == tmp_path / ".pythinker" / "agents" / "research" / "config.json"


def test_pre_save_diff_title_includes_agent_id(monkeypatch):
    """``render_pre_save_diff(..., agent_id=<x>)`` prefixes the panel title."""
    from pythinker.cli.onboard_views import summary

    captured: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        "pythinker.cli.onboard_views.clack.note",
        lambda title, lines: captured.append((title, list(lines))),
    )
    monkeypatch.setattr("pythinker.cli.onboard_views.clack.bar_break", lambda: None)

    summary.render_pre_save_diff(None, Config(), agent_id="research")
    assert captured
    title, _ = captured[0]
    assert "[research]" in title


def test_pre_save_diff_title_omits_default_agent_id(monkeypatch):
    """``agent_id="default"`` is treated as legacy and does not prefix the title."""
    from pythinker.cli.onboard_views import summary

    captured: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        "pythinker.cli.onboard_views.clack.note",
        lambda title, lines: captured.append((title, list(lines))),
    )
    monkeypatch.setattr("pythinker.cli.onboard_views.clack.bar_break", lambda: None)

    summary.render_pre_save_diff(None, Config(), agent_id="default")
    title, _ = captured[0]
    assert "[default]" not in title


def test_pre_save_diff_unchanged_signature_works(monkeypatch):
    """The original 2-arg signature still works (back-compat)."""
    from pythinker.cli.onboard_views import summary

    captured: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        "pythinker.cli.onboard_views.clack.note",
        lambda title, lines: captured.append((title, list(lines))),
    )
    monkeypatch.setattr("pythinker.cli.onboard_views.clack.bar_break", lambda: None)

    summary.render_pre_save_diff(None, Config())
    assert captured
    title, _ = captured[0]
    assert title == "Changes since last save"
