---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
edited: 2026-11-22 by Anish Chugh
confirmed: 2026-11-22 by Anish Chugh
files:
  - .github/workflows/release.yml@27544323ae7e
---
Releasing: run `python scripts/bump_version.py X.Y.Z`, which sets the same version in pyproject.toml, mnemolith/__init__.py, server.json (both `version` and `packages[0].version`), the README tag line and the website footers. Then run `uv lock` at the root: the script does not touch the root uv.lock and the release workflow does not check it (0.4.3 shipped with a stale one). Commit, then push a tag `vX.Y.Z`. The release workflow fails if any version disagrees with the tag. It publishes to PyPI by trusted publishing and to the MCP Registry by GitHub OIDC, so no tokens are needed.
