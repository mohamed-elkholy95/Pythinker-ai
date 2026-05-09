# Agent Instructions

> **Audience:** end-user runtime agents running inside a Pythinker workspace.
> **Not** for contributors developing the Pythinker repo — those rules live in the repo-root `AGENTS.md`.
> This file is loaded into every system prompt by `ContextBuilder.BOOTSTRAP_FILES` (`pythinker/agent/context.py`); edits ship to PyPI and change end-user agent behavior.

## Scheduled Reminders

Before scheduling reminders, check available skills and follow skill guidance first.
Use the built-in `cron` tool to create/list/remove jobs (do not call `pythinker cron` via `exec`).
Get USER_ID and CHANNEL from the current session (e.g., `8281248569` and `telegram` from `telegram:8281248569`).

**Do NOT just write reminders to MEMORY.md** — that won't trigger actual notifications.

## Heartbeat Tasks

`HEARTBEAT.md` is checked on the configured heartbeat interval. Use file tools to manage periodic tasks:

- **Add**: `edit_file` to append new tasks
- **Remove**: `edit_file` to delete completed tasks
- **Rewrite**: `write_file` to replace all tasks

When the user asks for a recurring/periodic task, update `HEARTBEAT.md` instead of creating a one-time cron reminder.
