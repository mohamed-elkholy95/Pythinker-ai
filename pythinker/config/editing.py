"""Safe config read/edit helpers shared by CLI and admin surfaces."""

from __future__ import annotations

import json
import os
import re
import shutil
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from pydantic.alias_generators import to_camel

from pythinker.config.loader import save_config
from pythinker.config.schema import Config

SECRET_PLACEHOLDER = "********"

_SECRET_NAME_RE = re.compile(r"(api[_-]?key|token|secret|password|credential|oauth)", re.I)
_ENV_REF_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


class ConfigEditError(ValueError):
    """Raised when a config path cannot be safely read or edited."""


@dataclass(frozen=True)
class ConfigEditResult:
    path: str
    value: Any
    restart_required: bool = True


@dataclass(frozen=True)
class ConfigBackupEntry:
    id: str
    path: Path
    mtime_ms: int
    size_bytes: int
    source: str
    kind: str
    summary: dict[str, object]


def _camel_to_snake(value: str) -> str:
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).replace("-", "_").lower()


def _field_lookup(cursor: BaseModel, candidates: tuple[str, ...]) -> str | None:
    fields = type(cursor).model_fields
    aliases = {info.alias: name for name, info in fields.items() if info.alias}
    for cand in candidates:
        if cand in fields:
            return cand
        if cand in aliases:
            return aliases[cand]
    return None


def _resolve_segment(cursor: Any, raw: str, *, dotted: str, depth: int) -> Any:
    snake, camel = raw, to_camel(raw)
    if isinstance(cursor, BaseModel):
        attr = _field_lookup(cursor, (snake, camel))
        if attr is not None:
            return getattr(cursor, attr)
    if isinstance(cursor, dict):
        for cand in (raw, snake, camel):
            if cand in cursor:
                return cursor[cand]
    raise ConfigEditError(
        f"unknown config segment {raw!r} at position {depth} of {dotted!r}"
    )


def _walk_config_path(root: Config, dotted: str) -> tuple[Any, str]:
    parts = [p for p in dotted.split(".") if p]
    if not parts:
        raise ConfigEditError("config path must not be empty")
    cursor: Any = root
    for i, raw in enumerate(parts[:-1]):
        cursor = _resolve_segment(cursor, raw, dotted=dotted, depth=i)
    return cursor, parts[-1]


def _read_leaf(cursor: Any, leaf: str) -> Any:
    snake, camel = leaf, to_camel(leaf)
    if isinstance(cursor, BaseModel):
        attr = _field_lookup(cursor, (snake, camel))
        if attr is not None:
            return getattr(cursor, attr)
    if isinstance(cursor, dict):
        for cand in (leaf, snake, camel):
            if cand in cursor:
                return cursor[cand]
    raise ConfigEditError(f"unknown leaf field {leaf!r}")


def read_config_value(config: Config, dotted: str) -> Any:
    parent, leaf = _walk_config_path(config, dotted)
    return _read_leaf(parent, leaf)


def coerce_config_value(raw: Any) -> Any:
    """Best-effort JSON string coercion, matching the CLI config command."""

    if not isinstance(raw, str) or raw == "":
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def _validate_config(config: Config) -> None:
    try:
        type(config).model_validate(config.model_dump(mode="json", by_alias=True))
    except ValidationError as exc:
        first = exc.errors()[0]
        raise ConfigEditError(str(first.get("msg", "invalid config"))) from exc


def set_config_value(config: Config, dotted: str, value: Any) -> ConfigEditResult:
    parent, leaf = _walk_config_path(config, dotted)
    coerced = coerce_config_value(value)
    if isinstance(parent, BaseModel):
        target = _field_lookup(parent, (leaf, to_camel(leaf)))
        if target is None:
            raise ConfigEditError(f"unknown field {leaf!r}")
        old_value = getattr(parent, target)
        try:
            setattr(parent, target, coerced)
            _validate_config(config)
        except Exception:
            setattr(parent, target, old_value)
            raise
    elif isinstance(parent, dict):
        old_missing = object()
        old_value = parent.get(leaf, old_missing)
        try:
            parent[leaf] = coerced
            _validate_config(config)
        except Exception:
            if old_value is old_missing:
                parent.pop(leaf, None)
            else:
                parent[leaf] = old_value
            raise
    else:
        raise ConfigEditError(f"cannot set on {type(parent).__name__}")
    return ConfigEditResult(path=dotted, value=coerced)


def _field_default(parent: BaseModel, attr: str) -> Any:
    info = type(parent).model_fields[attr]
    return info.get_default(call_default_factory=True)


def unset_config_value(config: Config, dotted: str) -> ConfigEditResult:
    parent, leaf = _walk_config_path(config, dotted)
    if isinstance(parent, BaseModel):
        target = _field_lookup(parent, (leaf, to_camel(leaf)))
        if target is None:
            raise ConfigEditError(f"unknown field {leaf!r}")
        old_value = getattr(parent, target)
        try:
            value = _field_default(parent, target)
            setattr(parent, target, value)
            _validate_config(config)
        except Exception:
            setattr(parent, target, old_value)
            raise
    elif isinstance(parent, dict):
        for cand in (leaf, to_camel(leaf)):
            parent.pop(cand, None)
        value = None
        _validate_config(config)
    else:
        raise ConfigEditError(f"cannot unset on {type(parent).__name__}")
    return ConfigEditResult(path=dotted, value=value)


def _is_secret_path(path: str, value: Any) -> bool:
    parts = path.split(".")
    last = parts[-1] if parts else ""
    if last in {"extra_headers", "headers"} and value:
        return True
    return any(_SECRET_NAME_RE.search(part) for part in parts)


def redacted_config(config: Config) -> dict[str, Any]:
    data = config.model_dump(mode="json", by_alias=True)
    secret_paths: list[str] = []

    def visit(value: Any, path: list[str]) -> Any:
        canonical = ".".join(_camel_to_snake(part) for part in path)
        if path and _is_secret_path(canonical, value):
            if value in (None, "", {}, []):
                return value
            secret_paths.append(canonical)
            return SECRET_PLACEHOLDER
        if isinstance(value, dict):
            return {key: visit(child, [*path, key]) for key, child in value.items()}
        if isinstance(value, list):
            return [visit(child, [*path, str(i)]) for i, child in enumerate(value)]
        return value

    return {"config": visit(data, []), "secret_paths": sorted(set(secret_paths))}


def collect_env_references(
    raw_config: dict[str, object],
    secret_paths: set[str],
) -> dict[str, dict[str, object]]:
    found: dict[str, dict[str, object]] = {}

    def walk(value: object, path: str) -> None:
        if isinstance(value, str):
            match = _ENV_REF_RE.match(value)
            if match:
                found[path] = {"env_var": match.group(1), "is_secret": path in secret_paths}
            return
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{_camel_to_snake(str(key))}" if path else _camel_to_snake(str(key))
                walk(child, child_path)
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}.{index}" if path else str(index))

    walk(raw_config, "")
    return found


def collect_field_defaults(schema: dict[str, object]) -> dict[str, object]:
    defaults: dict[str, object] = {}
    definitions = schema.get("$defs")
    defs = definitions if isinstance(definitions, dict) else {}

    def resolve(node: dict[str, object]) -> dict[str, object]:
        ref = node.get("$ref")
        if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
            return node
        target = defs.get(ref.rsplit("/", 1)[-1])
        return target if isinstance(target, dict) else node

    def walk(node: dict[str, object], path: str) -> None:
        node = resolve(node)
        if "default" in node and path:
            defaults[path] = node["default"]
        properties = node.get("properties")
        if isinstance(properties, dict):
            for key, child in properties.items():
                if isinstance(child, dict):
                    child_path = f"{path}.{_camel_to_snake(str(key))}" if path else _camel_to_snake(str(key))
                    walk(child, child_path)

    walk(schema, "")
    return defaults


def save_config_with_backup(config: Config, config_path: Path) -> Path | None:
    backup: Path | None = None
    if config_path.exists():
        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        backup = config_path.with_name(f"{config_path.name}.bak.{stamp}")
        shutil.copy2(config_path, backup)
    save_config(config, config_path)
    return backup


def _backup_id(path: Path, mtime_ns: int, size: int) -> str:
    payload = f"{path}\0{mtime_ns}\0{size}".encode()
    return urlsafe_b64encode(sha256(payload).digest()[:18]).decode().rstrip("=")


def _summarize_config_backup(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        config = Config.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        return {"valid": False, "error": str(exc)}
    return {
        "valid": True,
        "model": config.agents.defaults.model,
        "provider": config.get_provider_name(config.agents.defaults.model),
    }


def list_config_backups(config_path: Path, *, limit: int = 5) -> list[ConfigBackupEntry]:
    roots = [
        (config_path.parent, "sibling", "pre-edit-bak", f"{config_path.name}.bak.*"),
        (config_path.parent / "backups", "backup", "manual-snapshot", "config.*.json"),
        (
            config_path.parent / "backups",
            "pre_restore",
            "pre-restore-safety",
            "config.pre-restore.*.json",
        ),
    ]
    entries: dict[Path, ConfigBackupEntry] = {}
    for root, source, kind, pattern in roots:
        if not root.exists():
            continue
        for candidate in root.glob(pattern):
            if candidate.is_symlink():
                continue
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            if not resolved.is_file():
                continue
            if resolved.parent not in {config_path.parent, config_path.parent / "backups"}:
                continue
            stat = resolved.stat()
            entries[resolved] = ConfigBackupEntry(
                id=_backup_id(resolved, stat.st_mtime_ns, stat.st_size),
                path=resolved,
                mtime_ms=stat.st_mtime_ns // 1_000_000,
                size_bytes=stat.st_size,
                source=source,
                kind=kind,
                summary=_summarize_config_backup(resolved),
            )
    return sorted(entries.values(), key=lambda entry: entry.mtime_ms, reverse=True)[:limit]


def _write_pre_restore_backup(config_path: Path) -> Path:
    backups_dir = config_path.parent / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    safety = backups_dir / f"config.pre-restore.{stamp}.json"
    shutil.copy2(config_path, safety)
    return safety


def restore_config_backup(config_path: Path, backup_id: str) -> None:
    backups = {entry.id: entry for entry in list_config_backups(config_path, limit=100)}
    entry = backups.get(backup_id)
    if entry is None:
        raise ConfigEditError("Unknown backup id")
    try:
        payload = json.loads(entry.path.read_text(encoding="utf-8"))
        Config.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise ConfigEditError("Backup is not a valid config") from exc

    _write_pre_restore_backup(config_path)
    tmp = config_path.with_suffix(".json.restore.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, config_path)
    finally:
        if tmp.exists():
            tmp.unlink()
