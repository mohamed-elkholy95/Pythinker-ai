"""Versioned local agent definitions.

A manifest is a JSON file describing one agent: which model, which tools,
which skills, what memory scope, who owns it, what state it's in. The
registry loads a directory of these and exposes `get(id)`.

This is the smallest useful form of an agent registry. No HTTP API, no
signing, no remote sync. Operators drop JSON files into a directory and
set `runtime.manifests_dir` in config.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class AgentManifest(BaseModel):
    """One agent definition. Camel/snake-case both accepted.

    `allowed_tools` is REQUIRED and defaults to an empty list — never `["*"]`.
    A manifest that omits `allowedTools` produces a deny-everything agent,
    matching the plan's deny-by-default invariant. Operators who want
    unrestricted access for a specific agent must spell out `["*"]` so the
    grant is intentional and reviewable in source control.

    Uses Pydantic 2.11+'s `validate_by_name`/`validate_by_alias` pair instead
    of the deprecated `populate_by_name=True`. The existing project-wide
    `Base` (in `pythinker/config/schema.py`) still uses `populate_by_name`
    for backward compatibility — new code should adopt the modern config.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
    )

    id: str
    name: str
    version: str
    model: str
    owner: str
    lifecycle: Literal["draft", "active", "deprecated", "retired"] = "active"
    allowed_tools: list[str] = Field(default_factory=list)
    memory_scope: Literal["none", "session", "user", "tenant"] = "session"
    enabled_skills: list[str] = Field(default_factory=list)


class AgentRegistry:
    """Read-only in-memory registry built from a directory of JSON manifests."""

    def __init__(self, manifests: dict[str, AgentManifest], default: AgentManifest | None = None):
        self._manifests = manifests
        self._default = default

    @classmethod
    def load_dir(cls, directory: Path, *, default: AgentManifest | None = None) -> "AgentRegistry":
        manifests: dict[str, AgentManifest] = {}
        directory = Path(directory)
        if not directory.is_dir():
            return cls(manifests, default=default)
        seen_paths: dict[str, str] = {}
        for path in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                manifest = AgentManifest.model_validate(payload)
            except Exception as exc:
                logger.warning("AgentRegistry: skipping {} ({})", path.name, exc)
                continue
            if manifest.id in manifests:
                # Two manifests with the same id silently overwriting
                # would change policy behaviour by sort order. Log + skip
                # the later file so the operator sees the conflict.
                logger.warning(
                    "AgentRegistry: duplicate manifest id {!r} in {} (already loaded from {}) — skipping",
                    manifest.id, path.name, seen_paths[manifest.id],
                )
                continue
            manifests[manifest.id] = manifest
            seen_paths[manifest.id] = path.name
        return cls(manifests, default=default)

    def ids(self) -> list[str]:
        return sorted(self._manifests.keys())

    def get(self, agent_id: str) -> AgentManifest | None:
        manifest = self._manifests.get(agent_id)
        if manifest is not None:
            return manifest
        return self._default
