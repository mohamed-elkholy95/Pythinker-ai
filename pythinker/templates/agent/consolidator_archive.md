You are compacting a long conversation into a compressed memory entry that will be reloaded as bootstrap context for the next turn. The output is appended to the agent's `MEMORY.md` — keep it lean, scan-friendly, and faithful.

## Priorities (in order, highest first)

1. **Current focus.** What problem is the user actively solving right now? What constraint or sub-goal are we on? If they were mid-edit on a specific file, name the file.
2. **Active issues.** Open errors, failing tests, unresolved tracebacks, blocked decisions. Pair each with the last attempted fix and why it didn't land.
3. **Code state.** Files created / modified during this conversation, with one-line intent and the absolute path. **Do not paste full file contents.** Summarize what changed; if a snippet is essential to recall, quote at most 5 lines and prefix with the file path.
4. **Completed tasks.** What we've already shipped this session — durable artifacts only (committed code, written files, decisions), not exploratory dead-ends. One bullet per task.
5. **Environment.** Tools, versions, paths, env vars, OS quirks the next turn will need. Skip anything derivable from `pythinker doctor` or the workspace listing.
6. **Important context.** Anything else that materially changes the next turn's decisions: user preferences voiced this session, decisions made and their rationale, "do NOT do X" rules, pending follow-ups.

## Compression rules

- **Drop chat fluff.** No greetings, acknowledgements, restated user prompts, agent self-narration. The next turn does not need to relive the conversation.
- **Keep the why, not the what.** "Switched from `regex` to `glob` because regex didn't follow symlinks" — keep. "Tried `grep -r foo`, got 12 results, looked through them" — drop.
- **Verbatim only when load-bearing.** Exact error messages, exact file paths, exact command lines. Paraphrase the rest.
- **Names over descriptions.** `pythinker.cli.commands._make_provider` beats "the CLI helper that builds the provider object."
- **Resolve relative time.** "Yesterday's commit" → an actual SHA or timestamp.
- **Mask secrets.** API keys, tokens, OAuth codes → `***`. Even in error messages.

## Output format

Use these flat section tags. Skip a section entirely if it would be empty — do not emit `(none)` placeholders. Tags exist for the LLM writing this section, not for downstream parsing — keep the inside of each tag plain Markdown.

```
<current_focus>
One short paragraph (1–3 sentences). What is the user trying to accomplish right now?
</current_focus>

<active_issues>
- Issue 1: <one-line description> — last attempt: <one line> — blocker: <why it failed>
- Issue 2: ...
</active_issues>

<code_state>
- path/to/file.py — added X / changed Y / deleted Z
- path/to/other.py — ...
</code_state>

<completed_tasks>
- <task name>: <one-line outcome> [<commit SHA or artifact path>]
- ...
</completed_tasks>

<environment>
- <tool / version / env var / path that changed or matters>
- ...
</environment>

<important_context>
- <user preference voiced this session>
- <decision made and one-line rationale>
- <"do NOT do X" rule the next turn must respect>
- ...
</important_context>
```

If everything fits in `<current_focus>` and `<active_issues>` (small / quiet sessions), omit the rest. The goal is to be useful, not exhaustive.
