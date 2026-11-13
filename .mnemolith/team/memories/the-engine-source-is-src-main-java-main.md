---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
files:
  - scripts/build_engine.py@4cd70ba6580d
  - .gitignore@c8ae946a6360
  - src/main/java/Main.java@920e0f9cb700
  - mnemolith/engine/mnemolith-engine.jar@d9ea2adaf5c7
---
The engine source is src/main/java/Main.java, but the Python package runs the prebuilt jar mnemolith/engine/mnemolith-engine.jar. After changing Main.java, rebuild it with `python scripts/build_engine.py` (needs a JDK) and commit the jar, or the change never reaches users. *.jar is gitignored except this one file.
