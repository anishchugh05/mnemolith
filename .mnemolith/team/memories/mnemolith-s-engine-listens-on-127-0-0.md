---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
confirmed: 2026-11-13 by Anish Chugh
files:
  - mnemolith/config.py#PORT@d8f140b7caa8
  - mnemolith/ui.py#DEFAULT_PORT@bfabbb681ea1
---
Mnemolith's engine listens on 127.0.0.1:7380 by default, deliberately not Redis's 6379, so it never collides with a real Redis on the machine. The dashboard (`mnemolith ui`) uses 127.0.0.1:7381. Override them with MNEMOLITH_PORT and MNEMOLITH_UI_PORT.
