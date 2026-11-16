"""remember's size limit: a memory too long to be one fact is refused before anything is stored.

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
