import json
import os
from pathlib import Path

import pytest

from pythinker.config.editing import (
    ConfigEditError,
    list_config_backups,
    restore_config_backup,
)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _mtime(path: Path, ns: int) -> None:
    os.utime(path, ns=(ns, ns))


def test_list_config_backups_enumerates_supported_locations_with_opaque_ids(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_json(config_path, {"agents": {"defaults": {"model": "current/model"}}})
    sibling = tmp_path / "config.json.bak.20260502120000"
    backup = tmp_path / "backups" / "config.20260502115900.json"
    pre_restore = tmp_path / "backups" / "config.pre-restore.20260502115800.json"
    _write_json(sibling, {"agents": {"defaults": {"model": "sibling/model"}}})
    _write_json(backup, {"agents": {"defaults": {"model": "backup/model"}}})
    _write_json(pre_restore, {"agents": {"defaults": {"model": "pre-restore/model"}}})
    _mtime(pre_restore, 1_000_000)
    _mtime(backup, 2_000_000)
    _mtime(sibling, 3_000_000)

    backups = list_config_backups(config_path)

    assert [entry.source for entry in backups] == ["sibling", "backup", "pre_restore"]
    assert [entry.kind for entry in backups] == ["pre-edit-bak", "manual-snapshot", "pre-restore-safety"]
    assert [entry.summary["model"] for entry in backups] == ["sibling/model", "backup/model", "pre-restore/model"]
    assert all("/" not in entry.id for entry in backups)
    assert all(entry.size_bytes > 0 for entry in backups)


def test_list_config_backups_rejects_symlinks_and_non_regular_files(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_json(config_path, {})
    outside = tmp_path.parent / "outside-config.json"
    _write_json(outside, {"agents": {"defaults": {"model": "outside/model"}}})
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    (backups_dir / "config.20260502120000.json").symlink_to(outside)
    (backups_dir / "config.20260502120100.json").mkdir()

    assert list_config_backups(config_path) == []


def test_restore_config_backup_validates_schema_and_preserves_current_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_json(config_path, {"agents": {"defaults": {"model": "current/model"}}})
    invalid = tmp_path / "backups" / "config.20260502120000.json"
    _write_json(invalid, {"agents": {"defaults": {"dream": {"intervalH": 0}}}})
    backup_id = list_config_backups(config_path)[0].id

    with pytest.raises(ConfigEditError):
        restore_config_backup(config_path, backup_id)

    assert json.loads(config_path.read_text(encoding="utf-8"))["agents"]["defaults"]["model"] == "current/model"


def test_restore_config_backup_writes_pre_restore_safety_backup_before_replace(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_json(config_path, {"agents": {"defaults": {"model": "current/model"}}})
    backup = tmp_path / "backups" / "config.20260502120000.json"
    _write_json(backup, {"agents": {"defaults": {"model": "restored/model"}}})
    backup_id = list_config_backups(config_path)[0].id

    restore_config_backup(config_path, backup_id)

    assert json.loads(config_path.read_text(encoding="utf-8"))["agents"]["defaults"]["model"] == "restored/model"
    safety = sorted((tmp_path / "backups").glob("config.pre-restore.*.json"))
    assert len(safety) == 1
    assert json.loads(safety[0].read_text(encoding="utf-8"))["agents"]["defaults"]["model"] == "current/model"
