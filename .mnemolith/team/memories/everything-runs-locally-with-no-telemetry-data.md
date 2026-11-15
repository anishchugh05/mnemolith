---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
files:
  - mnemolith/config.py@f1ade1f9c820
  - mnemolith/javart.py@ac137b2471f7
---
Everything runs locally with no telemetry. Data lives under ~/.mnemolith (MNEMOLITH_HOME overrides it): the engine data, its log and pid, the downloaded embedding model (all-MiniLM-L6-v2, about 90 MB on first use) and a private Java from Adoptium if none is installed. The first start can take a minute while these download.
