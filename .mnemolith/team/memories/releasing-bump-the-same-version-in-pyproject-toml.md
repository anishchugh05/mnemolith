---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
files:
  - .github/workflows/release.yml@679dbc540403
---
Releasing: bump the same version in pyproject.toml, mnemolith/__init__.py and server.json (both `version` and `packages[0].version`), commit, then push a tag `vX.Y.Z`. The release workflow fails if any of them disagree with the tag. It publishes to PyPI by trusted publishing and to the MCP Registry by GitHub OIDC, so no tokens are needed.