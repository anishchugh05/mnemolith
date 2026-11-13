---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
confirmed: 2026-11-13 by Anish Chugh
files:
  - src/main/java/Main.java#DUPLICATE_SIMILARITY@7ba92aeb409d
---
Decision: embedding similarity can't tell duplicates from contradictions (in calibration "timeout is 30s" vs "60s" scored 0.94, "we use Postgres" vs "we migrated to MySQL" 0.30). So the engine auto-merges only near-identical text (similarity >= 0.97). For everything else, similar memories are shown to the AI to judge. Don't lower the auto-merge threshold.
