---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
files:
  - mnemolith/config.py@f1ade1f9c820
  - mnemolith/legacy.py@094a57a66b8c
  - memory_mcp/server.py@7beb8722aa8b
---
The project used to be called Aether Cache. Backward compatibility is deliberate and must be kept: every MNEMOLITH_* env var also accepts AETHER_* and older MEMORY_* names (config._env), legacy.py moves ~/.aether-cache to ~/.mnemolith once, memory_mcp/server.py keeps old `python memory_mcp/server.py` setups working, and the release still builds the final legacy/aether-cache package.
