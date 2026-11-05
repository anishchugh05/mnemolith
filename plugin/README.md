# Mnemolith plugin for Claude Code

Shared project memory that stays true to your code. Claude remembers conventions, setup gotchas and decisions
across sessions, and Cursor and Codex can read the same memories. Each memory is tied to the files it depends
on, so when those files change it's flagged stale instead of quietly misleading the next session.

More at [mnemolith.dev](https://mnemolith.dev/).

## Install

In Claude Code:

```
/plugin marketplace add anishchugh05/mnemolith
/plugin install mnemolith@mnemolith
```

Or in one step on Claude Code 2.1.275 or later:

```
/plugin install mnemolith --marketplace anishchugh05/mnemolith
```

## What you get

- **The Mnemolith MCP server**, with tools to `recall`, `remember`, `check_stale`, `mark_outdated`,
  `replace_memory`, `confirm_memory`, and to list and resolve conflicting memories. With Mender on, also
  `list_stale` and `propose_fix`.
- **`project-memory` skill**: tells Claude when to recall, what's worth saving, and how to fix memories.
- **`/mnemolith:dashboard`**: opens the local dashboard to browse memories and approve Mender's fixes.

## Requirements

[uv](https://docs.astral.sh/uv/) must be installed, because the server starts with `uvx mnemolith==<version>`.
Nothing else: no API key, no account, no `.env` file. Java and the embedding model are fetched automatically the
first time (see below), so the first start can take a minute.

Works in Claude Code, and in Cowork when the session runs on your computer. It isn't available in claude.ai
chat on the web or phone, because those can't start a local server.

## What it runs and what it sends

Everything runs on your computer and your memories never leave it.

- `uvx` installs the pinned `mnemolith` release and its Python dependencies from PyPI into a private environment.
  `uv.lock` in this folder records the exact versions and hashes.
- The server starts Mnemolith's storage engine, a small Java program listening only on `127.0.0.1:7380`. If
  your computer has no Java 11 or newer, it downloads a private copy of the Temurin runtime from
  `api.adoptium.net` into `~/.mnemolith`.
- The first time it needs to compare text, it downloads a small open embedding model (about 90 MB) from
  Hugging Face into `~/.mnemolith`. Embeddings are computed locally.
- It runs `git` in your project to tell which files changed since a memory was saved.
- `/mnemolith:dashboard` serves a web page on `127.0.0.1:7381`, reachable only from your computer.

There's no telemetry and no Mnemolith server. Your data stays in `~/.mnemolith` until you delete it.

## Already using Mnemolith?

If you connected it before with `mnemolith setup` or `claude mcp add`, remove that entry
(`claude mcp remove mnemolith`) so Claude doesn't see the tools twice. Your memories stay where they are.

## License

MIT. Source: [github.com/anishchugh05/mnemolith](https://github.com/anishchugh05/mnemolith).
