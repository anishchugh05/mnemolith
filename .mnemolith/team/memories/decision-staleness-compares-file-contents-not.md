---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
files:
  - mnemolith/server.py@ba7bd39b7e2e
---
Decision: staleness compares file contents, not commits. Each linked file is stored as path@<git blob hash> (git hash-object), and recall re-hashes the linked files before every search. That catches uncommitted edits, ignores commits that didn't touch the file, and needs no git hooks.
