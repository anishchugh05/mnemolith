---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
edited: 2026-11-22 by Anish Chugh
confirmed: 2026-11-22 by Anish Chugh
files:
  - plugin/pyproject.toml@e628dd6fb237
  - plugin/.claude-plugin/plugin.json@6f5c8733b685
---
The Claude Code plugin (plugin/) is bumped only after a release is on PyPI: run `python scripts/bump_version.py X.Y.Z --plugin`, which updates plugin/.claude-plugin/plugin.json (its version and the `mnemolith==X` pin in mcpServers), plugin/pyproject.toml, and the pin in plugin/skills/dashboard/SKILL.md. Then run `uv lock` in plugin/ and commit. It cannot go first because plugin/uv.lock needs the release to exist, and the release workflow runs `uv lock --check` in plugin/.
