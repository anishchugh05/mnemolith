"""Runs the with-vs-without memory evaluation: each task, in a fresh copy of the sample repo, with Claude Code in
three setups.

  none       no project notes at all
  notes      the facts written in CLAUDE.md, the usual way people give an AI project knowledge
  mnemolith  the same facts as Mnemolith memories, tied to the files they depend on

After the notes and memories are written, a commit changes the tax rate and the free shipping threshold, so two
facts go stale, as notes do in real projects.

Usage (from this folder):
  python3 run.py                     start a new batch: 10 tasks x 3 setups x 1 = 30 runs
  python3 run.py --limit 10          stop after 10 runs (to spread a batch over days)
  python3 run.py --resume            continue the latest batch where it stopped
  python3 run.py --dry-run           everything except Claude, to check the setup works
  python3 report.py                  turn the results into a table

Needs: Claude Code signed in, uv, git, make, and Mnemolith's engine able to start (it starts itself).
Standard library only. Your own Claude Code settings, plugins and MCP servers are left out of every run, so the
three setups differ only in the notes or memories they get.
"""
import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from tasks import DRIFT, DRIFT_MESSAGE, FACTS, TASKS

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template"
RUNS = HERE / "runs"
MNEMOLITH = "0.3.2"  # the release under test; seed.py and the MCP server both use it
ARMS = ["none", "notes", "mnemolith"]

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


def notes_file():
    lines = ["# tinyshop", "", "Notes for working in this repo:", ""]
    return "\n".join(lines + [f"- {f['text']}" for f in FACTS]) + "\n"


def prepare(run_dir, arm, project):
    """A fresh copy of the sample repo, with this setup's notes or memories, then the commit that makes two
    facts stale."""
    shutil.copytree(TEMPLATE, run_dir, ignore=shutil.ignore_patterns("__pycache__"))
    sh(["git", "init", "-q"], run_dir)
    git_commit(run_dir, "tinyshop")
    if arm == "notes":
        (run_dir / "CLAUDE.md").write_text(notes_file())
        git_commit(run_dir, "Add notes for AI tools")
    if arm == "mnemolith":
        env = dict(os.environ, MNEMOLITH_PROJECT=project, MNEMOLITH_SOURCE="eval")
        sh(["uv", "run", "--quiet", "--no-project", "--python", "3.12", "--with", f"mnemolith=={MNEMOLITH}", "python",
            str(HERE / "seed.py")], run_dir, env=env)
    for rel, (old, new) in DRIFT.items():
        path = run_dir / rel
        path.write_text(path.read_text().replace(old, new))
    git_commit(run_dir, DRIFT_MESSAGE)


def mcp_config(run_dir, arm, project):
    servers = {}
    if arm == "mnemolith":
        servers["mnemolith"] = {"command": "uvx", "args": ["--python", "3.12", f"mnemolith=={MNEMOLITH}", "serve"],
                                "env": {"MNEMOLITH_PROJECT": project, "MNEMOLITH_SOURCE": "eval"}}
    path = run_dir.parent / f"{run_dir.name}.mcp.json"
    path.write_text(json.dumps({"mcpServers": servers}))
    return path


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
    return result.get("error") and any(w in text for w in ("usage limit", "rate limit", "limit reached"))


def plan(repeat, seed):
    runs = [(t["id"], arm, r) for r in range(1, repeat + 1) for t in TASKS for arm in ARMS]
    random.Random(seed).shuffle(runs)  # so no setup always runs first, or always late in a session
    return runs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repeat", type=int, default=1, help="runs per task and setup (default 1)")
    ap.add_argument("--limit", type=int, help="stop after this many runs")
    ap.add_argument("--resume", nargs="?", const="latest", help="continue a batch (default: the latest)")
    ap.add_argument("--tasks", help="comma-separated task ids (default: all)")
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
        batch = RUNS / datetime.now().strftime("%Y%m%d-%H%M%S")
        batch.mkdir(parents=True)
        meta = {"repeat": args.repeat, "seed": random.randrange(1 << 30), "mnemolith": MNEMOLITH,
                "model": args.model, "dry_run": args.dry_run, "started": datetime.now().isoformat(timespec="seconds")}
        (batch / "batch.json").write_text(json.dumps(meta, indent=2))

    results_file = batch / "results.jsonl"
    done = set()
    if results_file.exists():
        done = {(r["task"], r["arm"], r["rep"]) for r in map(json.loads, results_file.read_text().splitlines())}
    wanted = set(args.tasks.split(",")) if args.tasks else None
    todo = [k for k in plan(meta["repeat"], meta["seed"]) if k not in done and (not wanted or k[0] in wanted)]
    if args.limit:
        todo = todo[:args.limit]
    tasks = {t["id"]: t for t in TASKS}
    print(f"Batch {batch.name}: {len(done)} done, running {len(todo)} now.")

    for n, (task_id, arm, rep) in enumerate(todo, 1):
        task = tasks[task_id]
        name = f"{task_id}-{arm}-{rep}"
        run_dir = batch / name
        project = f"eval-{batch.name}-{name}"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        print(f"[{n}/{len(todo)}] {name} ...", end=" ", flush=True)
        prepare(run_dir, arm, project)
        if args.dry_run:
            result = {"seconds": 0, "tool_calls": 0, "memory_calls": 0, "answer": "", "turns": None,
                      "tokens": None, "cost_usd": None, "error": False, "model": None}
        else:
            result = ask_claude(run_dir, task["prompt"], mcp_config(run_dir, arm, project), meta["model"],
                                batch / f"{name}.transcript.jsonl")
            if hit_limit(result):
                print("hit the Claude usage limit. Stopping; run `python3 run.py --resume` later.")
                break
        passed, note = task["check"](run_dir, result["answer"])
        row = {"task": task_id, "arm": arm, "rep": rep, "stale_task": bool(task.get("stale")), "passed": passed,
               "note": note, **result}
        with results_file.open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(("pass" if passed else "FAIL") + f" ({result['seconds']}s, {result['turns']} turns)")
        if arm == "mnemolith":
            sh(["uvx", "--python", "3.12", f"mnemolith=={MNEMOLITH}", "remove", project, "--yes"], run_dir, check=False)
        if passed and not args.keep:
            shutil.rmtree(run_dir, ignore_errors=True)

    print(f"Results: {results_file}\nSummary: python3 report.py {batch.name}")


if __name__ == "__main__":
    main()
