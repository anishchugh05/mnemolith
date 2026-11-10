"""Runs the with-vs-without memory evaluation: each task, in a fresh copy of the sample repo, with Claude Code in
three setups.

  none       no project notes at all
  notes      the facts written in CLAUDE.md, the usual way people give an AI project knowledge
  mnemolith  the same facts as Mnemolith memories, tied to the files they depend on

After the notes and memories are written, a commit changes the tax rate and the free shipping threshold, so two
facts go stale, as notes do in real projects.

There are three rounds (suites). v1: facts the code also shows, two of which go stale. v2: knowledge that is only
in people's heads (decisions, ownership, promises, rules), plus two tasks where the fact is mentioned in a first
session and needed in a second, fresh one. v3: two teammates, each with their own clone: one tells their AI
something and asks for the team to know it, the other's AI needs it; in three tasks the code changes in between.
v3 has a fourth setup, team: Mnemolith with team memory (then mnemolith means personal memories only).

Usage (from this folder):
  python3 run.py                     start a new batch of round 2: 7 tasks x 3 setups = 21 runs
  python3 run.py --suite v1          round 1 instead: 10 tasks x 3 setups = 30 runs
  python3 run.py --suite v3          round 3, two teammates: 7 tasks x 4 setups = 28 runs
  python3 run.py --limit 10          stop after 10 runs (to spread a batch over days)
  python3 run.py --resume            continue the latest batch where it stopped
  python3 run.py --dry-run           everything except Claude, to check the setup works
  python3 run.py --arms mnemolith    only some setups (comma-separated), e.g. to recheck Mnemolith after a change
  python3 run.py --local             test the code in this repo instead of the release on PyPI
  python3 report.py                  turn the results into a table

Needs: Claude Code signed in, uv, git, make, and Mnemolith's engine able to start (it starts itself).
Standard library only. Your own Claude Code settings, plugins and MCP servers are left out of every run, so the
three setups differ only in the notes or memories they get.
"""
import argparse
import importlib
import json
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITES = {"v1": ("tasks", "template"), "v2": ("tasks_v2", "template_v2"), "v3": ("tasks_v3", "template_v2")}
RUNS = HERE / "runs"
MNEMOLITH = "0.3.5"  # the release under test; seed.py and the MCP server both use it
ARMS = ["none", "notes", "mnemolith"]
LOCAL = None  # with --local: the repo folder whose code is tested instead of the PyPI release


def package():
    """What uv installs for the Mnemolith setup: the pinned release, or this repo's own code."""
    return str(LOCAL) if LOCAL else f"mnemolith=={MNEMOLITH}"


def uvx(*command):
    """uvx running a mnemolith command from the version under test."""
    base = ["uvx", "--refresh-package", "mnemolith", "--python", "3.12"]
    return base + (["--from", str(LOCAL), "mnemolith"] if LOCAL else [package()]) + list(command)


def local_version():
    head = sh(["git", "rev-parse", "--short", "HEAD"], LOCAL, check=False).stdout.strip()
    dirty = sh(["git", "status", "--porcelain", "--", "mnemolith"], LOCAL, check=False).stdout.strip()
    return f"local {head or 'copy'}" + (" + uncommitted changes" if dirty else "")

ALLOWED_TOOLS = [
    "Read", "Edit", "Write", "Glob", "Grep",
    "Bash(make:*)", "Bash(python3:*)", "Bash(python:*)", "Bash(SHOP_ENV=test:*)",
    "Bash(ls:*)", "Bash(cat:*)", "Bash(grep:*)", "Bash(find:*)",
    "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)",
    "mcp__mnemolith",
]


def sh(cmd, cwd, env=None, check=True):
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed:\n{p.stdout}{p.stderr}")
    return p


def git_commit(cwd, message):
    sh(["git", "add", "-A"], cwd)
    sh(["git", "-c", "user.name=eval", "-c", "user.email=eval@example.com", "commit", "-q", "-m", message], cwd)


def load_suite(name):
    module, template = SUITES[name]
    suite = importlib.import_module(module)
    suite.TEMPLATE = HERE / template
    return suite


def notes_file(facts):
    lines = ["# tinyshop", "", "Notes for working in this repo:", ""]
    return "\n".join(lines + [f"- {f['text']}" for f in facts]) + "\n"


def prepare(suite, run_dir, arm, project):
    """A fresh copy of the sample repo, with this setup's notes or memories, then (round 1) the commit that makes
    two facts stale."""
    shutil.copytree(suite.TEMPLATE, run_dir, ignore=shutil.ignore_patterns("__pycache__"))
    sh(["git", "init", "-q"], run_dir)
    git_commit(run_dir, "tinyshop")
    if arm == "notes":
        (run_dir / "CLAUDE.md").write_text(notes_file(suite.FACTS))
        git_commit(run_dir, "Add notes for AI tools")
    if arm == "mnemolith":
        env = dict(os.environ, MNEMOLITH_PROJECT=project, MNEMOLITH_SOURCE="eval", EVAL_SUITE=suite.__name__)
        sh(["uv", "run", "--quiet", "--no-project", "--refresh-package", "mnemolith", "--python", "3.12",
            "--with", package(), "python", str(HERE / "seed.py")], run_dir, env=env)
    if suite.DRIFT:
        for rel, (old, new) in suite.DRIFT.items():
            path = run_dir / rel
            path.write_text(path.read_text().replace(old, new))
        git_commit(run_dir, suite.DRIFT_MESSAGE)


def mcp_config(run_dir, arm, project):
    servers = {}
    if arm in ("mnemolith", "team"):
        command = uvx("serve")
        servers["mnemolith"] = {"command": command[0], "args": command[1:],
                                "env": {"MNEMOLITH_PROJECT": project, "MNEMOLITH_SOURCE": "eval"}}
    path = run_dir.parent / f"{run_dir.name}.mcp.json"
    path.write_text(json.dumps({"mcpServers": servers}))
    return path


def seed(suite, cwd, project, team=""):
    env = dict(os.environ, MNEMOLITH_PROJECT=project, MNEMOLITH_SOURCE="eval", EVAL_SUITE=suite.__name__, EVAL_TEAM=team)
    sh(["uv", "run", "--quiet", "--no-project", "--refresh-package", "mnemolith", "--python", "3.12",
        "--with", package(), "python", str(HERE / "seed.py")], cwd, env=env)


def team_dirs(run_dir):
    """Round 3: teammate B's clone is the run folder (so checks and clean-up work as in the other rounds);
    teammate A's clone and the shared remote sit next to it."""
    return run_dir.with_name(run_dir.name + ".a"), run_dir.with_name(run_dir.name + ".git")


def run_team(suite, task, run_dir, arm, project, model, batch, name, dry_run):
    """One round 3 run. Returns (first, result), either of which may be a usage-limit reply; None when dry."""
    a_dir, remote = team_dirs(run_dir)
    for d in (a_dir, remote):
        if d.exists():
            shutil.rmtree(d)
    # the repo as the team has it, and teammate A's clone of it
    shutil.copytree(suite.TEMPLATE, a_dir, ignore=shutil.ignore_patterns("__pycache__"))
    sh(["git", "init", "-q"], a_dir)
    git_commit(a_dir, "tinyshop")
    if arm == "notes":
        (a_dir / "CLAUDE.md").write_text(notes_file(suite.FACTS))
        git_commit(a_dir, "Add notes for AI tools")
    if arm == "mnemolith":
        seed(suite, a_dir, project)
    if arm == "team":
        seed(suite, a_dir, project, team="start")
        git_commit(a_dir, "Start a team memory")
    sh(["git", "init", "-q", "--bare", str(remote)], batch)
    sh(["git", "remote", "add", "origin", str(remote)], a_dir)
    sh(["git", "push", "-q", "-u", "origin", "HEAD"], a_dir)

    first = None
    if not dry_run:
        # Session 1: teammate A tells their AI something and asks for the team's AI tools to know it
        first = ask_claude(a_dir, task["first"], mcp_config(a_dir, arm, project), model, batch / f"{name}.first.jsonl")
        if hit_limit(first):
            return first, None
    sh(["git", "add", "-A"], a_dir)  # A commits and pushes whatever that session left in the repo
    sh(["git", "-c", "user.name=eval", "-c", "user.email=eval@example.com", "commit", "-q", "--allow-empty",
        "-m", "Session 1"], a_dir)
    if task.get("drift"):
        task["drift"](a_dir)  # another teammate's change, which makes what A shared out of date
    sh(["git", "push", "-q", "origin", "HEAD"], a_dir)

    # teammate B: their own clone, their own personal memories
    sh(["git", "clone", "-q", str(remote), str(run_dir)], batch)
    b_project = project + "-b"
    if arm == "mnemolith":
        seed(suite, run_dir, b_project)
    if arm == "team":
        seed(suite, run_dir, b_project, team="join")
    if dry_run:
        return None, None
    result = ask_claude(run_dir, task["prompt"], mcp_config(run_dir, arm, b_project), model,
                        batch / f"{name}.transcript.jsonl")
    return first, result


def clean_team(suite, run_dir, arm, project, keep):
    a_dir, remote = team_dirs(run_dir)
    if arm in ("mnemolith", "team"):
        for d, p in ((a_dir, project), (run_dir, project + "-b")):
            if d.exists():
                sh(uvx("remove", p, "--yes"), d, check=False)
                if arm == "team":
                    seed(suite, d, p, team="leave")
    if not keep:
        for d in (run_dir, a_dir, remote):
            shutil.rmtree(d, ignore_errors=True)


def ask_claude(run_dir, prompt, config, model, transcript):
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--strict-mcp-config", "--mcp-config", str(config),
           "--setting-sources", "project,local", "--no-session-persistence",
           "--permission-mode", "acceptEdits", "--allowedTools", *ALLOWED_TOOLS]
    if model:
        cmd += ["--model", model]
    started = time.time()
    p = subprocess.run(cmd, cwd=run_dir, capture_output=True, text=True, timeout=1200)
    transcript.write_text(p.stdout + ("\n" + p.stderr if p.stderr else ""))
    out = {"seconds": round(time.time() - started, 1), "tool_calls": 0, "memory_calls": 0, "answer": "",
           "turns": None, "tokens": None, "cost_usd": None, "error": p.returncode != 0, "model": None}
    for line in p.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            out["model"] = event.get("model")
        if event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    out["tool_calls"] += 1
                    out["memory_calls"] += block.get("name", "").startswith("mcp__mnemolith__")
        if event.get("type") == "result":
            usage = event.get("usage") or {}
            out.update(answer=event.get("result") or "", turns=event.get("num_turns"),
                       cost_usd=event.get("total_cost_usd"), error=bool(event.get("is_error")),
                       tokens=sum(usage.get(k) or 0 for k in ("input_tokens", "output_tokens",
                                                              "cache_read_input_tokens",
                                                              "cache_creation_input_tokens")))
    return out


def hit_limit(result):
    text = (result.get("answer") or "").lower()
    return result.get("error") and any(w in text for w in ("usage limit", "rate limit", "limit reached",
                                                           "hit your", "limit · resets", "resets "))


def plan(tasks, repeat, seed, arms=ARMS):
    runs = [(t["id"], arm, r) for r in range(1, repeat + 1) for t in tasks for arm in arms]
    random.Random(seed).shuffle(runs)  # so no setup always runs first, or always late in a session
    return runs


def main():
    global LOCAL
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", choices=sorted(SUITES), default="v2", help="which round to run (default v2)")
    ap.add_argument("--repeat", type=int, default=1, help="runs per task and setup (default 1)")
    ap.add_argument("--limit", type=int, help="stop after this many runs")
    ap.add_argument("--resume", nargs="?", const="latest", help="continue a batch (default: the latest)")
    ap.add_argument("--tasks", help="comma-separated task ids (default: all)")
    ap.add_argument("--arms", help="comma-separated setups to run: none, notes, mnemolith (default: all three)")
    ap.add_argument("--local", action="store_true", help="test the code in this repo instead of the PyPI release")
    ap.add_argument("--model", help="Claude model to use (default: Claude Code's default)")
    ap.add_argument("--dry-run", action="store_true", help="skip Claude; just prepare each run and check it")
    ap.add_argument("--keep", action="store_true", help="keep each run's repo copy (default: only failures)")
    args = ap.parse_args()

    for tool in ("git", "make", "uv") + (() if args.dry_run else ("claude",)):
        if not shutil.which(tool):
            sys.exit(f"{tool} isn't installed or isn't on PATH.")

    if args.resume:
        batches = sorted(p for p in RUNS.glob("*") if p.is_dir())
        if not batches:
            sys.exit("No batch to resume.")
        batch = RUNS / args.resume if args.resume != "latest" else batches[-1]
        meta = json.loads((batch / "batch.json").read_text())
    else:
        known = getattr(load_suite(args.suite), "ARMS", ARMS)
        arms = [a.strip() for a in args.arms.split(",")] if args.arms else known
        unknown = [a for a in arms if a not in known]
        if unknown:
            sys.exit(f"Unknown setup {unknown[0]!r}. Round {args.suite} has: {', '.join(known)}.")
        if args.local:
            LOCAL = HERE.parent
        batch = RUNS / datetime.now().strftime("%Y%m%d-%H%M%S")
        batch.mkdir(parents=True)
        meta = {"suite": args.suite, "repeat": args.repeat, "seed": random.randrange(1 << 30),
                "mnemolith": local_version() if LOCAL else MNEMOLITH, "local": str(LOCAL) if LOCAL else None,
                "arms": arms, "model": args.model, "dry_run": args.dry_run,
                "started": datetime.now().isoformat(timespec="seconds")}
        (batch / "batch.json").write_text(json.dumps(meta, indent=2))
    if meta.get("local"):  # a resumed batch keeps testing what it started with
        LOCAL = Path(meta["local"])

    suite = load_suite(meta.get("suite", "v1"))
    results_file = batch / "results.jsonl"
    done = set()
    if results_file.exists():
        done = {(r["task"], r["arm"], r["rep"]) for r in map(json.loads, results_file.read_text().splitlines())}
    wanted = set(args.tasks.split(",")) if args.tasks else None
    todo = [k for k in plan(suite.TASKS, meta["repeat"], meta["seed"], meta.get("arms") or getattr(suite, "ARMS", ARMS))
            if k not in done and (not wanted or k[0] in wanted)]
    if args.limit:
        todo = todo[:args.limit]
    tasks = {t["id"]: t for t in suite.TASKS}
    print(f"Batch {batch.name} (round {meta.get('suite', 'v1')}, Mnemolith {meta.get('mnemolith')}): "
          f"{len(done)} done, running {len(todo)} now.")

    for n, (task_id, arm, rep) in enumerate(todo, 1):
        task = tasks[task_id]
        name = f"{task_id}-{arm}-{rep}"
        run_dir = batch / name
        project = f"eval-{batch.name}-{name}"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        print(f"[{n}/{len(todo)}] {name} ...", end=" ", flush=True)
        team_round = getattr(suite, "TEAM", False)
        first = None
        if team_round:
            first, result = run_team(suite, task, run_dir, arm, project, meta["model"], batch, name, args.dry_run)
            if (first and hit_limit(first)) or (result and hit_limit(result)):
                clean_team(suite, run_dir, arm, project, keep=False)
                print("hit the Claude usage limit. Stopping; run `python3 run.py --resume` later.")
                break
            if result is None:
                result = {"seconds": 0, "tool_calls": 0, "memory_calls": 0, "answer": "", "turns": None,
                          "tokens": None, "cost_usd": None, "error": False, "model": None}
        else:
            prepare(suite, run_dir, arm, project)
        if team_round:
            pass
        elif args.dry_run:
            result = {"seconds": 0, "tool_calls": 0, "memory_calls": 0, "answer": "", "turns": None,
                      "tokens": None, "cost_usd": None, "error": False, "model": None}
        else:
            config = mcp_config(run_dir, arm, project)
            if task.get("first"):
                # Session 1 mentions the fact in passing; session 2, a fresh Claude Code session, needs it.
                first = ask_claude(run_dir, task["first"], config, meta["model"], batch / f"{name}.first.jsonl")
                if hit_limit(first):
                    print("hit the Claude usage limit. Stopping; run `python3 run.py --resume` later.")
                    break
                sh(["git", "add", "-A"], run_dir)
                sh(["git", "-c", "user.name=eval", "-c", "user.email=eval@example.com", "commit", "-q",
                    "--allow-empty", "-m", "Session 1"], run_dir)
            result = ask_claude(run_dir, task["prompt"], config, meta["model"], batch / f"{name}.transcript.jsonl")
            if hit_limit(result):
                print("hit the Claude usage limit. Stopping; run `python3 run.py --resume` later.")
                break
        passed, note = task["check"](run_dir, result["answer"])
        row = {"task": task_id, "arm": arm, "rep": rep, "stale_task": bool(task.get("stale")),
               "cross_session": bool(task.get("cross_session")), "passed": passed, "note": note, **result}
        if first:
            row["first_turns"], row["first_memory_calls"] = first["turns"], first["memory_calls"]
            row["first_cost_usd"] = first["cost_usd"]
        with results_file.open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(("pass" if passed else "FAIL") + f" ({result['seconds']}s, {result['turns']} turns)")
        if team_round:
            clean_team(suite, run_dir, arm, project, keep=args.keep or not passed)
            continue
        if arm == "mnemolith":
            sh(uvx("remove", project, "--yes"), run_dir, check=False)
        if passed and not args.keep:
            shutil.rmtree(run_dir, ignore_errors=True)

    print(f"Results: {results_file}\nSummary: python3 report.py {batch.name}")


if __name__ == "__main__":
    main()
