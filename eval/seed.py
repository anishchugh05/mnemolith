"""Saves the evaluation's facts as Mnemolith memories, each tied to its files, the same way the remember tool does.
Run by run.py inside a fresh copy of the sample repo:
    uv run --no-project --with mnemolith==<version> python seed.py
with MNEMOLITH_PROJECT set to that run's own project, so runs never see each other's memories."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mnemolith import engine, server  # noqa: E402

from tasks import FACTS  # noqa: E402

engine.ensure_running()
for fact in FACTS:
    reply = server.remember(fact["text"], fact["files"])
    if not reply.startswith("Saved as"):
        sys.exit(f"Couldn't save a memory: {reply}")
print(f"Saved {len(FACTS)} memories in {server.project_name()}")
