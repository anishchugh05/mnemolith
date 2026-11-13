---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
files:
  - src/main/java/Main.java@920e0f9cb700
---
Memories never change silently: every change is a logged event with a timestamp and a reason. The engine writes each memory to the append-only log with the id and timestamp it assigned, so a replay after restart rebuilds the exact same memories and histories. Changes to memory code in Main.java must keep that replay deterministic.
