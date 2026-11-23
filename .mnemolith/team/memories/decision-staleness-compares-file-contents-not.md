---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
edited: 2026-11-23 by Anish Chugh (suggested by Claude Code)
files:
  - mnemolith/server.py#blob_hash@df55b2576c11
  - mnemolith/server.py#check_staleness@c4251bb9d750
---
Decision: staleness compares file contents, not commits. Each linked file is stored as path@<git blob hash> (git hash-object), and a linked part of a file as path#symbol@<hash of that part>. recall re-hashes the linked files before every search. That catches uncommitted edits, ignores commits that didn't touch the file, and needs no git hooks.
