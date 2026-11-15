---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
files:
  - scripts/build_engine.py@4cd70ba6580d
  - mnemolith/engine.py@d92b15ccf687
  - pom.xml@19eee35913d6
  - codecrafters.yml@d5c9ba7ad0e3
---
The shipped engine jar is compiled with `javac --release 11`, so it runs on any Java 11+ runtime (engine.py's MIN_JAVA = 11). Main.java must not use language features or APIs newer than Java 11, even though pom.xml and codecrafters.yml target Java 25 with --enable-preview for the CodeCrafters build.
