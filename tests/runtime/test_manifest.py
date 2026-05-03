"""Tests for AgentManifest schema + AgentRegistry directory loader."""

import json
from pathlib import Path

from pythinker.runtime.manifest import AgentManifest, AgentRegistry


def test_manifest_round_trip_via_pydantic():
    raw = {
        "id": "research",
        "name": "Research Agent",
        "version": "0.1.0",
        "model": "openai-codex/gpt-5.5",
        "owner": "moelkholy@example.com",
        "lifecycle": "active",
        "allowed_tools": ["read_file", "grep", "web_search"],
        "memory_scope": "session",
        "enabled_skills": ["weather"],
    }
    m = AgentManifest.model_validate(raw)
    assert m.id == "research"
    assert m.allowed_tools == ["read_file", "grep", "web_search"]
    assert m.lifecycle == "active"


def test_registry_loads_manifests_from_directory(tmp_path: Path):
    (tmp_path / "research.json").write_text(json.dumps({
        "id": "research", "name": "R", "version": "0.1.0",
        "model": "m", "owner": "o", "allowed_tools": ["read_file"],
    }), encoding="utf-8")
    (tmp_path / "ops.json").write_text(json.dumps({
        "id": "ops", "name": "O", "version": "0.1.0",
        "model": "m", "owner": "o", "allowed_tools": ["exec"],
    }), encoding="utf-8")
    reg = AgentRegistry.load_dir(tmp_path)
    assert sorted(reg.ids()) == ["ops", "research"]
    assert reg.get("research").allowed_tools == ["read_file"]
    assert reg.get("ops").allowed_tools == ["exec"]


def test_registry_skips_invalid_manifests(tmp_path: Path):
    (tmp_path / "good.json").write_text(json.dumps({
        "id": "good", "name": "G", "version": "0.1.0",
        "model": "m", "owner": "o", "allowed_tools": [],
    }), encoding="utf-8")
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "missing.json").write_text(json.dumps({"id": "missing"}), encoding="utf-8")
    reg = AgentRegistry.load_dir(tmp_path)
    assert reg.ids() == ["good"]


def test_registry_get_unknown_returns_default_when_set(tmp_path: Path):
    reg = AgentRegistry.load_dir(tmp_path)  # empty dir
    assert reg.get("unknown") is None
    fallback = AgentManifest(
        id="default", name="Default", version="0.0.0",
        model="m", owner="-", allowed_tools=["*"],
    )
    reg2 = AgentRegistry.load_dir(tmp_path, default=fallback)
    assert reg2.get("anything").id == "default"


def test_registry_load_dir_handles_missing_directory(tmp_path: Path):
    reg = AgentRegistry.load_dir(tmp_path / "does-not-exist")
    assert reg.ids() == []


def test_manifest_omitting_allowed_tools_defaults_to_empty():
    """A manifest without `allowedTools` is a deny-everything agent, not allow-all.

    This guards the plan's deny-by-default invariant against the obvious
    pitfall of operators omitting the field and accidentally getting
    unrestricted tool access.
    """
    raw = {
        "id": "tight",
        "name": "Tight",
        "version": "0.1.0",
        "model": "m",
        "owner": "o",
        # NOTE: allowedTools intentionally omitted.
    }
    m = AgentManifest.model_validate(raw)
    assert m.allowed_tools == []
    assert m.lifecycle == "active"  # active by default — but with no tools


def test_registry_warns_and_skips_duplicate_manifest_id(tmp_path: Path, caplog):
    """Two manifests sharing an id must not silently overwrite — first wins, second is logged."""
    import logging

    (tmp_path / "a-first.json").write_text(json.dumps({
        "id": "shared", "name": "A", "version": "0.1.0",
        "model": "m", "owner": "o", "allowed_tools": ["read_file"],
    }), encoding="utf-8")
    (tmp_path / "b-second.json").write_text(json.dumps({
        "id": "shared", "name": "B", "version": "0.2.0",
        "model": "m", "owner": "o", "allowed_tools": ["exec"],
    }), encoding="utf-8")

    # AgentRegistry uses loguru; redirect to stdlib so caplog captures it.
    # Other tests in the suite call commands.serve(), which executes
    # `logger.disable("pythinker")` and silences loguru globally — re-enable
    # explicitly so this test is order-independent.
    from loguru import logger as _logger
    _logger.enable("pythinker")
    handler_id = _logger.add(lambda m: logging.getLogger("loguru").warning(m.record["message"]), level="WARNING")
    try:
        with caplog.at_level(logging.WARNING, logger="loguru"):
            reg = AgentRegistry.load_dir(tmp_path)
    finally:
        _logger.remove(handler_id)

    # Sorted glob order means "a-first.json" loads first; the duplicate is skipped.
    assert reg.get("shared").allowed_tools == ["read_file"]
    assert any("duplicate manifest id" in rec.getMessage() for rec in caplog.records)


def test_manifest_explicit_wildcard_must_be_spelled_out():
    """Operators who want allow-all must say so explicitly. No implicit wildcard."""
    raw = {
        "id": "loose", "name": "Loose", "version": "0.1.0",
        "model": "m", "owner": "o", "allowedTools": ["*"],
    }
    m = AgentManifest.model_validate(raw)
    assert m.allowed_tools == ["*"]
