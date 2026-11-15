# Team memory

This folder is your team's shared memory for AI coding tools, managed by [Mnemolith](https://mnemolith.dev/).

Each file in `memories/` is one fact about this project that everyone's AI tool should know: a convention,
a setup step, a decision and the reason for it. They are plain text, so you can read, edit and review them
like any other file in the repo.

- **Everyone who can see this repo can see these files.** Never put passwords, keys or anything private here.
- To use them, install Mnemolith and run `mnemolith team init` in this repo. It only changes your own setup.
- To change a memory, edit its file. To retire one, set `status: retired` in its header.
- `mnemolith.md` at the top of the repo lists every memory in one line, for teammates and AI tools without
  Mnemolith. It's generated: `mnemolith team sync` rebuilds it after you edit a memory by hand.
- `files:` lists the files (or folders) a memory depends on. When one changes, Mnemolith shows the memory as possibly
  outdated until someone runs `mnemolith team confirm <name>` or fixes it.
- Mnemolith never commits. Changes here are yours to commit and review.
