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


# k. a part link follows a rename (a.py -> b.py) when the part itself is unchanged

def test_a_part_link_follows_a_rename_to_a_new_name_and_confirm_relinks_it(engine, repo):
    add(engine, repo, "product adds a markup", ["a.py#product"])
    git(repo, "mv", "a.py", "b.py")
    assert stale(engine, repo) == ("stale", "a.py moved to b.py since this was saved")
    assert server.confirm("m1", repo) is True
    link, part_hash = engine["m1"]["files"].rsplit("@", 1)
    assert link == "b.py#product" and len(part_hash) == 12 and engine["m1"]["status"] == "active"
    assert server.check_staleness("shop", repo) == []


def test_a_rename_that_also_edits_the_part_is_not_followed_and_confirm_is_refused(engine, repo):
    add(engine, repo, "product adds a markup", ["a.py#product"])
    before = engine["m1"]["files"]
    git(repo, "mv", "a.py", "b.py")
    (repo / "b.py").write_text(A.replace("1.25", "1.5"))
    assert stale(engine, repo) == ("stale", "a.py was deleted since this was saved")
    with pytest.raises(server.LinkGone):
        server.confirm("m1", repo)
    assert engine["m1"]["files"] == before and engine["m1"]["status"] == "stale"


# l. confirming a memory whose part or file is gone is refused, and nothing changes

GONE_PART = ("m1 depends on `product` in a.py, which no longer exists. Nothing changed. If the fact is still true, "
             "save it again with the right files and replaces=\"m1\"; if not, retire it.")


def test_confirm_is_refused_when_the_part_is_gone_from_a_file_that_still_exists(engine, repo):
    add(engine, repo, "product adds a markup", ["a.py#product"])
    before = engine["m1"]["files"]
    (repo / "a.py").write_text("def other(x):\n    return x + 1\n")
    assert stale(engine, repo)[0] == "stale"
    with pytest.raises(server.LinkGone) as e:
        server.confirm("m1", repo)
    assert str(e.value) == GONE_PART
    assert engine["m1"]["status"] == "stale" and engine["m1"]["files"] == before


def test_confirm_is_refused_when_the_whole_file_is_gone(engine, repo):
    add(engine, repo, "a.py has product", ["a.py"])
    before = engine["m1"]["files"]
    (repo / "a.py").unlink()
    assert stale(engine, repo)[0] == "stale"
    with pytest.raises(server.LinkGone, match="m1 depends on a.py, which no longer exists. Nothing changed"):
        server.confirm("m1", repo)
    assert engine["m1"]["status"] == "stale" and engine["m1"]["files"] == before


def test_the_confirm_tool_returns_the_refusal_and_the_dashboard_gives_a_409_with_it(engine, repo, monkeypatch):
    from mnemolith import ui
    add(engine, repo, "product adds a markup", ["a.py#product"])
    before = engine["m1"]["files"]
    (repo / "a.py").write_text("def other(x):\n    return x + 1\n")
    stale(engine, repo)
    monkeypatch.setattr(server, "repo_root", lambda: repo)
    monkeypatch.setattr(server, "_in_project", lambda m: True)
    assert server.confirm_memory("m1") == GONE_PART
    monkeypatch.setattr(ui, "repo_for", lambda p: repo)
    with pytest.raises(ui.ApiError) as e:
        ui.api_confirm({}, {"id": "m1"})
    assert e.value.status == 409 and str(e.value) == GONE_PART
    assert engine["m1"]["status"] == "stale" and engine["m1"]["files"] == before


# m. several candidates for a renamed part: the one with the identical part wins

def test_a_renamed_part_picks_the_file_whose_part_is_identical(engine, repo):
    (repo / "c.py").write_text(A.replace("1.25", "9"))  # same suffix, an edited product
    (repo / "aa.py").write_text(A.replace("1.25", "8"))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "two")
    add(engine, repo, "product adds a markup", ["a.py#product"])
    git(repo, "mv", "a.py", "b.py")
    assert stale(engine, repo) == ("stale", "a.py moved to b.py since this was saved")
    server.confirm("m1", repo)
    assert engine["m1"]["files"].startswith("b.py#product@") and engine["m1"]["status"] == "active"


# n. Mender's "still true" approval refuses a gone part

@pytest.mark.parametrize("text", [None, "product adds a markup"])
def test_mender_still_true_is_refused_when_a_part_is_gone_and_nothing_is_written(engine, repo, mender_on,
                                                                              monkeypatch, text):
    add(engine, repo, "product adds a markup", ["a.py#product"])
    (repo / "a.py").write_text("def other(x):\n    return x + 1\n")
    assert stale(engine, repo)[0] == "stale"
    draft = {"kind": "confirm", "files": ["a.py#product"], "text": "product adds a markup", "by": "rules"}
    monkeypatch.setattr(mender, "load_drafts", lambda p: {"m1": draft})
    monkeypatch.setattr(mender, "get_memory", lambda m: dict(engine[m], id=m))
    monkeypatch.setattr(mender, "needs_decision", lambda m: True)
    monkeypatch.setattr(mender, "drifted", lambda d, r: False)
    dropped = []
    monkeypatch.setattr(mender, "drop_draft", lambda *a: dropped.append(a))
    before = dict(engine["m1"])
    with pytest.raises(mender.Refused) as e:
        mender.approve("shop", "m1", repo, text)
    assert e.value.status == 409
    assert str(e.value) == ("`product` is no longer in a.py, so this can't be confirmed or reworded as is. "
                            "Retire it.")
    assert engine["m1"] == before and dropped == []
    assert "@missing" not in engine["m1"]["files"]


def test_mender_reworded_approval_is_refused_when_a_part_is_gone_and_nothing_is_stored(engine, repo, mender_on,
                                                                                    monkeypatch):
    add(engine, repo, "product adds a markup", ["a.py#product"])
    (repo / "a.py").write_text("def other(x):\n    return x + 1\n")
    assert stale(engine, repo)[0] == "stale"
    draft = {"kind": "replace", "files": ["a.py#product"], "text": "product adds a bigger markup", "by": "rules"}
    monkeypatch.setattr(mender, "load_drafts", lambda p: {"m1": draft})
    monkeypatch.setattr(mender, "get_memory", lambda m: dict(engine[m], id=m))
    monkeypatch.setattr(mender, "needs_decision", lambda m: True)
    monkeypatch.setattr(mender, "drifted", lambda d, r: False)
    dropped, added = [], []
    monkeypatch.setattr(mender, "drop_draft", lambda *a: dropped.append(a))
    monkeypatch.setattr(server, "add_memory", lambda *a, **k: added.append(a) or ("m2", "added", ""))
    before = dict(engine["m1"])
    with pytest.raises(mender.Refused) as e:
        mender.approve("shop", "m1", repo, "product adds a markup of 50%")
    assert e.value.status == 409
    assert str(e.value) == ("`product` is no longer in a.py, so this can't be confirmed or reworded as is. "
                            "Retire it.")
    assert engine["m1"] == before and dropped == [] and added == []
    assert set(engine) == {"m1"}


# o. the moved-part search is cached until a candidate file changes

def test_a_repeated_moved_part_search_does_not_search_again_until_a_file_changes(engine, repo, monkeypatch):
    add(engine, repo, "product adds a markup", ["a.py#product"])
    link, saved = engine["m1"]["files"].rsplit("@", 1)
    (repo / "c.py").write_text(A.replace("1.25", "9"))  # a candidate with an edited product
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "two")
    git(repo, "rm", "-q", "a.py")  # the link's file is gone and no file has the part
    reads = []
    real = team._has_part

    def counting(*args):
        reads.append(args)
        return real(*args)

    monkeypatch.setattr(team, "_has_part", counting)
    assert team.moved_to(repo, link, saved) is None
    first = len(reads)
    assert first > 0
    assert team.moved_to(repo, link, saved) is None and team.moved_to(repo, link, saved) is None
    assert len(reads) == first  # repeats cost stats only: no file is read again
    # a candidate file changes: searched again (still not found)
    (repo / "c.py").write_text(A.replace("1.25", "77"))  # a different size, so no clock resolution matters
    assert team.moved_to(repo, link, saved) is None
    assert len(reads) > first
    # a new file with the part appears: searched again and found
    again = len(reads)
    (repo / "d.py").write_text(A)
    assert team.moved_to(repo, link, saved) == "d.py#product"
    assert len(reads) > again


def test_a_found_moved_part_is_reused_without_a_search_and_researched_after_it_changes(engine, repo, monkeypatch):
    add(engine, repo, "product adds a markup", ["a.py#product"])
    link, saved = engine["m1"]["files"].rsplit("@", 1)
    git(repo, "mv", "a.py", "b.py")
    assert team.moved_to(repo, link, saved) == "b.py#product"
    calls = []
    real_run = team.subprocess.run
    monkeypatch.setattr(team.subprocess, "run", lambda *a, **k: calls.append(a) or real_run(*a, **k))
    assert team.moved_to(repo, link, saved) == "b.py#product"
    assert calls == []  # no file listing, no search: only b.py's part hash is checked
    (repo / "b.py").write_text(A.replace("1.25", "2"))  # the found file's part changed
    (repo / "e.py").write_text(A)
    assert team.moved_to(repo, link, saved) == "e.py#product"
    assert calls != []
