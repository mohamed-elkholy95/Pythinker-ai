# In-Chat Commands

These commands work inside chat channels and interactive agent sessions:

| Command | Description |
|---------|-------------|
| `/new` | Stop current task and start a new conversation |
| `/stop` | Stop the current task |
| `/restart` | Restart the bot |
| `/status` | Show bot status |
| `/tasks` | List active and recent autonomous tasks for this session |
| `/task-output <task_id>` | Show the latest output tail for a task |
| `/task-stop <task_id>` | Stop a running task by id |
| `/dream` | Run Dream memory consolidation now |
| `/dream-log` | Show the latest Dream memory change |
| `/dream-log <sha>` | Show a specific Dream memory change |
| `/dream-restore` | List recent Dream memory versions |
| `/dream-restore <sha>` | Restore memory to the state before a specific change |
| `/help` | Show available in-chat commands |

## Autonomous Tasks

Subagents are tracked as autonomous tasks. Use `/tasks` to see active and recent work, `/task-output <task_id>` to inspect the latest saved output, and `/task-stop <task_id>` to cancel a running subagent.

Task output is stored under the workspace's `.pythinker/task-results/` directory and exposed through bounded chat output so large results do not flood the conversation. The store keeps the most recent ~200 terminal records — older terminal output files are unlinked automatically as new tasks finish.

In-memory task records are scoped to the originating chat session: `/tasks`, `/task-stop`, and `/task-output` only see tasks from the session they were issued in. The one exception is restart recovery — surviving output files left under `.pythinker/task-results/` are reloaded as ownerless "orphaned" records and `/task-output <task_id>` can read them from any session. That's the intended behavior for Pythinker's single-user/local deployment; if you run Pythinker as a shared service, treat task output as workspace-wide rather than session-private.

## Periodic Tasks

The gateway wakes up every 30 minutes and checks `HEARTBEAT.md` in your workspace (`~/.pythinker/workspace/HEARTBEAT.md`). If the file has tasks, the agent executes them and delivers results to your most recently active chat channel.

**Setup:** edit `~/.pythinker/workspace/HEARTBEAT.md` (created automatically by `pythinker onboard`):

```markdown
## Periodic Tasks

- [ ] Check weather forecast and send a summary
- [ ] Scan inbox for urgent emails
```

The agent can also manage this file itself — ask it to "add a periodic task" and it will update `HEARTBEAT.md` for you.

> **Note:** The gateway must be running (`pythinker gateway`) and you must have chatted with the bot at least once so it knows which channel to deliver to.
