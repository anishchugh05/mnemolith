"""Team memory: the files in .mnemolith/team/, read without the engine.

The fake keys and tokens below are split into pieces ("ghp" + "_abc...") so that secret scanners, GitHub's
included, don't mistake this file for one that leaks real ones. None of them is real.

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
    "Deploy with " + "AKIA" + "IOSFODNN7EXAMPLE as the key",
    "CI uses " + "ghp" + "_abcdefghijklmnopqrstuvwxyz0123456789",
    "The staging db is postgres://admin:hunter2@db.internal:5432/shop",
    "Set OPENAI key " + "sk" + "-proj-abcdefghijklmnopqrstuvwx",
    "admin password: hunter2",
    "api_key = 9f8a7b6c5d",
    "-----BEGIN RSA " + "PRIVATE KEY-----",
    "The admin password is hunter2",
    "The staging token was ab12cd34ef56",
    "Local login uses pwd=Summer2024!",
    "the password for admin is Tr0ub4dor&3",
    "Our API key is 9f8a7b6c5d4e",
    "DB_PASSWORD=hunter2",
    "export SECRET_KEY='django-insecure-abc123'",
    "REDIS_AUTH_TOKEN: 9f8a7b6c5d4e",
    "login is admin / hunter2",
    "creds: admin:hunter2",
    "the pw is hunter2",
    "ssh root@10.0.0.5 with password Tr0ub4dor",
    "Authorization: Bearer abcdef1234567890abcdef1234567890",
    "GitLab uses " + "glpat" + "-abcdefghij1234567890",
    "HF token " + "hf" + "_abcdefghijklmnopqrstuvwxyzABCDEFGH",
    "npm token " + "npm" + "_abcdefghijklmnopqrstuvwxyz0123456789",
    "card 4111 1111 1111 1111 exp 12/27",
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
    "TOKEN_TTL=3600",
    "PASSWORD_MIN_LENGTH=12",
    "Set DB_PASSWORD in .env before running",
    "token: Bearer scheme",
    "login is handled in auth/login.py",
    "User: admin/editor roles are in roles.py",
    "Order 4111 2222 3333 4445 shipped",
    "users log in with password + OTP",
    "credentials: stored in Vault under shop/prod",
    "secret: ${{ secrets.NPM_TOKEN }}",
    "Password hashing uses argon2id",
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
    assert calls == ["MEM.GET"] and team.load(repo) == [] and "hunter2" not in (repo / "mnemolith.md").read_text()


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


# ---------- found while trying to break it before launch ----------

def git(repo, *args):
    import subprocess
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=repo,
                          capture_output=True, text=True)


def test_a_path_with_an_at_sign_survives_being_written_and_read(repo):
    team.init(repo)
    (repo / "packages" / "@acme" / "ui").mkdir(parents=True)
    (repo / "packages" / "@acme" / "ui" / "button.js").write_text("x\n")
    mem = team.add(repo, "Buttons live in the ui package.", ["packages/@acme/ui/button.js"])
    again = team.find(repo, mem.name)
    assert again.files == mem.files and again.files[0][0] == "packages/@acme/ui/button.js"
    assert team.changed(again, repo) == []


def test_a_folder_can_be_linked_and_any_change_inside_it_counts(repo):
    team.init(repo)
    (repo / "shop" / "payments").mkdir(parents=True)
    (repo / "shop" / "payments" / "gateway.py").write_text("def charge(): pass\n")
    mem = team.add(repo, "The payments folder belongs to the payments team.", ["shop/payments/"])
    assert [p for p, _ in mem.files] == ["shop/payments"]
    assert team.changed(team.find(repo, mem.name), repo) == []
    (repo / "shop" / "payments" / "refunds.py").write_text("x = 1\n")
    assert team.changed(team.find(repo, mem.name), repo) == ["shop/payments changed"]
    team.confirm(repo, mem.name)
    (repo / "shop" / "payments" / "gateway.py").write_text("def charge(): return 1\n")
    assert team.changed(team.find(repo, mem.name), repo) == ["shop/payments changed"]


def test_memories_in_any_language_get_a_real_file_name(repo):
    assert team.slug("价格以分为单位存储") == "价格以分为单位存储"
    assert team.slug("Les prix sont stockés en centimes") == "les-prix-sont-stockés-en-centimes"
    assert team.slug("!!!") == "memory"


def test_starting_a_team_memory_writes_the_list_and_its_merge_rule_once(repo):
    (repo / ".gitattributes").write_text("* text=auto")
    team.init(repo)
    assert (repo / "mnemolith.md").is_file() and not team.index_outdated(repo)
    rules = (repo / ".gitattributes").read_text()
    assert rules.startswith("* text=auto\n") and rules.count("mnemolith.md merge=union") == 1
    assert not team.ensure_merge_rule(repo)
    team.leave(repo)
    team.init(repo)  # joining touches nothing in the repo
    assert (repo / ".gitattributes").read_text() == rules


def test_two_people_sharing_on_different_branches_merge_without_a_conflict(repo, tmp_path):
    git(repo, "init", "-q")
    team.init(repo)
    team.add(repo, "Money is stored in cents.")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "start")
    main = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    git(repo, "checkout", "-qb", "a")
    team.add(repo, "Deploys go out on Tuesdays.")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "a")
    git(repo, "checkout", "-q", main); git(repo, "checkout", "-qb", "b")
    team.add(repo, "Never cache prices.")
    team.add(repo, "Api v1 is frozen.")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "b")
    merged = git(repo, "merge", "-q", "a", "-m", "merge")
    assert merged.returncode == 0, merged.stdout + merged.stderr
    assert len(team.current(repo)) == 4 and not team.index_outdated(repo)
    listed = (repo / "mnemolith.md").read_text()
    assert all(text in listed for text in ("Tuesdays", "Never cache", "frozen", "cents")) and "<<<<" not in listed


def test_a_list_left_in_conflict_is_rebuilt_by_sync(repo):
    team.init(repo)
    team.add(repo, "Money is stored in cents.")
    (repo / "mnemolith.md").write_text("<<<<<<< HEAD\n- a (a)\n=======\n- b (b)\n>>>>>>> x\n")
    assert team.index_outdated(repo) and team.write_index(repo) and not team.index_outdated(repo)


def test_a_starter_run_is_capped_and_the_pointer_is_added_once(repo, monkeypatch):
    team.init(repo)
    assert team.starter_limit() is None
    monkeypatch.setenv(team.STARTER_ENV, "30")
    assert team.starter_limit() == 30
    cmd = team.starter_command(repo, "claude", repo / "mcp.json", limit=30)
    assert cmd[:2] == ["claude", "-p"] and "--strict-mcp-config" in cmd and "Edit" not in cmd and "Write" not in cmd
    (repo / "CLAUDE.md").write_text("# shop\n")
    assert [p.name for p in team.add_pointer(repo)] == ["CLAUDE.md"]
    assert team.add_pointer(repo) == [] and (repo / "CLAUDE.md").read_text().count("mnemolith.md") == 1


def test_max_files_counts_the_files_the_text_names_too(repo):
    team.init(repo)
    for name in ("pyproject.toml", "server.json", "setup.cfg", "VERSION"):
        (repo / name).write_text("x\n")
    mem = team.add(repo, "Bump pyproject.toml, server.json, setup.cfg and VERSION together.", ["VERSION"], max_files=3)
    assert [p for p, _ in mem.files] == ["VERSION", "pyproject.toml", "server.json"]


def test_team_memorys_own_files_are_never_linked(repo):
    team.init(repo)
    mem = team.add(repo, "mnemolith.md is generated from .mnemolith/team/memories/.", ["mnemolith.md", ".mnemolith/team/README.md"])
    assert mem.files == []
    path = mem.path
    path.write_text(path.read_text().replace("---\nmnemolith", "---\nmnemolith", 1).replace(
        "status: active", "status: active\nfiles:\n  - mnemolith.md@000000000000", 1))
    assert team.confirm(repo, mem.name).files == []  # confirming drops an old link to it


# ---------- second round: duplicates, moves, parts of files, edits, conflicts, pull request check ----------

def test_sharing_what_the_team_already_has_is_refused(repo, monkeypatch):
    team.init(repo)
    team.add(repo, "Prices are stored in cents, never floats.")
    monkeypatch.setattr(server, "run", lambda *a: ["id", "m9", "project", "shop", "status", "active", "files", "",
                                                   "text", "Prices are stored in cents, never floats"])
    with pytest.raises(team.TeamError, match="already has this"):
        team.promote(repo, "shop", "m9")
    assert len(team.load(repo)) == 1


def test_a_renamed_file_is_followed_when_the_memory_is_confirmed(repo):
    git(repo, "init", "-q")
    team.init(repo)
    (repo / "shop").mkdir()
    (repo / "shop" / "pricing.py").write_text("RATE = 8\n")
    mem = team.add(repo, "The tax rate lives in the pricing module.", ["shop/pricing.py"])
    (repo / "shop" / "pricing.py").rename(repo / "shop" / "prices.py")
    assert team.changed(team.find(repo, mem.name), repo) == ["shop/pricing.py moved to shop/prices.py"]
    done = team.confirm(repo, mem.name, by="Sam Lee")
    assert [p for p, _ in done.files] == ["shop/prices.py"] and team.changed(done, repo) == []
    assert done.meta["confirmed"].endswith("by Sam Lee")
    (repo / "shop" / "prices.py").unlink()
    with pytest.raises(team.TeamError, match="no longer exists"):
        team.confirm(repo, mem.name)


CODE = '''RATE = 8


def product(p):
    return {"name": p["name"], "price": p["price_cents"]}


def listing(ps):
    return [product(p) for p in ps]


class Cart:
    def total(self):
        return 0
'''


def test_a_memory_can_follow_one_part_of_a_file(repo):
    team.init(repo)
    (repo / "api.py").write_text(CODE)
    mem = team.add(repo, "API product responses (api.py) are frozen for a partner.", ["api.py#product"])
    assert [p for p, _ in mem.files] == ["api.py#product"]  # the part replaces the whole file the text names
    (repo / "api.py").write_text(CODE.replace("for p in ps]", "for p in ps if p]").replace("RATE = 8", "RATE = 9"))
    assert team.changed(team.find(repo, mem.name), repo) == []  # other parts of the file changed
    (repo / "api.py").write_text(CODE.replace('p["price_cents"]}', 'p["price_cents"], "id": 1}'))
    assert team.changed(team.find(repo, mem.name), repo) == ["`product` in api.py changed"]
    (repo / "api.py").write_text(CODE.replace("def product(p)", "def item(p)"))
    assert team.changed(team.find(repo, mem.name), repo) == ["`product` is no longer in api.py"]


@pytest.mark.parametrize("text,name,first,last", [
    (CODE, "product", "def product(p):", 'return {"name": p["name"], "price": p["price_cents"]}'),
    (CODE, "Cart", "class Cart:", "return 0"),
    (CODE, "RATE", "RATE = 8", "RATE = 8"),
    ("# Shop\n\n## Releasing\n\nBump and tag.\n\n### Notes\n\nx\n\n## Testing\n\nRun make.\n", "Releasing", "## Releasing", "x"),
    ("server:\n  port: 80\n  timeout: 30\nclient:\n  retries: 2\n", "server", "server:", "timeout: 30"),
    ("function charge(amount) {\n  return amount;\n}\n\nconst other = 1;\n", "charge", "function charge(amount) {", "}"),
    ("public class Shop {\n    public static int total(int a) {\n        return a;\n    }\n}\n", "total",
     "public static int total(int a) {", "}"),
    ('[tool.hatch]\nartifacts = [\n  "a.jar",\n]\nname = "x"\n', "artifacts", "artifacts = [", "]"),
])
def test_parts_of_files_are_found_in_code_config_and_docs(text, name, first, last):
    part = team.section_text(text, name).split("\n")
    assert part[0].strip() == first and part[-1].strip() == last


def test_a_call_is_not_mistaken_for_the_definition():
    text = "def run():\n    return product(1)\n\n\ndef product(p):\n    return p\n"
    assert team.section_text(text, "product").startswith("def product(p):")
    assert team.section_text(text, "nothing_here") is None


def test_a_file_a_hand_edit_broke_is_held_back_and_named(repo):
    team.init(repo)
    write(repo, "open-header", "---\nstatus: active\nPrices are in cents.\n")
    write(repo, "typo", "---\nstatus: retierd\n---\nPrices are in cents.\n")
    write(repo, "fine", "Prices are in cents.\n")
    assert [m.name for m in team.current(repo)] == ["fine"]
    problems = {m.name: m.problem for m in team.load(repo)}
    assert "never closed" in problems["open-header"] and "retierd" in problems["typo"] and not problems["fine"]
    with pytest.raises(team.TeamError, match="can't be read"):
        team.confirm(repo, "typo")


def test_rewording_checks_for_secrets_and_records_who(repo):
    team.init(repo)
    mem = team.add(repo, "Deploys go out on Tuesdays.")
    with pytest.raises(team.SecretFound):
        team.edit(repo, mem.name, "The deploy password is hunter2")
    with pytest.raises(team.TeamError):
        team.edit(repo, mem.name, "   ")
    done = team.edit(repo, mem.name, "Deploys go out on Thursdays.", by="Sam Lee")
    assert done.text == "Deploys go out on Thursdays." and done.meta["edited"].endswith("by Sam Lee")
    assert "Thursdays" in (repo / "mnemolith.md").read_text() and "Tuesdays" not in (repo / "mnemolith.md").read_text()


def conflict(repo, mine, theirs):
    """A real git conflict between two branches that each wrote the same memory file."""
    git(repo, "init", "-q")
    team.init(repo)
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "start")
    main = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    git(repo, "checkout", "-qb", "theirs")
    name = team.add(repo, theirs, by="Priya").name
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "theirs")
    git(repo, "checkout", "-q", main)
    path = team.folder(repo) / "memories" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nmnemolith: 1\nstatus: active\nauthor: Sam\n---\n{mine}\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "mine")
    assert git(repo, "merge", "theirs").returncode != 0
    return name


def test_two_people_writing_the_same_memory_file_shows_both_and_keeps_one(repo):
    name = conflict(repo, "Deploys go out on Tuesdays.", "Deploys go out on Thursdays after standup.")
    mem = team.find(repo, name)
    assert team.conflicted(mem) and team.current(repo) == []
    assert team.sides(mem) == ("Deploys go out on Tuesdays.", "Deploys go out on Thursdays after standup.")
    with pytest.raises(team.TeamError, match="in conflict"):
        team.confirm(repo, name)
    kept = team.keep(repo, name, "theirs")
    assert not team.conflicted(kept) and kept.text == "Deploys go out on Thursdays after standup."
    assert kept.meta["author"] == "Priya" and "<<<<" not in kept.path.read_text()
    assert [m.name for m in team.current(repo)] == [name]


def test_taking_a_memory_back_retires_the_teams_copy(repo, monkeypatch):
    team.init(repo)
    mem = team.add(repo, "Deploys go out on Tuesdays.")
    saved = []
    monkeypatch.setattr(server, "embed", lambda text: ["0.1"])
    monkeypatch.setattr(server, "add_memory", lambda project, text, files, vector=None: (saved.append((project, text)) or ("m5", "saved", [])))
    back, new_id = team.unshare(repo, "shop", mem.name)
    assert new_id == "m5" and saved == [("shop", "Deploys go out on Tuesdays.")]
    assert back.status == "retired" and team.current(repo) == [] and back.path.is_file()


def test_new_arrivals_are_marked_until_looked_at_and_your_own_never_are(repo):
    team.init(repo)
    team.add(repo, "Deploys go out on Tuesdays.")  # mine: not news to me
    assert team.unseen(repo) == []
    write(repo, "from-a-pull", "Prices are in cents.\n")
    assert team.unseen(repo) == ["from-a-pull"]
    team.mark_seen(repo)
    assert team.unseen(repo) == []


def test_two_team_memories_saying_the_same_thing_are_paired(repo):
    team.init(repo)
    a = team.add(repo, "Deploys go out on Tuesdays after the standup.")
    write(repo, "also-deploys", "Deploys go out on Tuesdays after standup.\n")
    team.add(repo, "Prices are stored in integer cents.")
    assert [(x.name, y.name) for x, y, _ in team.twins(repo)] == [("also-deploys", a.name)]


def test_a_pull_request_is_checked_against_what_it_changed(repo):
    git(repo, "init", "-q")
    team.init(repo)
    (repo / "api.py").write_text(CODE)
    (repo / "docs").mkdir(); (repo / "docs" / "a.md").write_text("a\n")
    part = team.add(repo, "Product responses are frozen for a partner.", ["api.py#product"])
    whole = team.add(repo, "The API module has no side effects on import.", ["api.py"])
    docs = team.add(repo, "Docs are written in plain Markdown.", ["docs"])
    other = team.add(repo, "The packageManager is pnpm.", ["package.json"])
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "start")
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "checkout", "-qb", "feature")
    (repo / "api.py").write_text(CODE.replace("for p in ps]", "for p in ps if p]"))
    git(repo, "commit", "-qam", "listing skips empties")
    found = {f["name"]: f["why"] for f in team.pr_check(repo, base)}
    assert list(found) == [whole.name] and found[whole.name] == ["`api.py` changed"]  # `product` itself didn't change
    (repo / "api.py").write_text(CODE.replace('p["price_cents"]}', 'p["price_cents"], "id": 1}'))
    (repo / "docs" / "b.md").write_text("b\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "more")
    assert sorted(f["name"] for f in team.pr_check(repo, base)) == sorted([part.name, whole.name, docs.name])
    (repo / "docs" / "c.md").write_text("c\n")
    git(repo, "add", "docs/c.md")  # not committed yet: still counts
    assert docs.name in [f["name"] for f in team.pr_check(repo, base)]
    (repo / "docs" / "d.md").write_text("d\n")  # not even added to git: still counts
    team.confirm(repo, part.name); team.retire(repo, whole.name)
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "looked at the memories")
    found = team.pr_check(repo, base)
    assert [f["name"] for f in found] == [docs.name] and other.name not in str(found)
    comment = team.pr_comment(found)
    assert "1 team memory may be out of date" in comment and "docs/" in comment and team.pr_comment([]) == ""
    assert team.write_workflow(repo) and not team.write_workflow(repo)


def test_a_teammate_who_hasnt_joined_is_told_once_and_never_after_leaving(repo, monkeypatch):
    team.init(repo)
    team.add(repo, "Deploys go out on Tuesdays.")
    team.leave(repo)
    monkeypatch.setattr(server, "repo_root", lambda: repo)
    monkeypatch.setattr(server, "_NUDGED", False)
    assert server._team_invite() == []  # they chose to leave: stay quiet
    team.init(repo)
    settings = team._settings()
    settings["team"] = []  # a teammate who cloned the repo and never joined
    team._save_settings(settings)
    monkeypatch.setattr(server, "_NUDGED", False)
    first = server._team_invite()
    assert first and "mnemolith team init" in first[0] and "1 memory" in first[0]
    assert server._team_invite() == []  # once per session
