---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
confirmed: 2026-11-13 by Anish Chugh
files:
  - mnemolith/server.py#blob_hash@df55b2576c11
  - mnemolith/server.py#check_staleness@01482a9bc249
---
Decision: staleness compares file contents, not commits. Each linked file is stored as path@<git blob hash> (git hash-object), and recall re-hashes the linked files before every search. That catches uncommitted edits, ignores commits that didn't touch the file, and needs no git hooks.
