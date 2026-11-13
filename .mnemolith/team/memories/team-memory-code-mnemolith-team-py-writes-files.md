---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
files:
  - mnemolith/team.py@940fe0874f04
---
Team memory code (mnemolith/team.py) writes files under .mnemolith/team/ and mnemolith.md but never runs git commit; people review and commit. It keeps each file's own line endings when rewriting and reads CRLF as LF when fingerprinting, so Windows and Mac agree. A linked folder is fingerprinted file by file, capped at MAX_FOLDER_FILES = 500.
