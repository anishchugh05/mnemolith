"""Mender's rules: what they rewrite, and what they leave for a person (or the AI tool) to judge.

These need no engine: they exercise the pure functions that read diffs and rewrite memory text.
Run with `python -m pytest tests`.
"""
import subprocess

import pytest

from mnemolith import mender as m, server, team


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


# ---------- part links: the rules compare only the linked part, and never swap common words ----------
# The functions below are the real text of mnemolith/server.py at 0c44eea (old) and 4106aaf (new), copied in so the
# tests don't read this repo's history. Between them, content_hashes' docstring was reworded ("path -> hash of the
# file's current contents" became "link -> hash of what it points at now"), which once fed a garbled draft for a
# memory linked to check_staleness, a different function in the same file.

OLD_CONTENT_HASHES = r'''def content_hashes(paths: List[str], root: Optional[Path] = None) -> dict:
    """path -> hash of the file's current contents, or "missing"."""
    root = root or repo_root()
    result = {}
    for p in paths:
        try:
            result[p] = blob_hash(root / p) if (root / p).is_file() else "missing"
        except OSError:
            result[p] = "missing"
    return result
'''

OLD_CHECK_STALENESS = r'''def check_staleness(project: Optional[str] = None, root: Optional[Path] = None) -> List[str]:
    """Marks active memories stale when a file they depend on has changed since they were saved.
    Returns one line per memory that just went stale. Defaults to the current project and repo."""
    project = project or project_name()
    root = root or repo_root()
    newly_stale = []
    linked = []  # (id, text, [(path, saved hash)])
    for mid, _status, files, text in run("MEM.LIST", project):
        entries = [f.rsplit("@", 1) for f in files.split(",") if "@" in f]
        if entries:
            linked.append((mid, text, entries))
    if not linked:
        return newly_stale
    current = content_hashes(sorted({path for _, _, entries in linked for path, _ in entries}), root)
    for mid, text, entries in linked:
        changed = [path for path, saved in entries if current.get(path) != saved]
        if changed:
            what = ", ".join(f"{p} {'was deleted' if current.get(p) == 'missing' else 'changed'}" for p in changed)
            run("MEM.STATUS", mid, "stale", "REASON", f"{what} since this was saved")
            newly_stale.append(f"[{mid}] \"{_shown(text)}\" is now marked outdated: {what}.")
    return newly_stale
'''

NEW_CONTENT_HASHES = r'''def content_hashes(paths: List[str], root: Optional[Path] = None) -> dict:
    """link -> hash of what it points at now, or "missing". A whole file gets its git blob hash; a part of a file
    ("a.py#product", see team.split_link) gets the same 12-hex hash of that part that team memories use."""
    from . import team  # team imports this module
    root = root or repo_root()
    result = {}
    for p in paths:
        path, name = team.split_link(root, p)
        try:
            if not (root / path).is_file():
                result[p] = "missing"
            else:
                result[p] = team._now(root, p) if name else blob_hash(root / path)
        except OSError:
            result[p] = "missing"
    return result
'''

NEW_CHECK_STALENESS = r'''def check_staleness(project: Optional[str] = None, root: Optional[Path] = None) -> List[str]:
    """Marks active memories stale when a file (or part of a file) they depend on has changed since they were
    saved. Returns one line per memory that just went stale. Defaults to the current project and repo."""
    project = project or project_name()
    root = root or repo_root()
    newly_stale = []
    linked = []  # (id, text, [(link, saved hash)])
    for mid, _status, files, text in run("MEM.LIST", project):
        entries = [f.rsplit("@", 1) for f in files.split(",") if "@" in f]
        if entries:
            linked.append((mid, text, entries))
    if not linked:
        return newly_stale
    current = content_hashes(sorted({link for _, _, entries in linked for link, _ in entries}), root)
    for mid, text, entries in linked:
        changed = [(link, saved) for link, saved in entries if current.get(link) != saved]
        if changed:
            what = ", ".join(what_happened(link, saved, current.get(link), root) for link, saved in changed)
            run("MEM.STATUS", mid, "stale", "REASON", f"{what} {FILE_CHANGE}")
            newly_stale.append(f"[{mid}] \"{_shown(text)}\" is now marked outdated: {what}.")
    return newly_stale
'''

# the real team memory (decision-staleness-compares-file-contents-not), linked to server.py#check_staleness
STALENESS_MEMORY = (
    "Decision: staleness compares file contents, not commits. Each linked file is stored as path@<git blob hash> "
    "(git hash-object), and recall re-hashes the linked files before every search. That catches uncommitted edits, "
    "ignores commits that didn't touch the file, and needs no git hooks.")
GARBLED = ("compares it now", "linked it", "what linked", "what it")  # what the old rules drafted

SERVER_OLD = OLD_CONTENT_HASHES + "\n\n" + OLD_CHECK_STALENESS
SERVER_NEW = NEW_CONTENT_HASHES + "\n\n" + NEW_CHECK_STALENESS


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def new_repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    return tmp_path


def commit(root, rel, text, message="change"):
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", message)


def part_memory(root, rel, name, old_text, text, personal=False):
    """A memory linked to a part of a file, as the rules see it. A team memory holds the part's fingerprint, a
    personal one the part's hash: the same 12 hex either way (server.content_hashes). rules_files turns it into
    the git blob of the version in history, keeping the #symbol."""
    saved = team._section_print(old_text, name)
    return {"id": "t1", "status": "stale", "files": m.rules_files(root, [(f"{rel}#{name}", saved)], personal=personal),
            "text": text}


def evidence_text(draft, memory, root):
    """Everything shown as evidence for a memory: the draft's, and what change_evidence gives."""
    items = (draft["evidence"] if draft else []) + m.change_evidence(memory, root)
    return "\n".join(f"{e['label']}\n{e['detail']}" for e in items)


def staleness_case(tmp_path, personal):
    root = new_repo(tmp_path)
    commit(root, "mnemolith/server.py", SERVER_OLD, "old")
    memory = part_memory(root, "mnemolith/server.py", "check_staleness", SERVER_OLD, STALENESS_MEMORY, personal)
    commit(root, "mnemolith/server.py", SERVER_NEW, "new")
    return root, memory


KINDS = pytest.mark.parametrize("personal", [False, True], ids=["team", "personal"])


@KINDS
def test_staleness_memory_linked_to_check_staleness_is_not_garbled(tmp_path, personal):
    root, memory = staleness_case(tmp_path, personal)
    draft = m.draft_with_rules(memory, root)
    assert draft is not None
    for garbled in GARBLED:
        assert garbled not in draft["text"]
    assert draft["text"].startswith("Decision: staleness compares file contents, not commits.")
    assert draft["files"] == ["mnemolith/server.py#check_staleness"]


@KINDS
def test_docstring_of_another_function_is_not_evidence_for_a_part_link(tmp_path, personal):
    root, memory = staleness_case(tmp_path, personal)
    shown = evidence_text(m.draft_with_rules(memory, root), memory, root)
    assert shown  # check_staleness itself changed, so there is something to show
    assert "current contents" not in shown and "points at now" not in shown
    assert "def content_hashes" not in shown


@KINDS
def test_known_limit_part_draft_still_swaps_path_for_link_from_a_rename_inside_the_part(tmp_path, personal):
    # check_staleness itself renamed `path` to `link` (an identifier, not a value). Telling that apart from a value
    # change is the later docstring-and-rename work, so the draft still carries it. Fix after 0.4.4.
    root, memory = staleness_case(tmp_path, personal)
    draft = m.draft_with_rules(memory, root)
    assert "stored as link@<git blob hash>" in draft["text"]
    assert "path → link" in draft["reason"]


@KINDS
def test_part_link_ignores_changes_outside_the_part(tmp_path, personal):
    root = new_repo(tmp_path)
    old = "def alpha():\n    return 'ok'\n\n\ndef beta():\n    return 'postgres:16'\n"
    commit(root, "app.py", old, "old")
    memory = part_memory(root, "app.py", "alpha", old, "Alpha returns ok, and the database is Postgres (postgres:16).",
                         personal)
    commit(root, "app.py", old.replace("postgres:16", "mysql:8"), "new")
    draft = m.draft_with_rules(memory, root)
    assert draft is not None and draft["kind"] == "confirm"
    assert draft["text"] == memory["text"]  # no swap postgres -> mysql: that line isn't in the part
    shown = evidence_text(draft, memory, root)
    assert "mysql" not in shown and "postgres" not in shown
    assert draft["files"] == ["app.py#alpha"]


@KINDS
def test_value_change_inside_the_part_is_still_drafted(tmp_path, personal):
    root = new_repo(tmp_path)
    old = "def beta():\n    return 'unrelated'\n\n\ndef rate():\n    return 1.25\n"
    commit(root, "app.py", old, "old")
    memory = part_memory(root, "app.py", "rate", old, "The rate is 1.25 per call.", personal)
    commit(root, "app.py", old.replace("1.25", "1.5"), "new")
    draft = m.draft_with_rules(memory, root)
    assert draft is not None and draft["kind"] == "replace"
    assert draft["text"] == "The rate is 1.5 per call."
    assert draft["files"] == ["app.py#rate"]
    assert "1.5" in evidence_text(draft, memory, root)


@KINDS
def test_part_that_is_gone_gives_no_draft_and_the_evidence_names_it(tmp_path, personal):
    root = new_repo(tmp_path)
    old = "def alpha():\n    return 'ok'\n\n\ndef beta():\n    return 2\n"
    commit(root, "app.py", old, "old")
    memory = part_memory(root, "app.py", "alpha", old, "Alpha returns ok.", personal)
    commit(root, "app.py", old.replace("def alpha", "def alpha_v2"), "renamed")
    assert m.draft_with_rules(memory, root) is None
    assert "`alpha` is no longer in app.py" in evidence_text(None, memory, root)


# ---------- common words are never swapped, values next to them still are ----------

@pytest.mark.parametrize("pair", [("the", "what"), ("file", "it"), ("file contents", "it now")])
def test_swap_of_common_words_changes_nothing(pair):
    text = "Read the file contents now, and the file is cached."
    new, used = m.apply_swaps(text, [pair])
    assert new == text and not used


def test_swap_of_a_real_value_still_applies_next_to_common_words():
    new, used = m.apply_swaps("The database is postgres:16 for the app.", [("postgres:16", "mysql:8")])
    assert new == "The database is mysql:8 for the app." and used == [("postgres:16", "mysql:8")]


def whole_file_draft(tmp_path, rel, old, new, text):
    root = new_repo(tmp_path)
    commit(root, rel, old, "old")
    memory = {"id": "t1", "status": "stale", "files": f"{rel}@{server.blob_hash(root / rel)}", "text": text}
    commit(root, rel, new, "new")
    return m.draft_with_rules(memory, root)


@pytest.mark.parametrize("old, new, text, expected", [
    ('timeout: "30s"\n', 'timeout: "60s"\n', "The timeout is 30s", "The timeout is 60s"),
    ("PORT = 5432\n", "PORT = 6543\n", "The database listens on port 5432", "The database listens on port 6543"),
    ('timeout: "30s"  # the timeout\n', 'timeout: "60s"  # the timeout\n', "The timeout is 30s",
     "The timeout is 60s"),
    ("port = 5432  # is the db port\n", "port = 6543  # is the db port\n", "The database listens on port 5432",
     "The database listens on port 6543"),
    ("DEBUG = True\n", "DEBUG = False\n", "Debug is true.", "Debug is false."),
    ("cache: yes\n", "cache: no\n", "The cache is yes.", "The cache is no."),
], ids=["timeout", "port", "timeout-common-word-in-line", "port-common-word-in-line", "debug-true-false",
        "cache-yes-no"])
def test_value_change_next_to_a_common_word_is_still_drafted(tmp_path, old, new, text, expected):
    draft = whole_file_draft(tmp_path, "settings.txt", old, new, text)
    assert draft is not None and draft["kind"] == "replace"
    assert draft["text"] == expected


def test_known_limit_bare_number_does_not_update_a_value_with_a_unit(tmp_path):
    # TIMEOUT = 30 -> 60 gives the swap "30" -> "60", and "30s" in the memory isn't a whole-word "30", so the memory
    # is left as it is. Predates the common-word rule; to fix after 0.4.4.
    draft = whole_file_draft(tmp_path, "settings.py", "TIMEOUT = 30\n", "TIMEOUT = 60\n", "The timeout is 30s")
    assert draft is None or draft["text"] == "The timeout is 30s"


def test_known_limit_whole_file_link_still_swaps_path_for_link(tmp_path):
    # A whole-file link to server.py sees every function, so the path -> link rename still feeds the draft.
    # Docstring and rename handling is left for later. The common words in the reworded docstring ("the" ->
    # "what", "file" -> "it", "contents" -> "now") are no longer swapped.
    draft = whole_file_draft(tmp_path, "mnemolith/server.py", SERVER_OLD, SERVER_NEW, STALENESS_MEMORY)
    assert draft is not None
    assert "stored as link@<git blob hash>" in draft["text"]
    for garbled in GARBLED:
        assert garbled not in draft["text"]


@pytest.mark.parametrize("old, new, text, expected", [
    ("auth: true\n", "auth: false\n", "Auth is required for the API. The ping endpoint returns true.", None),
    ("DEBUG = True\n", "DEBUG = False\n", "Debug is true in dev, but false in prod.", None),
    ("DEBUG = True\n", "DEBUG = False\n", "Debug is true. The ping endpoint returns true.",
     "Debug is false. The ping endpoint returns true."),
    ("mode: none\n", "mode: null\n", "The mode is none.", "The mode is null."),
], ids=["other-sentence-holds-the-same-value", "sentence-holds-both-values", "only-the-sentence-about-the-line",
        "none-to-null"])
def test_a_value_swap_stays_in_the_sentence_about_the_changed_line(tmp_path, old, new, text, expected):
    draft = whole_file_draft(tmp_path, "settings.txt", old, new, text)
    if expected is None:
        assert draft is None
    else:
        assert draft is not None and draft["kind"] == "replace" and draft["text"] == expected


class _FakeDB:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = value


PART_SRC = "def alpha():\n    return 'ok'\n\n\ndef beta():\n    return 1\n"


def team_part_case(tmp_path, monkeypatch):
    """A repo with a team memory linked to app.py#alpha, then alpha edited (so the memory is flagged)."""
    from mnemolith import config
    monkeypatch.setattr(config, "FAKE_EMBEDDER", True)
    monkeypatch.setattr(config, "HOME", tmp_path / "home")
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "home" / "settings.json")
    monkeypatch.setattr(team, "_vectors", None)
    monkeypatch.setattr(m, "enabled", lambda: True)
    monkeypatch.setattr(server, "db", lambda store=_FakeDB(): store)
    (tmp_path / "shop").mkdir()
    root = new_repo(tmp_path / "shop")
    commit(root, "app.py", PART_SRC, "start")
    team.init(root)
    mem = team.add(root, "Alpha returns ok.", ["app.py#alpha"])
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "memory")
    commit(root, "app.py", PART_SRC.replace("'ok'", "'fine'"), "alpha edited")
    return root, mem


def test_team_memory_on_a_part_link_shows_the_files_diff_in_list_stale(tmp_path, monkeypatch):
    root, mem = team_part_case(tmp_path, monkeypatch)
    listed = m.describe_team_stale("shop", root)
    assert mem.id in listed and "deleted or moved" not in listed
    assert "app.py changed:" in listed and "+    return 'fine'" in listed


def test_team_draft_on_a_part_link_drifts_only_when_the_part_changes(tmp_path, monkeypatch):
    root, mem = team_part_case(tmp_path, monkeypatch)
    draft = m.refresh_team("shop", root)[mem.id]
    assert list(draft["file_hashes"]) == ["app.py#alpha"]
    assert m.drifted(draft, root) is False
    commit(root, "app.py", (root / "app.py").read_text().replace("return 1", "return 2"), "beta edited")
    assert m.drifted(draft, root) is False  # outside the part
    commit(root, "app.py", (root / "app.py").read_text().replace("'fine'", "'great'"), "alpha edited again")
    assert m.drifted(draft, root) is True  # inside the part
