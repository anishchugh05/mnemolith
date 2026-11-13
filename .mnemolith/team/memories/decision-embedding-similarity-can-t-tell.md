---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
files:
  - mnemolith/server.py@ba7bd39b7e2e
  - mnemolith/config.py@f1ade1f9c820
---
Decision: embedding similarity can't tell duplicates from contradictions (in calibration "timeout is 30s" vs "60s" scored 0.94, "we use Postgres" vs "we migrated to MySQL" 0.30). So the engine auto-merges only near-identical text (similarity >= 0.97). For everything else, similar memories are shown to the AI to judge. Don't lower the auto-merge threshold.
