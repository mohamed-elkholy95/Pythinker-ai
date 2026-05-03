from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from pythinker.bus.events import InboundMessage
from pythinker.command.builtin import cmd_dream, cmd_dream_log, cmd_dream_restore
from pythinker.command.router import CommandContext
from pythinker.runtime.context import RequestContext
from pythinker.utils.gitstore import CommitInfo


class _FakeStore:
    def __init__(self, git, last_dream_cursor: int = 1):
        self.git = git
        self._last_dream_cursor = last_dream_cursor

    def get_last_dream_cursor(self) -> int:
        return self._last_dream_cursor


class _FakeGit:
    def __init__(
        self,
        *,
        initialized: bool = True,
        commits: list[CommitInfo] | None = None,
        diff_map: dict[str, tuple[CommitInfo, str] | None] | None = None,
        revert_result: str | None = None,
    ):
        self._initialized = initialized
        self._commits = commits or []
        self._diff_map = diff_map or {}
        self._revert_result = revert_result

    def is_initialized(self) -> bool:
        return self._initialized

    def log(self, max_entries: int = 20) -> list[CommitInfo]:
        return self._commits[:max_entries]

    def show_commit_diff(self, sha: str, max_entries: int = 20):
        return self._diff_map.get(sha)

    def revert(self, sha: str) -> str | None:
        return self._revert_result


def _make_ctx(raw: str, git: _FakeGit, *, args: str = "", last_dream_cursor: int = 1) -> CommandContext:
    msg = InboundMessage(channel="cli", sender_id="u1", chat_id="direct", content=raw)
    store = _FakeStore(git, last_dream_cursor=last_dream_cursor)
    loop = SimpleNamespace(consolidator=SimpleNamespace(store=store))
    return CommandContext(msg=msg, session=None, key=msg.session_key, raw=raw, args=args, loop=loop)


@pytest.mark.asyncio
async def test_dream_log_latest_is_more_user_friendly() -> None:
    commit = CommitInfo(sha="abcd1234", message="dream: 2026-04-04, 2 change(s)", timestamp="2026-04-04 12:00")
    diff = (
        "diff --git a/SOUL.md b/SOUL.md\n"
        "--- a/SOUL.md\n"
        "+++ b/SOUL.md\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    git = _FakeGit(commits=[commit], diff_map={commit.sha: (commit, diff)})

    out = await cmd_dream_log(_make_ctx("/dream-log", git))

    assert "## Dream Update" in out.content
    assert "Here is the latest Dream memory change." in out.content
    assert "- Commit: `abcd1234`" in out.content
    assert "- Changed files: `SOUL.md`" in out.content
    assert "Use `/dream-restore abcd1234` to undo this change." in out.content
    assert "```diff" in out.content


@pytest.mark.asyncio
async def test_dream_log_missing_commit_guides_user() -> None:
    git = _FakeGit(diff_map={})

    out = await cmd_dream_log(_make_ctx("/dream-log deadbeef", git, args="deadbeef"))

    assert "Couldn't find Dream change `deadbeef`." in out.content
    assert "Use `/dream-restore` to list recent versions" in out.content


@pytest.mark.asyncio
async def test_dream_log_before_first_run_is_clear() -> None:
    git = _FakeGit(initialized=False)

    out = await cmd_dream_log(_make_ctx("/dream-log", git, last_dream_cursor=0))

    assert "Dream has not run yet." in out.content
    assert "Run `/dream`" in out.content


@pytest.mark.asyncio
async def test_dream_restore_lists_versions_with_next_steps() -> None:
    commits = [
        CommitInfo(sha="abcd1234", message="dream: latest", timestamp="2026-04-04 12:00"),
        CommitInfo(sha="bbbb2222", message="dream: older", timestamp="2026-04-04 08:00"),
    ]
    git = _FakeGit(commits=commits)

    out = await cmd_dream_restore(_make_ctx("/dream-restore", git))

    assert "## Dream Restore" in out.content
    assert "Choose a Dream memory version to restore." in out.content
    assert "`abcd1234` 2026-04-04 12:00 - dream: latest" in out.content
    assert "Preview a version with `/dream-log <sha>`" in out.content
    assert "Restore a version with `/dream-restore <sha>`." in out.content


@pytest.mark.asyncio
async def test_manual_dream_routes_through_egress_under_system_dream() -> None:
    """`/dream` (manual) must build a system_dream context + pass egress, same as cron.

    Regression: the manual command path was previously calling `loop.dream.run()`
    with no kwargs, which made Dream fall back to direct tool dispatch — bypassing
    the egress gateway and breaking the runtime invariant 'no traffic bypasses
    controls'. This test pins the governed-path handoff.
    """
    msg = InboundMessage(channel="cli", sender_id="u1", chat_id="direct", content="/dream")

    captured: dict[str, object] = {}

    async def fake_run(*, request_context=None, egress=None) -> bool:
        captured["request_context"] = request_context
        captured["egress"] = egress
        return True

    sentinel_egress = MagicMock(name="egress-sentinel")
    template_ctx = RequestContext(
        trace_id="t", span_id="s", parent_span_id=None,
        session_key="cron:dream", channel="cron",
        sender_id="system", chat_id="dream",
        agent_id="default",
    )

    loop = SimpleNamespace(
        dream=SimpleNamespace(run=AsyncMock(side_effect=fake_run)),
        egress=sentinel_egress,
        policy=SimpleNamespace(policy_version=42),
        bus=SimpleNamespace(publish_outbound=AsyncMock()),
        _normalize_context_for_cron=MagicMock(return_value=template_ctx),
    )
    ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw="/dream", args="", loop=loop)

    out = await cmd_dream(ctx)
    assert out.content == "Dreaming..."

    # Wait for the background task to complete.
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    rctx = captured["request_context"]
    assert isinstance(rctx, RequestContext)
    assert rctx.agent_id == "system_dream"
    assert rctx.policy_version == 42
    assert captured["egress"] is sentinel_egress


@pytest.mark.asyncio
async def test_dream_restore_success_mentions_files_and_followup() -> None:
    commit = CommitInfo(sha="abcd1234", message="dream: latest", timestamp="2026-04-04 12:00")
    diff = (
        "diff --git a/SOUL.md b/SOUL.md\n"
        "--- a/SOUL.md\n"
        "+++ b/SOUL.md\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "diff --git a/memory/MEMORY.md b/memory/MEMORY.md\n"
        "--- a/memory/MEMORY.md\n"
        "+++ b/memory/MEMORY.md\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    git = _FakeGit(
        diff_map={commit.sha: (commit, diff)},
        revert_result="eeee9999",
    )

    out = await cmd_dream_restore(_make_ctx("/dream-restore abcd1234", git, args="abcd1234"))

    assert "Restored Dream memory to the state before `abcd1234`." in out.content
    assert "- New safety commit: `eeee9999`" in out.content
    assert "- Restored files: `SOUL.md`, `memory/MEMORY.md`" in out.content
    assert "Use `/dream-log eeee9999` to inspect the restore diff." in out.content
