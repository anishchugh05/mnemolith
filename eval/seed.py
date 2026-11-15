"""Saves the evaluation's facts as Mnemolith memories, each tied to its files, the same way the remember tool does.
Run by run.py inside a fresh copy of the sample repo:
    uv run --no-project --with mnemolith==<version> python seed.py
with MNEMOLITH_PROJECT set to that run's own project, so runs never see each other's memories.

Round 3 (two teammates) also uses it with EVAL_TEAM set: "start" writes the facts as team memories in the repo
and lets the AI share when asked, as teammate A would have set it up; "join" is teammate B running
`mnemolith team init` in their clone; "leave" undoes either on this computer when the run is over."""
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mnemolith import engine, server  # noqa: E402

FACTS = importlib.import_module(os.environ.get("EVAL_SUITE", "tasks")).FACTS
mode = os.environ.get("EVAL_TEAM", "")

if mode:
    from mnemolith import team
    root = Path.cwd()
    if mode == "leave":
        team.leave(root)
    else:
        team.init(root)
    if mode == "start":
        for fact in FACTS:
            team.add(root, fact["text"], fact["files"], by="eval", index=False)
        team.write_index(root)
        team.allow_ai(root, True)
    print(f"Team memory: {mode} in {root.name}")
    sys.exit(0)

engine.ensure_running()
for fact in FACTS:
    reply = server.remember(fact["text"], fact["files"])
    if not reply.startswith("Saved as"):
        sys.exit(f"Couldn't save a memory: {reply}")
print(f"Saved {len(FACTS)} memories in {server.project_name()}")
