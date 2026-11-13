---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
files:
  - plugin/pyproject.toml@28e137aef3c5
  - plugin/.claude-plugin/plugin.json@acec3b40c774
---
The Claude Code plugin (plugin/) is bumped only after a release is on PyPI: update the version in plugin/.claude-plugin/plugin.json (the `mnemolith==X` pin in mcpServers too) and plugin/pyproject.toml, run `uv lock` in plugin/, then commit. It can't go first because uv.lock needs the release to exist, and the release workflow runs `uv lock --check` in plugin/.
