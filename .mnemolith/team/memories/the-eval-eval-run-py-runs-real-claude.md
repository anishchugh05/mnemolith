---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
files:
  - eval/run.py@b76596730990
  - .gitignore@c8ae946a6360
---
The eval (eval/run.py) runs real Claude Code sessions on sample shop codebases in eval/template*/ and needs Claude Code signed in, plus uv, git and make. Use `python3 eval/run.py --dry-run` to check the setup without spending runs. The eval/template*/ folders are test fixtures with deliberate rules, not part of Mnemolith. Results go to eval/runs/, which is gitignored.
