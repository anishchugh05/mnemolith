"""Mender's rules: what they rewrite, and what they leave for a person (or the AI tool) to judge.

These need no engine: they exercise the pure functions that read diffs and rewrite memory text.
Run with `python -m pytest tests`.
"""
from mnemolith import mender as m


def compare(old, new, memory):
    return m._compare(old, new, memory, m._key_words(memory), "file")


def test_value_swap_rewrites_the_memory_and_keeps_capitals():
    text, used = m.apply_swaps("The database is Postgres (postgres:16 image).",
                               m.swaps_for("    image: postgres:16", "    image: mysql:8"))
    assert text == "The database is MySQL (mysql:8 image)."
    assert ("postgres:16", "mysql:8") in used


def test_version_bump():
    text, _ = m.apply_swaps("We pin redis-py 5.0.0 (redis==5.0.0).", m.swaps_for("redis==5.0.0", "redis==5.2.1"))
    assert text == "We pin redis-py 5.2.1 (redis==5.2.1)."


def test_package_json_version():
    assert m.swaps_for('  "next": "^14.2.0",', '  "next": "^15.0.1",') == [("14.2.0", "15.0.1")]


def test_single_digits_alone_are_never_swapped():
    text, used = m.apply_swaps("Runs 8 workers", [("8", "9")])
    assert text == "Runs 8 workers" and not used


def test_change_to_a_value_the_memory_doesnt_mention_is_harmless():
    v = compare("db:\n  image: postgres:16\n", "db:\n  image: postgres:17\n", "We use Postgres")
    assert not v["touched"] and not v["undecided"]


def test_comment_only_change_is_harmless():
    v = compare("test:\n\tpytest\n", "# run everything\ntest:\n\tpytest\n", "Run the tests with make test")
    assert not v["touched"] and not v["undecided"]


def test_removed_line_the_memory_depends_on_needs_judgment():
    v = compare("DOCKER_HOST=x\nLOG=info\n", "LOG=info\n", "Tests need DOCKER_HOST set")
    assert v["touched"] and v["undecided"] and v["text"] == "Tests need DOCKER_HOST set"


def test_related_edit_with_no_matching_words_needs_judgment():
    v = compare("timeout: 30\n", "timeout: 60\n", "Requests time out after half a minute")
    assert v["text"] == "Requests time out after half a minute"


def test_changed_span_is_widened_to_whole_words():
    assert m._changed_span("image: postgres:16", "image: mysql:8") == ("postgres:16", "mysql:8")
    assert m._changed_span("redis==5.0.0", "redis==5.2.1") == ("5.0.0", "5.2.1")


def test_known_names_keep_their_own_capitals():
    assert m._styled("mysql", "Postgres") == "MySQL"
    assert m._styled("mysql", "postgres") == "mysql"
    assert m._styled("valkey", "REDIS") == "VALKEY"
