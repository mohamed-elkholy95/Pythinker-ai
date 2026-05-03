"""Internal helper: build the runtime services Tasks 8/8b/8c need.

Shared between `Pythinker.from_config`, `pythinker serve`, `pythinker agent`,
and `_run_gateway` so the same telemetry-sink-installation + PolicyService-
construction logic lives in one place.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from pythinker.runtime.policy import PolicyService
from pythinker.runtime.telemetry import (
    CompositeSink,
    JSONLSink,
    LoggingSink,
    set_sink,
)

if TYPE_CHECKING:
    from pythinker.config.schema import Config


def install_telemetry_sink(config: "Config") -> None:
    """Install the process-wide telemetry sink based on config.runtime.

    Captures the previously-installed sink and closes it. Without this,
    long-lived processes that rebuild Config (e.g. config reload, repeated
    `Pythinker.from_config(...)` in the same interpreter) would leak any
    open file handle a previous JSONLSink owned and silently clobber
    routing.
    """
    rt = config.runtime
    new_sink = None
    if rt.telemetry_sink == "log":
        new_sink = LoggingSink()
    elif rt.telemetry_sink == "jsonl" and rt.telemetry_jsonl_path:
        new_sink = JSONLSink(Path(rt.telemetry_jsonl_path))
    elif rt.telemetry_sink == "both" and rt.telemetry_jsonl_path:
        new_sink = CompositeSink([LoggingSink(), JSONLSink(Path(rt.telemetry_jsonl_path))])
    # else: "off" or jsonl/both without a path -> no sink installed (new_sink stays None).
    previous = set_sink(new_sink)
    if previous is not None:
        try:
            previous.close()
        except Exception:
            logger.exception("telemetry sink close-on-replace failed")


def build_policy(config: "Config") -> PolicyService:
    """Build a PolicyService from config.runtime, warning on the deny-default footgun."""
    rt = config.runtime
    if rt.policy_enabled and not rt.manifests_dir and rt.policy_migration_mode is None:
        logger.warning(
            "runtime.policy_enabled=true but no manifests_dir and no migration_mode — "
            "every tool call will be denied. Set runtime.manifestsDir or runtime.policyMigrationMode."
        )
    return PolicyService(
        enabled=rt.policy_enabled,
        allowed_tools={},  # filled by Task 10b wiring when manifests_dir is set
        blocked_senders=set(rt.blocked_senders) if rt.blocked_senders else None,
        max_recursion_depth=rt.max_subagent_recursion_depth,
        migration_mode=rt.policy_migration_mode,
    )
