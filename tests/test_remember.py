"""The 2,000-character limit on a memory: remember refuses a longer one before anything is stored, and an older memory
that's longer anyway is cut visibly wherever AI tools see it or AGENTS.md / CLAUDE.md list it.

These need no engine: the engine, the embedding model and the project lookup are replaced with stand-ins.
Run with `python -m pytest tests`.
"""
import pytest

from mnemolith import server


@pytest.fixture
def saved(monkeypatch):
    """What remember stored, with the engine and the model stood in for."""
    stored = []
    monkeypatch.setattr(server, "embed", lambda text: ["0.1"])
    monkeypatch.setattr(server, "run", lambda *args: [])  # no similar memories
    monkeypatch.setattr(server, "project_name", lambda: "shop")
    monkeypatch.setattr(server, "_team_hits", lambda *args, **kwargs: [])
    monkeypatch.setattr(server, "add_memory", lambda project, text, files, vector=None:
                        (stored.append(text) or ("m1", "saved", "")))
    return stored


def test_a_fact_over_the_limit_is_refused_and_nothing_is_stored(saved, monkeypatch):
    monkeypatch.setattr(server, "embed", lambda text: pytest.fail("a refused fact shouldn't be embedded"))
    reply = server.remember("x" * (server.MAX_FACT + 1))
    assert reply.startswith("Not saved:") and "2,001 characters" in reply and "2,000" in reply
    assert saved == []


def test_a_fact_at_the_limit_is_saved(saved):
    reply = server.remember("x" * server.MAX_FACT)
    assert reply.startswith("Saved as m1")
    assert saved == ["x" * server.MAX_FACT]


def test_spaces_and_line_breaks_count_once_as_the_dashboard_counts_them(saved):
    fact = ("word " + " " * 20 + "\n\n") * 300  # 8,100 characters, but only 1,499 once spaces count once
    assert len(fact) > server.MAX_FACT and len(" ".join(fact.split())) <= server.MAX_FACT
    assert server.remember(fact).startswith("Saved as m1")


def test_a_replacing_fact_over_the_limit_is_refused_too(saved):
    reply = server.remember("y" * 5000, replaces="m7")
    assert reply.startswith("Not saved:") and saved == []


# ---------- older memories over the limit: cut where they're shown, with a note saying so ----------

OLD = "z" * 5000  # saved before remember had a limit


def test_a_memory_within_the_limit_is_shown_exactly_as_saved():
    text = "Tests need DOCKER_HOST set.\nRun them with `make test`."
    assert server._shown(text) == text and server._shown("x" * server.MAX_FACT) == "x" * server.MAX_FACT


def test_a_longer_memory_is_cut_and_says_where_to_read_the_rest():
    shown = server._shown(OLD)
    assert shown.startswith("z" * server.MAX_FACT + "…") and "z" * (server.MAX_FACT + 1) not in shown
    assert "5,000 characters; explain_memory shows all of it" in shown
    assert server._shown(OLD, in_file=True).endswith("… (cut: the full memory is in Mnemolith)")


def test_recall_shows_an_old_long_memory_cut(monkeypatch):
    monkeypatch.setattr(server, "check_staleness", lambda: [])
    monkeypatch.setattr(server, "embed", lambda text: ["0.1"])
    monkeypatch.setattr(server, "project_name", lambda: "shop")
    monkeypatch.setattr(server, "run", lambda *args: [("m1", "0.9", OLD)] if args[0] == "MEM.SEARCH" else [])
    for helper in ("_team_hits", "_team_on_hold", "_team_invite", "_setup_memories", "past_versions"):
        monkeypatch.setattr(server, helper, lambda *a, **k: [])
    monkeypatch.setattr(server, "MENDER_ON", False)
    reply = server.recall("database setup")
    assert "[m1]" in reply and "explain_memory shows all of it" in reply
    assert "z" * (server.MAX_FACT + 1) not in reply


def test_agents_md_lists_an_old_long_memory_cut(monkeypatch):
    monkeypatch.setattr(server, "project_name", lambda: "shop")
    monkeypatch.setattr(server, "run", lambda *args: [("m1", "active", "", OLD)])
    block = server.memory_block()
    assert "the full memory is in Mnemolith) (m1)" in block and "z" * (server.MAX_FACT + 1) not in block
