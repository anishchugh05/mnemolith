---
name: project-memory
description: Use Mnemolith as this project's memory. Load at the start of any coding task, when the user asks to remember or note something about the project, or when a remembered fact might be out of date.
---

# Project memory with Mnemolith

Mnemolith keeps facts about this project across sessions and across AI tools (Claude Code, Cursor, Codex).
Each memory can be tied to the files it depends on; when those files change, the memory is flagged stale
instead of quietly misleading the next session. Use its tools instead of any built-in memory or notes file.

## Every task

1. Call `recall` with a short description of the task before you start. Treat what comes back as project
   knowledge, but trust the code over a memory if they disagree.
2. If `recall` says memories went stale, call `check_stale` and look at the files before relying on them.

## Saving something

- Call `remember` when the user asks you to remember, save or note something about the project, or when you
  learn something a future session would need: a convention, a setup gotcha, a decision and the reason for it.
- Always pass `files=` with the files the fact depends on (for example `["docker-compose.yml"]`). That is what
  lets Mnemolith retire the memory when those files change.
- One fact per memory, written so it makes sense on its own months later.
- Don't save what `recall` already showed you.
- Don't save things that are obvious from the code, or temporary state like the current branch.

## Fixing memories

- A memory turns out to be wrong: `mark_outdated` with a short reason.
- A new fact makes an old one untrue: `remember` the new one, then `replace_memory`.
- A stale memory is still true after checking the files: `confirm_memory`.
- `recall` reports two memories that disagree: `list_conflicts`, then `resolve_conflict`.

## When Mender is on

If the tools `list_stale` and `propose_fix` exist, Mender is on. For stale memories, call `list_stale`, read
the changed files, and call `propose_fix` for each one that has no draft yet, instead of rewriting memories
yourself. Nothing changes until the user approves the fix in the dashboard (`/mnemolith:dashboard`).
