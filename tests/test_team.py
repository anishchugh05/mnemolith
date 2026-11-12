"""Team memory: the files in .mnemolith/team/, read without the engine.

Run with `python -m pytest tests`.
"""
import pytest

from mnemolith import config, server, team


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FAKE_EMBEDDER", True)
    monkeypatch.setattr(config, "HOME", tmp_path / "home")
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "home" / "settings.json")
    monkeypatch.setattr(team, "_vectors", None)
    root = tmp_path / "shop"
    root.mkdir()
    (root / "package.json").write_text('{"packageManager": "pnpm"}\n')
    return root


def write(root, name, text):
    path = team.folder(root) / "memories" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def vec(text):
    return server.fake_embed(text)


def test_init_creates_the_folder_then_joining_touches_nothing(repo):
    first = team.init(repo)
    assert first["created"] and team.joined(repo)
    assert (team.folder(repo) / "README.md").is_file()
    readme = (team.folder(repo) / "README.md").read_text()
    team.leave(repo)
    assert not team.joined(repo)
    second = team.init(repo)
    assert not second["created"] and team.joined(repo)
    assert (team.folder(repo) / "README.md").read_text() == readme


def test_a_hand_written_file_with_no_header_is_just_a_fact(repo):
    team.init(repo)
    write(repo, "pnpm", "We use pnpm, not npm.\n")
    (mem,) = team.current(repo)
    assert (mem.id, mem.text, mem.status, mem.files) == ("team:pnpm", "We use pnpm, not npm.", "active", [])


def test_header_is_read_and_unknown_lines_survive_a_rewrite(repo):
    team.init(repo)
    path = write(repo, "pnpm", "---\nmnemolith: 1\nstatus: active\nauthor: Sam Lee\nticket: SHOP-12\n"
                               "files:\n  - package.json\n---\nWe use pnpm, not npm.\n")
    mem = team.find(repo, "team:pnpm")
    assert mem.meta["author"] == "Sam Lee" and mem.files == [("package.json", "")]
    assert team.changed(mem, repo) == []  # no fingerprint yet, so nothing to compare
    team.confirm(repo, "pnpm")
    text = path.read_text()
    assert "ticket: SHOP-12" in text and "author: Sam Lee" in text and text.endswith("We use pnpm, not npm.\n")
    assert f"  - package.json@{team.fingerprint(repo / 'package.json')}" in text


def test_a_changed_file_marks_the_memory_until_it_is_confirmed(repo):
    team.init(repo)
    write(repo, "pnpm", "---\nfiles:\n  - package.json\n---\nWe use pnpm, not npm.\n")
    team.confirm(repo, "pnpm")
    (repo / "package.json").write_text('{"packageManager": "yarn"}\n')
    mem = team.find(repo, "pnpm")
    assert team.changed(mem, repo) == ["package.json changed"]
    assert "possibly outdated: package.json changed" in team.line(mem, repo)
    team.confirm(repo, "pnpm")
    assert team.changed(team.find(repo, "pnpm"), repo) == []


def test_line_endings_dont_change_the_fingerprint(repo):
    (repo / "a.txt").write_bytes(b"one\ntwo\n")
    (repo / "b.txt").write_bytes(b"one\r\ntwo\r\n")
    assert team.fingerprint(repo / "a.txt") == team.fingerprint(repo / "b.txt")


def test_confirming_a_memory_whose_file_is_gone_is_refused(repo):
    team.init(repo)
    write(repo, "old", "---\nfiles:\n  - gone.yml\n---\nDeploys read gone.yml.\n")
    with pytest.raises(team.TeamError):
        team.confirm(repo, "old")


def test_search_finds_by_meaning_and_skips_retired_and_newer_formats(repo):
    team.init(repo)
    write(repo, "pnpm", "We use pnpm, not npm, to install packages.\n")
    write(repo, "deploy", "Deploys go out on Tuesdays after the standup.\n")
    write(repo, "gone", "---\nstatus: retired\n---\nWe use pnpm to install packages, always.\n")
    write(repo, "future", "---\nmnemolith: 2\n---\nWe use pnpm to install packages, forever.\n")
    hits = team.search(repo, vec("which tool to install packages: pnpm or npm"), limit=5, floor=0.2)
    assert [m.name for _, m in hits] == ["pnpm"]


def test_nothing_is_read_until_this_person_joins(repo):
    write(repo, "pnpm", "We use pnpm, not npm.\n")
    assert team.search(repo, vec("pnpm npm"), 5) == []
    team.init(repo)
    assert len(team.search(repo, vec("pnpm npm"), 5)) == 1


def test_ids_cant_reach_outside_the_folder(repo):
    team.init(repo)
    (repo / "secret.md").write_text("hidden\n")
    assert team.find(repo, "team:../../secret") is None
    assert team.find(repo, "team:..") is None


def test_embeddings_are_cached_between_sessions(repo, monkeypatch):
    team.init(repo)
    write(repo, "pnpm", "We use pnpm, not npm.\n")
    team.search(repo, vec("pnpm"), 5)
    monkeypatch.setattr(team, "_vectors", None)  # a new session
    monkeypatch.setattr(server, "embed_many", lambda texts: pytest.fail("embedded again"))
    assert len(team.search(repo, vec("pnpm"), 5)) == 1


# ---------- the secret check ----------

@pytest.mark.parametrize("text", [
    "Deploy with AKIAIOSFODNN7EXAMPLE as the key",
    "CI uses ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "The staging db is postgres://admin:hunter2@db.internal:5432/shop",
    "Set OPENAI key sk-proj-abcdefghijklmnopqrstuvwx",
    "admin password: hunter2",
    "api_key = 9f8a7b6c5d",
    "-----BEGIN RSA PRIVATE KEY-----",
    "The admin password is hunter2",
    "The staging token was ab12cd34ef56",
    "Local login uses pwd=Summer2024!",
    "the password for admin is Tr0ub4dor&3",
    "Our API key is 9f8a7b6c5d4e",
])
def test_real_looking_secrets_are_always_refused(text):
    found = team.scan_secrets(text)
    assert found and any(f["hard"] for f in found)
    for f in found:
        assert len(f["shown"]) <= 5  # never printed in full


@pytest.mark.parametrize("text", [
    "The API key is read from STRIPE_KEY in the environment.",
    "token: $GITHUB_TOKEN, set in CI",
    "password = <ask ops>",
    "Tokens expire after 30 minutes; refresh in auth/session.py",
    "Passwords are hashed with bcrypt, cost 12.",
    "We use pnpm 9, not npm. See package.json.",
    "Staging is https://staging.example.com/admin",
    "The admin password is stored in 1Password, ask Priya",
    "The password is required for every login",
    "The token is rotated every 30 days",
    "The password is never logged",
    "Ask Priya for the staging password, it is not stored anywhere",
    "The Stripe key is read from STRIPE_KEY in the environment",
    "The API key is $STRIPE_KEY in CI",
])
def test_talking_about_secrets_is_fine(text):
    assert team.scan_secrets(text) == []


def test_maybe_secrets_are_left_for_a_person_to_decide(repo):
    team.init(repo)
    text = "The fix landed in commit 3f2a9c1b04de5a6b7c8d9e0f1a2b3c4d5e6f7a8b."
    with pytest.raises(team.SecretFound) as e:
        team.add(repo, text)
    assert e.value.overridable
    assert team.add(repo, text, allow_soft=True).text == text
    with pytest.raises(team.SecretFound) as e:
        team.add(repo, "admin password: hunter2", allow_soft=True)  # a certain one can't be waved through
    assert not e.value.overridable
    assert "hunter2" not in str(e.value)
    assert team.scan_secrets("Logs are in /Users/sam/shop/logs")[0]["hard"] is False


# ---------- adding, sharing, mnemolith.md ----------

def test_add_writes_a_file_links_real_files_only_and_updates_the_index(repo):
    team.init(repo)
    mem = team.add(repo, "We use pnpm, not npm.", ["package.json", "nope.txt", "../outside.txt"], by="Sam Lee")
    assert mem.name == "we-use-pnpm-not-npm" and mem.path.is_file()
    assert mem.files == [("package.json", team.fingerprint(repo / "package.json"))]
    again = team.find(repo, mem.id)
    assert again.meta["author"] == "Sam Lee" and again.meta["status"] == "active" and again.meta["mnemolith"] == "1"
    assert team.add(repo, "We use pnpm, not npm.").name == "we-use-pnpm-not-npm-2"
    index = (repo / "mnemolith.md").read_text()
    assert "- We use pnpm, not npm. (we-use-pnpm-not-npm)" in index and not team.index_outdated(repo)


def test_the_index_follows_hand_edits_and_never_carries_a_secret(repo):
    team.init(repo)
    team.add(repo, "We use pnpm, not npm.")
    write(repo, "typed-by-hand", "The admin password: hunter2\n")
    write(repo, "deploys", "Deploys go out on Tuesdays.\n")
    assert team.index_outdated(repo)
    assert team.write_index(repo) and not team.write_index(repo)
    index = (repo / "mnemolith.md").read_text()
    assert "Deploys go out on Tuesdays. (deploys)" in index and "hunter2" not in index
    team.retire(repo, "deploys")
    assert "Tuesdays" not in (repo / "mnemolith.md").read_text()
    assert team.find(repo, "deploys").status == "retired" and team.find(repo, "deploys").path.is_file()


def test_promoting_moves_the_memory_to_the_team(repo, monkeypatch):
    team.init(repo)
    calls = []

    def run(*args):
        calls.append(args)
        if args[0] == "MEM.GET":
            return ["id", "m7", "project", "shop", "status", "active", "files", "package.json@abc",
                    "text", "We use pnpm, not npm."]
        return "OK"

    monkeypatch.setattr(server, "run", run)
    mem = team.promote(repo, "shop", "m7")
    assert mem.files[0][0] == "package.json"
    assert calls[-1] == ("MEM.STATUS", "m7", "superseded", "REASON", f"moved to team memory {mem.id}")
    with pytest.raises(team.TeamError):
        team.promote(repo, "another-project", "m7")


def test_a_secret_stops_a_promotion_before_anything_is_written_or_retired(repo, monkeypatch):
    team.init(repo)
    calls = []

    def run(*args):
        calls.append(args[0])
        return ["id", "m8", "project", "shop", "status", "active", "files", "", "text", "db password: hunter2"]

    monkeypatch.setattr(server, "run", run)
    with pytest.raises(team.SecretFound):
        team.promote(repo, "shop", "m8")
    assert calls == ["MEM.GET"] and team.load(repo) == [] and not (repo / "mnemolith.md").exists()


def test_notes_files_become_team_memories_without_secrets_or_repeats(repo):
    team.init(repo)
    team.add(repo, "Run the tests with `make test`.")
    (repo / "CLAUDE.md").write_text("# shop\n\n## Conventions\n\n- We use pnpm, not npm. See package.json.\n"
                                    "- Run the tests with `make test`.\n- The admin password: hunter2\n"
                                    "- [ ] finish the docs\n")
    found = team.notes_to_import(repo)
    assert [n["text"] for n in found["notes"]] == ["Conventions: We use pnpm, not npm. See package.json."]
    assert found["notes"][0]["files"] == ["package.json"] and found["skipped_secrets"] == 1
    made = team.import_notes(repo)
    assert len(made) == 1 and team.notes_to_import(repo)["notes"] == []


def test_a_merge_conflict_is_noticed_and_kept_out_of_recall(repo):
    team.init(repo)
    path = write(repo, "deploys", "<<<<<<< HEAD\nDeploys go out on Tuesdays.\n=======\n"
                                  "Deploys go out on Thursdays.\n>>>>>>> sam/deploy-day\n")
    assert team.conflicted(team.find(repo, "deploys")) and team.current(repo) == []
    path.write_text("Deploys go out on Thursdays.\n")
    assert len(team.current(repo)) == 1


def test_a_team_memory_close_to_one_of_your_own_is_pointed_out(repo):
    team.init(repo)
    mem = team.add(repo, "We use pnpm, not npm, to install packages.")
    other = team.add(repo, "Deploys go out on Tuesdays after the standup.")
    close = team.overlaps(repo, [("m3", "We use pnpm, not npm, to install packages")], [mem, other])
    assert list(close) == [mem.id] and close[mem.id][0] == "m3"


def test_choices_are_per_person_and_leaving_drops_the_ai_permission(repo):
    team.init(repo)
    assert not team.ai_allowed(repo) and team.warning_on()
    team.allow_ai(repo, True)
    team.set_warning(False)
    assert team.ai_allowed(repo) and not team.warning_on() and team.joined(repo)
    team.leave(repo)
    assert not team.ai_allowed(repo)


def test_a_conflict_shows_both_wordings_and_holds_off_the_index(repo):
    team.init(repo)
    team.add(repo, "Deploys go out on Mondays.")
    path = team.find(repo, "deploys-go-out-on-mondays").path
    path.write_text("---\nmnemolith: 1\nstatus: active\n---\n<<<<<<< HEAD\nDeploys go out on Tuesdays.\n=======\n"
                    "Deploys go out on Thursdays.\n>>>>>>> b308ec8 (deploy day)\n")
    mem = team.find(repo, "deploys-go-out-on-mondays")
    assert team.sides(mem) == ("Deploys go out on Tuesdays.", "Deploys go out on Thursdays.")
    assert not team.index_outdated(repo)  # not until the conflict is settled
    path.write_text("---\nmnemolith: 1\nstatus: active\n---\nDeploys go out on Thursdays.\n")
    assert team.sides(team.find(repo, "deploys-go-out-on-mondays")) is None and team.index_outdated(repo)


def test_files_a_memory_names_are_linked_even_if_not_passed(repo):
    team.init(repo)
    (repo / "shop").mkdir()
    (repo / "shop" / "v1.py").write_text("x = 1\n")
    mem = team.add(repo, "API v1 (shop/v1.py) is frozen: never change its fields.")
    assert [p for p, _ in mem.files] == ["shop/v1.py"]


def test_a_plain_word_password_is_left_for_a_person(repo):
    found = team.scan_secrets("The wifi password is swordfish")
    assert found and not any(f["hard"] for f in found)
