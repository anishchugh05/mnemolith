---
name: dashboard
description: Open the Mnemolith dashboard to browse, search and fix this project's memories, and to approve Mender's drafted fixes. Use when the user asks to see, review or manage their memories.
---

# Open the Mnemolith dashboard

Run this in the background with the Bash tool, then tell the user the address it prints
(usually http://127.0.0.1:7381):

```sh
uvx mnemolith==0.3.5 ui
```

The dashboard runs only on this computer. From it the user can browse memories by project, see which ones went
stale and why, approve or dismiss Mender's drafted fixes, revert a fix, and turn Mender on or off in Settings.
Turning Mender on or off takes effect the next time Claude Code starts.
