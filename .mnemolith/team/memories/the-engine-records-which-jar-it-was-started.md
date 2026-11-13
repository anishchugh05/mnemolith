---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
files:
  - mnemolith/engine.py@d92b15ccf687
---
The engine records which jar it was started from (sha256 fingerprint in ~/.mnemolith/engine.version). ensure_running restarts it when the installed jar's fingerprint differs, so a rebuilt jar takes effect without stopping the engine by hand.
