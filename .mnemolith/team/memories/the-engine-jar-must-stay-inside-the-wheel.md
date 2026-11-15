---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
files:
  - pyproject.toml@1d03a3c2bf43
  - mnemolith/engine/mnemolith-engine.jar@d9ea2adaf5c7
---
The engine jar must stay inside the wheel and sdist: pyproject.toml lists it under hatch `artifacts` because *.jar is gitignored, and the release smoke test fails if the wheel lacks mnemolith/engine/mnemolith-engine.jar. Don't remove those artifacts lines.
