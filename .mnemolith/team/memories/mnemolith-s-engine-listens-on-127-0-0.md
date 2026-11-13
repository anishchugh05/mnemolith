---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
files:
  - mnemolith/config.py@f1ade1f9c820
  - mnemolith/ui.py@bf96a4a6097b
---
Mnemolith's engine listens on 127.0.0.1:7380 by default, deliberately not Redis's 6379, so it never collides with a real Redis on the machine. The dashboard (`mnemolith ui`) uses 127.0.0.1:7381. Override them with MNEMOLITH_PORT and MNEMOLITH_UI_PORT.
