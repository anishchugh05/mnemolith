"""The evaluation never touches your real Mnemolith: each run gets its own scratch home and port (eval/sandbox.py),
cleaned up however the run ends.

The first tests need no engine: a sleeping Python process whose command line names the scratch home's data folder
stands in for a run's engine. The last ones are the proof, with real engines: a stand-in for your own store is
running while the eval does a dry run (no Claude), and afterwards it is byte-for-byte unchanged and nothing of the
run is left. Run with `python -m pytest tests`.
"""
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
EVAL = REPO / "eval"

spec = importlib.util.spec_from_file_location("eval_sandbox", EVAL / "sandbox.py")
sandbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sandbox)


def wait_for(condition, timeout=30.0, step=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(step)
    return False


def fake_engine(home: Path, mention_home: bool = True) -> subprocess.Popen:
    """A process that looks like the run's engine to teardown: its command line names home/data, and engine.pid
    holds its PID, as the real engine's start() writes it."""
    args = ["--dir", str(home / "data")] if mention_home else ["--dir", "/somewhere/else"]
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", *args])
    (home / "engine.pid").write_text(str(p.pid))
    return p


@pytest.fixture
def caller(tmp_path, monkeypatch):
    """Whoever starts the eval, with their own Mnemolith home and port (never the real ones, in tests)."""
    home = tmp_path / "callers-mnemolith"
    home.mkdir()
    monkeypatch.setenv("MNEMOLITH_HOME", str(home))
    monkeypatch.setenv("MNEMOLITH_PORT", str(sandbox.free_port()))
    return home


@pytest.fixture
def root(tmp_path):
    return tmp_path / "runs" / ".sandboxes"


def open_box(root, name="batch-task-mnemolith-1"):
    return sandbox.Sandbox(name, root=root, cache=root.parent / ".cache")


# ---------- where a run's Mnemolith lives ----------

def test_a_run_gets_its_own_home_and_port_never_the_callers(caller, root):
    with open_box(root) as box:
        env = box.env(MNEMOLITH_PROJECT="eval-x")
        assert env["MNEMOLITH_HOME"] == str(box.home) and box.home.is_dir()
        assert int(env["MNEMOLITH_PORT"]) == box.port
        assert box.port not in (int(os.environ["MNEMOLITH_PORT"]), 7380)
        assert env["MNEMOLITH_PROJECT"] == "eval-x"
        # the big downloads are shared through the eval's cache, not ~/.mnemolith
        assert (box.home / "models").resolve() == (root.parent / ".cache" / "models").resolve()
    assert not box.home.exists()


def test_settings_that_point_at_a_real_project_are_not_passed_on(caller, root, monkeypatch):
    monkeypatch.setenv("MNEMOLITH_REPO", "/some/real/project")
    monkeypatch.setenv("MEMORY_REPO", "/some/real/project")
    with open_box(root) as box:
        env = box.env()
    assert "MNEMOLITH_REPO" not in env and "MEMORY_REPO" not in env


@pytest.mark.parametrize("where", ["is the caller's home", "is inside it", "contains it"])
def test_a_scratch_home_that_overlaps_the_callers_home_is_refused(caller, tmp_path, where):
    root, name = {"is the caller's home": (caller.parent, caller.name),
                  "is inside it": (caller, "run"),
                  "contains it": (caller.parent.parent, caller.parent.name)}[where]
    with pytest.raises(sandbox.SandboxError, match="overlaps your Mnemolith home"):
        with sandbox.Sandbox(name, root=root, cache=tmp_path / "cache"):
            pytest.fail("the sandbox shouldn't open")
    assert caller.is_dir() and list(caller.iterdir()) == []  # and nothing was written there


# ---------- clean-up, however the run ends ----------

@pytest.mark.parametrize("error", [ValueError, KeyboardInterrupt])
def test_clean_up_runs_when_the_run_fails_or_is_interrupted(caller, root, error):
    with pytest.raises(error):
        with open_box(root) as box:
            engine = fake_engine(box.home)
            raise error("the run went wrong")
    assert engine.wait(timeout=10) is not None  # stopped
    assert not box.home.exists()


def test_clean_up_runs_on_sigterm(caller, root, tmp_path):
    ready = tmp_path / "ready"
    script = f"""
import importlib.util, subprocess, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("s", {str(EVAL / "sandbox.py")!r})
s = importlib.util.module_from_spec(spec); spec.loader.exec_module(s)
s.install_signal_handlers()
with s.Sandbox("run", root=Path({str(root)!r}), cache=Path({str(tmp_path / "cache")!r})) as box:
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", "--dir", str(box.home / "data")],
                         start_new_session=True)
    (box.home / "engine.pid").write_text(str(p.pid))
    Path({str(ready)!r}).write_text(str(p.pid))
    time.sleep(120)
"""
    run = subprocess.Popen([sys.executable, "-c", script])
    assert wait_for(ready.exists), "the run never got going"
    engine_pid = int(ready.read_text())
    run.send_signal(signal.SIGTERM)
    assert run.wait(timeout=30) == 128 + signal.SIGTERM
    assert not (root / "run").exists()
    assert wait_for(lambda: not sandbox.alive(engine_pid), timeout=5)


def test_a_pid_that_isnt_the_runs_engine_is_never_killed(caller, root):
    box = open_box(root).__enter__()
    other = fake_engine(box.home, mention_home=False)  # e.g. the PID was reused by some other program
    try:
        box.__exit__(None, None, None)  # a warning, not an error: the batch goes on
        assert any("isn't this run's engine" in p for p in box.problems), box.problems
        assert other.poll() is None, "another program was killed"
        assert box.home.exists()  # kept as evidence, for sweep() to try again
    finally:
        other.kill()
        other.wait()
    assert sandbox.sweep(root) and not box.home.exists()


def test_an_engine_that_never_wrote_its_pid_file_is_still_stopped(caller, root):
    with open_box(root) as box:
        engine = fake_engine(box.home)
        (box.home / "engine.pid").unlink()  # killed between starting the engine and writing engine.pid
    assert engine.wait(timeout=10) is not None
    assert not box.home.exists() and box.problems == []


def test_a_similar_folder_name_is_not_mistaken_for_the_runs_engine(caller, root):
    box = open_box(root, "run").__enter__()
    neighbour = root / "run2"
    neighbour.mkdir()
    other = fake_engine(neighbour)  # --dir .../run2/data contains .../run/data as text, but isn't it
    try:
        box.__exit__(None, None, None)
        assert other.poll() is None, "another run's engine was killed"
        assert not box.home.exists()
    finally:
        other.kill()
        other.wait()


def test_java_is_linked_only_once_the_cache_has_it_and_a_download_is_kept(caller, root):
    cache = root.parent / ".cache"
    (cache / "java").mkdir(parents=True)  # an empty leftover folder isn't a Java to share
    with open_box(root, "empty-cache") as box:
        assert not (box.home / "java").exists()
    (cache / "java").rmdir()
    with open_box(root, "first") as box:
        assert not (box.home / "java").exists()  # nothing to share yet; Mnemolith downloads into the scratch home
        (box.home / "java" / "jdk" / "bin").mkdir(parents=True)  # as javart.download() leaves it
        (box.home / "java" / "jdk" / "bin" / "java").write_text("")
    assert (cache / "java" / "jdk" / "bin" / "java").is_file()  # handed to the cache
    with open_box(root, "second") as box:
        assert (box.home / "java").is_symlink() and (box.home / "java").resolve() == (cache / "java").resolve()
    assert (cache / "java" / "jdk" / "bin" / "java").is_file()  # deleting the run's home left the cache alone


def test_sweep_cleans_up_after_a_killed_run_and_leaves_a_running_one_alone(caller, root):
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    killed = open_box(root, "killed-run").__enter__()
    (killed.home / sandbox.OWNER).write_text(json.dumps({"pid": gone.pid, "port": killed.port}))
    engine = fake_engine(killed.home)

    live_owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    running = open_box(root, "running-run").__enter__()
    (running.home / sandbox.OWNER).write_text(json.dumps({"pid": live_owner.pid, "port": running.port}))
    try:
        report = sandbox.sweep(root)
        assert report == ["killed-run: cleaned up (its run was killed)"]
        assert engine.wait(timeout=10) is not None and not killed.home.exists()
        assert running.home.exists()
    finally:
        live_owner.kill()
        live_owner.wait()
        shutil.rmtree(running.home, ignore_errors=True)


# ---------- the proof: real engines, a dry run, and a stand-in for your own store ----------

def have_engine():
    try:
        from mnemolith import engine
        engine.find_java()
        return engine.JAR.exists()
    except Exception:
        return False


needs_engine = pytest.mark.skipif(not have_engine() or not shutil.which("uv") or not shutil.which("make"),
                                  reason="needs Java, the engine jar, uv and make")


def snapshot(home: Path) -> dict:
    """Every file under the home, with its size and SHA-256."""
    return {str(p.relative_to(home)): (p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(home.rglob("*")) if p.is_file()}


@pytest.fixture
def your_store(tmp_path):
    """A stand-in for your own Mnemolith: its own home, its engine running on its own port, one memory saved.
    Yields (env pointing at it, home, port). Stopped afterwards."""
    home, port = tmp_path / "your-mnemolith", sandbox.free_port()
    env = dict(os.environ, MNEMOLITH_HOME=str(home), MNEMOLITH_PORT=str(port), MNEMOLITH_EMBEDDER="fake",
               MNEMOLITH_PROJECT="your-project", MNEMOLITH_SOURCE="test")
    env.pop("MNEMOLITH_REPO", None)
    own = dict(env, PYTHONPATH=str(REPO))  # this repo's mnemolith, for the stand-in's own commands only
    out = subprocess.run([sys.executable, "-c", "from mnemolith import engine, server; engine.ensure_running(); "
                          "print(server.remember('Your own memory, which no eval run may change.'))"],
                         cwd=tmp_path, env=own, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and "Saved" in out.stdout, out.stdout + out.stderr
    try:
        yield env, home, port
    finally:
        subprocess.run([sys.executable, "-m", "mnemolith", "stop"], env=own, capture_output=True, timeout=60)


def dry_run(env, tmp_path, suite="v2", arms="mnemolith", task="price-speed"):
    """run.py --dry-run of one task, testing this repo's code. No Claude."""
    env = dict(env, EVAL_RUNS_DIR=str(tmp_path / "runs"), EVAL_SANDBOX_LOG=str(tmp_path / "sandbox-log.jsonl"))
    return subprocess.Popen([sys.executable, "run.py", "--dry-run", "--local", "--suite", suite,
                             "--arms", arms, "--tasks", task],
                            cwd=EVAL, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def your_memories(port):
    """What your stand-in project holds, asked of its engine (MEM.LIST only reads)."""
    import redis
    return redis.Redis(host="127.0.0.1", port=port, protocol=2, decode_responses=True).execute_command(
        "MEM.LIST", "your-project")


def check_nothing_left(tmp_path, home, port, before, memories_before):
    assert snapshot(home) == before, "your store's files changed"
    assert your_memories(port) == memories_before, "your project's memories changed"
    left = tmp_path / "runs" / ".sandboxes"
    assert not left.exists() or list(left.iterdir()) == [], f"left behind: {list(left.iterdir())}"
    for line in (tmp_path / "sandbox-log.jsonl").read_text().splitlines():
        run = json.loads(line)
        assert run["port"] != port and not Path(run["home"]).exists()
        assert not sandbox.port_open(run["port"]), "the run's engine is still running"


@pytest.mark.engine
@needs_engine
@pytest.mark.parametrize("suite, arms, task", [
    ("v2", "mnemolith", "price-speed"),
    # round 3: two teammates' personal memories and team memory, whose settings live in the home (team.py)
    ("v3", "mnemolith,team", "payments-ours"),
])
def test_a_dry_run_never_touches_your_store_even_while_your_engine_runs(your_store, tmp_path, suite, arms, task):
    env, home, port = your_store
    before, memories_before = snapshot(home), your_memories(port)
    assert len(memories_before) == 1
    time.sleep(1)
    assert snapshot(home) == before, "your store changes by itself, so this test can't tell anything"

    run = dry_run(env, tmp_path, suite, arms, task)
    out, err = run.communicate(timeout=600)
    assert run.returncode == 0, out + err

    runs = [json.loads(line) for line in (tmp_path / "sandbox-log.jsonl").read_text().splitlines()]
    assert len(runs) == len(arms.split(",")) and all(r["ended_by"] is None for r in runs)
    if arms == "mnemolith,team":
        runs = [r for r in runs if "mnemolith" in Path(r["home"]).name]  # team memory is files, not the engine
    assert runs[0]["saved_bytes"] > 0, "the run saved nothing in its sandbox, so this proves nothing"
    check_nothing_left(tmp_path, home, port, before, memories_before)


@pytest.mark.engine
@needs_engine
def test_a_dry_run_interrupted_partway_cleans_up_and_never_touches_your_store(your_store, tmp_path):
    env, home, port = your_store
    before, memories_before = snapshot(home), your_memories(port)
    run = dry_run(env, tmp_path)
    sandboxes = tmp_path / "runs" / ".sandboxes"

    def engine_started():
        return any((h / "engine.pid").exists() for h in sandboxes.glob("*")) if sandboxes.exists() else False
    assert wait_for(engine_started, timeout=300), "the run's engine never started"
    pid = int(next(sandboxes.glob("*/engine.pid")).read_text())
    run.send_signal(signal.SIGINT)
    out, err = run.communicate(timeout=120)
    assert run.returncode != 0 and "Stopped." in err, out + err
    assert wait_for(lambda: not sandbox.alive(pid), timeout=10), "the run's engine is still running"
    runs = [json.loads(line) for line in (tmp_path / "sandbox-log.jsonl").read_text().splitlines()]
    assert runs and runs[0]["ended_by"] == "KeyboardInterrupt"
    check_nothing_left(tmp_path, home, port, before, memories_before)
