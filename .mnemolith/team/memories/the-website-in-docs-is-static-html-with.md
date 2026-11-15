---
mnemolith: 1
status: active
author: Anish Chugh
created: 2026-11-12
files:
  - docs/CNAME@e06c69c083ef
  - docs/assets/site.js@5e6645dde67d
---
The website in docs/ is static HTML with shared assets/site.css and assets/site.js, with no build step or framework. Preview it with `python3 -m http.server 4173` from docs/. Absolute share-metadata URLs use https://mnemolith.dev/ (docs/CNAME) and must be updated on every page if the address changes.
