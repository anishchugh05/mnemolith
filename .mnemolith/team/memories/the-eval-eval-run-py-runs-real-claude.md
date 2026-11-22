---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
edited: 2026-11-22 by Anish Chugh
confirmed: 2026-11-22 by Anish Chugh
files:
  - eval/run.py@18c3a4b97da1
  - .gitignore@fbfc5d1d3d6c
---
The eval (eval/run.py) runs real Claude Code sessions on sample shop codebases in eval/template*/ and needs Claude Code signed in, plus uv, git and make. Use `python3 eval/run.py --dry-run` to check the setup without spending runs (it skips Claude, so Claude Code is not needed for it). Every run, dry or real, gets its own scratch Mnemolith home and port (eval/sandbox.py), so the eval never touches ~/.mnemolith or an engine that is already running. The eval/template*/ folders are test fixtures with deliberate rules, not part of Mnemolith. Results go to eval/runs/, which is gitignored.
