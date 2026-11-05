# Does memory help? The Mnemolith evaluation

Claude Code does ten tasks in a small sample shop codebase ([template/](template)), in three setups:

| Setup | What Claude gets |
| --- | --- |
| No memory | Just the code |
| CLAUDE.md notes | Nine facts about the project in a CLAUDE.md file, the usual way |
| Mnemolith | The same nine facts as Mnemolith memories, each tied to the files it depends on |

The facts are things a teammate would tell you that the code doesn't spell out: run tests with `make test`, money is
integer cents, never hand-edit the generated schema, all HTTP goes through one client, and so on
([tasks.py](tasks.py)). After the notes and memories are written, a commit changes the tax rate and the free
shipping threshold, so two of the facts go stale, the way notes do in real projects. Three tasks depend on those.

Every task has an automatic check (tests pass, the right helper was used, the answer is right), so nobody grades
by hand. Each run starts from a fresh copy of the repo, and your own Claude Code settings, plugins and MCP servers
are left out, so the setups differ only in the notes or memories they get.

## Running it

Needs Claude Code (signed in), uv, git and make.

```
cd eval
python3 run.py --dry-run     # checks the setup, without Claude (every task fails: nothing was done)
python3 run.py --limit 10    # 10 runs now; the whole batch is 30
python3 run.py --resume      # the next ones, another day
python3 report.py            # the results table
```

Runs use your Claude plan. If it hits the usage limit, the batch stops and `--resume` picks up where it left off.
Results and transcripts go to `eval/runs/` (not committed).
