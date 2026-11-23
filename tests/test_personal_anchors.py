"""Personal memories carry the same anchors team memories do: a link can point at one part of a file (`a.py#product`,
hashed by that part's content), and a file that moved is followed instead of reported as gone.

These need no engine, no Java and no network: the engine is an in-memory stand-in, the project is a tmp git repo,
and config.HOME is a tmp folder. Run with `python -m pytest tests`.
"""
import subprocess

import pytest

from mnemolith import config, mender, server, team

A = "def product(p):\n    return p * 1.25\n\n\ndef other(x):\n    return x + 1\n"


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FAKE_EMBEDDER", True)
    monkeypatch.setattr(config, "HOME", tmp_path / "home")
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "home" / "settings.json")
    monkeypatch.setattr(team, "_vectors", None)
    root = tmp_path / "shop"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / "a.py").write_text(A)
    git(root, "add", ".")
    git(root, "commit", "-qm", "one")
    return root


@pytest.fixture
def engine(monkeypatch):
    """An in-memory stand-in for the engine's memory commands."""
    mems = {}

    def run(*args):
        cmd = args[0]
        if cmd == "MEM.LIST":
            want = args[args.index("STATUS") + 1] if "STATUS" in args else "active"
            return [(m, d["status"], d["files"], d["text"]) for m, d in mems.items() if want in ("any", d["status"])]
        if cmd == "MEM.GET":
            d = mems.get(args[1])
            return None if d is None else [x for k, v in dict(d, id=args[1]).items() for x in (k, v)]
        if cmd == "MEM.STATUS":
            mems[args[1]]["status"] = args[2]
            mems[args[1]]["reason"] = args[4]
            return "OK"
        if cmd == "MEM.FILES":
            mems[args[1]]["files"] = args[2]
            return "OK"
        if cmd == "MEM.WHY":
            return ["history", [("1", "active->stale", mems[args[1]].get("reason", ""))]]
        raise AssertionError(args)

    monkeypatch.setattr(server, "run", run)
    return mems


def add(engine, root, text, files):
    engine["m1"] = {"status": "active", "text": text, "project": "shop", "files": server.link_files(files, root)}


def stale(engine, root):
    server.check_staleness("shop", root)
    return engine["m1"]["status"], engine["m1"].get("reason")


@pytest.fixture
def mender_on(monkeypatch):
    monkeypatch.setattr(mender, "enabled", lambda: True)
    monkeypatch.setattr(mender, "load_drafts", lambda p: {})
    monkeypatch.setattr(mender, "save_drafts", lambda p, d: None)


# a. whole files behave exactly as before

def test_a_whole_file_link_keeps_its_old_format_and_goes_stale_only_on_an_edit(engine, repo):
    add(engine, repo, "a.py has product", ["a.py"])
    assert engine["m1"]["files"] == f"a.py@{server.blob_hash(repo / 'a.py')}"
    assert server.check_staleness("shop", repo) == [] and engine["m1"]["status"] == "active"
    (repo / "a.py").write_text(A + "\n")
    assert stale(engine, repo) == ("stale", "a.py changed since this was saved")


# b, c. part links

def test_a_part_link_ignores_edits_elsewhere_in_the_file_but_not_to_the_part(engine, repo):
    add(engine, repo, "product adds a markup", ["a.py#product"])
    link, part_hash = engine["m1"]["files"].rsplit("@", 1)
    assert link == "a.py#product" and len(part_hash) == 12
    (repo / "a.py").write_text(A.replace("x + 1", "x + 2"))
    assert server.check_staleness("shop", repo) == [] and engine["m1"]["status"] == "active"
    (repo / "a.py").write_text(A.replace("p * 1.25", "p * 1.5"))
    status, reason = stale(engine, repo)
    assert status == "stale" and reason == "`product` in a.py changed since this was saved"
    assert reason.endswith(server.FILE_CHANGE)


def test_a_part_that_was_removed_is_reported_by_name(engine, repo):
    add(engine, repo, "product adds a markup", ["a.py#product"])
    (repo / "a.py").write_text("def other(x):\n    return x + 1\n")
    assert stale(engine, repo) == ("stale", "`product` is no longer in a.py since this was saved")


# d, e. moved files

def test_a_moved_file_is_followed_and_confirm_relinks_it(engine, repo):
    add(engine, repo, "a.py has product", ["a.py"])
    git(repo, "mv", "a.py", "b.py")
    assert stale(engine, repo) == ("stale", "a.py moved to b.py since this was saved")
    assert server.confirm("m1", repo) is True
    assert engine["m1"]["files"] == f"b.py@{server.blob_hash(repo / 'b.py')}"
    assert engine["m1"]["status"] == "active"
    assert server.check_staleness("shop", repo) == []


def test_a_moved_file_is_followed_even_when_its_name_is_not_unique(engine, repo):
    (repo / "sub").mkdir()
    (repo / "sub" / "a.py").write_text("something else entirely\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "two")
    add(engine, repo, "a.py has product", ["a.py"])
    git(repo, "mv", "a.py", "b.py")
    assert stale(engine, repo) == ("stale", "a.py moved to b.py since this was saved")
    server.confirm("m1", repo)
    assert engine["m1"]["files"].startswith("b.py@") and engine["m1"]["status"] == "active"


# f. refused symbols

@pytest.mark.parametrize("bad", ["a.py#x,y", "a.py#x@y"])
def test_a_symbol_that_would_break_the_link_list_is_refused_and_nothing_is_stored(bad, monkeypatch):
    monkeypatch.setattr(server, "project_name", lambda: "shop")
    monkeypatch.setattr(server, "embed", lambda *a, **k: pytest.fail("a refused fact shouldn't be embedded"))
    monkeypatch.setattr(server, "add_memory", lambda *a, **k: pytest.fail("a refused fact shouldn't be stored"))
    assert server.remember("a fact", files=[bad]).startswith("Not saved:")


# g. promote / unshare

def test_promote_keeps_a_part_link_as_a_team_part_link_and_unshare_brings_it_back(engine, repo, monkeypatch):
    team.init(repo)
    add(engine, repo, "product adds a 1.25 markup", ["a.py#product"])
    files = engine["m1"]["files"]
    mem = team.promote(repo, "shop", "m1")
    assert [name for name, _ in mem.files] == ["a.py#product"]
    assert mem.files[0][1] == files.rsplit("@", 1)[1]
    saved = []
    monkeypatch.setattr(server, "embed", lambda text: ["0.1"])
    monkeypatch.setattr(server, "add_memory",
                        lambda project, text, f, vector=None: (saved.append(f) or ("m5", "saved", [])))
    team.unshare(repo, "shop", mem.name)
    assert saved == [files]


# h, i. Mender

def test_mender_finds_the_old_part_in_git_history_and_drafts_the_swap(engine, repo, mender_on):
    add(engine, repo, "product multiplies by 1.25", ["a.py#product"])
    (repo / "a.py").write_text(A.replace("p * 1.25", "p * 1.5"))
    git(repo, "commit", "-qam", "three")
    server.check_staleness("shop", repo)
    draft = mender.refresh("shop", repo)["m1"]
    assert draft["kind"] == "replace" and draft["text"] == "product multiplies by 1.5"
    assert draft["files"] == ["a.py#product"]


def test_mender_leaves_a_part_saved_from_an_uncommitted_version_for_a_decision(engine, repo, mender_on, monkeypatch):
    (repo / "a.py").write_text(A.replace("p * 1.25", "p * 1.05"))  # saved before it was ever committed
    add(engine, repo, "product multiplies by 1.05", ["a.py#product"])
    (repo / "a.py").write_text(A.replace("p * 1.25", "p * 1.06"))
    server.check_staleness("shop", repo)
    assert mender.refresh("shop", repo) == {}
    monkeypatch.setattr(mender, "describe_team_stale", lambda *a: "")
    assert "`product` in a.py: changed; the saved version isn't in git" in mender.describe_stale("shop", repo)


# j. a part link whose file moved to another folder

def test_a_part_link_follows_its_file_to_another_folder_and_confirm_relinks_it(engine, repo):
    add(engine, repo, "product adds a markup", ["a.py#product"])
    (repo / "lib").mkdir()
    git(repo, "mv", "a.py", "lib/a.py")
    assert stale(engine, repo) == ("stale", "a.py moved to lib/a.py since this was saved")
    assert server.confirm("m1", repo) is True
    link, part_hash = engine["m1"]["files"].rsplit("@", 1)
    assert link == "lib/a.py#product" and len(part_hash) == 12 and engine["m1"]["status"] == "active"
