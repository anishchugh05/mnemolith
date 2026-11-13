---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
files:
  - tests/test_team.py@fdaa70d8c5ae
  - mnemolith/config.py@f1ade1f9c820
---
Run the tests with `python -m pytest tests`. They don't need the Java engine or the embedding model download: they set config.FAKE_EMBEDDER (env MNEMOLITH_EMBEDDER=fake) and point config.HOME at a tmp folder. Keep new tests the same way, without the engine or the network.
