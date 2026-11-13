---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
files:
  - pom.xml@19eee35913d6
  - .codecrafters/run.sh@440eb31c8631
  - codecrafters.yml@d5c9ba7ad0e3
  - your_program.sh@0c4c9b7dc179
  - scripts/build_engine.py@4cd70ba6580d
---
pom.xml, codecrafters.yml, your_program.sh and .codecrafters/ come from the CodeCrafters "Build Your Own Redis" setup. Don't change the maven-assembly finalName `codecrafters-redis` (the pom says not to), because .codecrafters/run.sh runs /tmp/codecrafters-build-redis-java/codecrafters-redis.jar. These files don't build the Mnemolith package; scripts/build_engine.py does.
