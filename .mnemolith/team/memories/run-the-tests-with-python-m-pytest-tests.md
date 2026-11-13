---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
confirmed: 2026-11-13 by Anish Chugh
files:
  - tests/test_team.py#repo@76a4006888c9
  - mnemolith/config.py#FAKE_EMBEDDER@1afb62f5fdd4
---
Run the tests with `python -m pytest tests`. They don't need the Java engine or the embedding model download: they set config.FAKE_EMBEDDER (env MNEMOLITH_EMBEDDER=fake) and point config.HOME at a tmp folder. Keep new tests the same way, without the engine or the network.
