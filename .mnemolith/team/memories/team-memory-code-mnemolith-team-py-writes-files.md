---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
confirmed: 2026-11-13 by Anish Chugh
files:
  - mnemolith/team.py#_write@1be8903f6ddc
  - mnemolith/team.py#fingerprint@f988674cad84
  - mnemolith/team.py#MAX_FOLDER_FILES@5991826b8f67
---
Team memory code (mnemolith/team.py) writes files under .mnemolith/team/ and mnemolith.md but never runs git commit; people review and commit. It keeps each file's own line endings when rewriting and reads CRLF as LF when fingerprinting, so Windows and Mac agree. A linked folder is fingerprinted file by file, capped at MAX_FOLDER_FILES = 500.
