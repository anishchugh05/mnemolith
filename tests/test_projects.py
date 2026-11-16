"""Each project's memories stay its own: the tools that act on a memory by id (m1, m2, ... numbered across every
project) refuse one from another project, so an AI working in one repo can't read or change another repo's memories.

These need no engine: a small stand-in holds memories for two projects, and this session is in "shop".
Run with `python -m pytest tests`.
"""
import pytest

from mnemolith import server

MEMORIES = {
    "m1": {"project": "shop", "text": "Prices are in cents."},
    "m2": {"project": "billing", "text": "Invoices go out on the 1st."},  # another repo's
    "m3": {"project": "shop", "text": "Prices are in cents, rounded half up."},
}


@pytest.fixture
def changes(monkeypatch):
    """The changes the tools asked the engine for, with the engine stood in for."""
    done = []

    def run(command, *args):
        if command in ("MEM.GET", "MEM.WHY"):
            m = MEMORIES.get(args[0])
            if m is None:
                return None
            reply = ["id", args[0], "project", m["project"], "text", m["text"], "status", "active", "files", "",
                     "source", "claude-code", "session", "", "created_at", "0"]
            return reply + (["history", []] if command == "MEM.WHY" else [])
        done.append((command, *args))
        return "OK"

    monkeypatch.setattr(server, "run", run)
    monkeypatch.setattr(server, "project_name", lambda: "shop")
    return done


@pytest.mark.parametrize("call", [
    lambda: server.explain_memory("m2"),
    lambda: server.mark_outdated("m2", "wrong"),
    lambda: server.confirm_memory("m2"),
    lambda: server.replace_memory("m2", "m1"),  # retiring another repo's memory
    lambda: server.replace_memory("m1", "m2"),  # or replacing with one
    lambda: server.resolve_conflict("m1", "m2", True),
    lambda: server.resolve_conflict("m2", "m1", False),
])
def test_another_projects_memory_is_refused_and_left_alone(changes, call):
    assert call() == "No memory with id m2 in this project."
    assert changes == []


def test_an_unknown_id_says_the_same(changes):
    assert server.mark_outdated("m99", "wrong") == "No memory with id m99 in this project."
    assert changes == []


def test_this_projects_memories_still_work(changes):
    assert server.explain_memory("m1").startswith('m1: "Prices are in cents."')
    assert server.mark_outdated("m1", "wrong") == "Marked m1 as outdated."
    assert server.confirm_memory("m1").startswith("Confirmed m1")
    assert server.replace_memory("m1", "m3") == "m1 retired; m3 replaces it."
    assert server.resolve_conflict("m3", "m1", False) == "Kept both m3 and m1."
    assert [c[0] for c in changes] == ["MEM.STATUS", "MEM.STATUS", "MEM.STATUS", "MEM.RESOLVE"]
