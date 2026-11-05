# Does memory help? The Mnemolith evaluation

Claude Code does tasks in a small sample shop codebase, in three setups:

| Setup | What Claude gets |
| --- | --- |
| No memory | Just the code |
| CLAUDE.md notes | Nine facts about the project in a CLAUDE.md file, the usual way |
| Mnemolith | The same nine facts as Mnemolith memories, each tied to the files it depends on |

There are two rounds.

**Round 2** (the default, [tasks_v2.py](tasks_v2.py), [template_v2/](template_v2)): knowledge that is nowhere in the
code. Past decisions (prices are never cached), ownership (the payments team's code isn't ours to edit), promises
(API v1 is frozen for a partner), business and privacy rules (big refunds need approval, emails are never logged).
Each task is one where the natural move, without that knowledge, is the wrong one. Two tasks test learning across
sessions: the fact comes up in passing in a first session, and a second, fresh session needs it. Nothing is
preloaded for those, in any setup. 7 tasks x 3 setups = 21 runs.

**Round 1** (`--suite v1`, [tasks.py](tasks.py), [template/](template)): the facts are things a teammate would tell you that the code doesn't spell out: run tests with `make test`, money is
integer cents, never hand-edit the generated schema, all HTTP goes through one client, and so on
([tasks.py](tasks.py)). After the notes and memories are written, a commit changes the tax rate and the free
shipping threshold, so two of the facts go stale, the way notes do in real projects. Three tasks depend on those.
10 tasks x 3 setups = 30 runs. In our first run every setup passed every task: Claude found what it needed in the
code, which is why round 2 exists.

Every task has an automatic check (tests pass, the right helper was used, the answer is right), so nobody grades
by hand. Each run starts from a fresh copy of the repo, and your own Claude Code settings, plugins and MCP servers
are left out, so the setups differ only in the notes or memories they get.

## Running it

Needs Claude Code (signed in), uv, git and make.

```
cd eval
python3 run.py --dry-run     # checks the setup, without Claude (every task fails: nothing was done)
python3 run.py --limit 10    # 10 runs now; round 2 is 21 in all (add --suite v1 for round 1)
python3 run.py --resume      # the next ones, another day
python3 report.py            # the results table
```

Runs use your Claude plan. If it hits the usage limit, the batch stops and `--resume` picks up where it left off.
Results and transcripts go to `eval/runs/` (not committed).
