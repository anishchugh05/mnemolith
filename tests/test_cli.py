"""Command-line behavior that doesn't need the engine. Run with `python -m pytest tests`."""
import pytest

from mnemolith import cli, engine, ui


def run(*argv):
    with pytest.raises(SystemExit) as e:
        cli.main(list(argv))
    return e.value.code


def fake_projects(monkeypatch, names):
    removed = []
    monkeypatch.setattr(engine, "ensure_running", lambda *a, **k: None)
    monkeypatch.setattr(ui, "api_projects", lambda *a: {"projects": [
        {"name": n, "counts": {"active": 2, "stale": 0}} for n in names]})
    monkeypatch.setattr(ui, "api_remove", lambda _, body: removed.append(body["name"]) or {"retired": 2})
    return removed


def test_remove_takes_a_folder_path_as_well_as_a_name(monkeypatch, tmp_path, capsys):
    folder = tmp_path / "mn-mender-test"
    folder.mkdir()
    removed = fake_projects(monkeypatch, ["mn-mender-test", "shop"])
    assert run("remove", str(folder), "--yes") == 0
    assert removed == ["mn-mender-test"]


def test_remove_by_name_still_works_and_unknown_names_list_the_projects(monkeypatch, capsys):
    removed = fake_projects(monkeypatch, ["shop"])
    assert run("remove", "shop", "--yes") == 0 and removed == ["shop"]
    assert run("remove", "nope", "--yes") == 1
    assert "Projects: shop." in capsys.readouterr().out


def test_the_pull_request_check_passes_quietly_without_a_team_memory(tmp_path, monkeypatch, capsys):
    import subprocess
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    assert run("team", "check", "--base", "HEAD", "--markdown") == 0
    assert capsys.readouterr().out == ""
