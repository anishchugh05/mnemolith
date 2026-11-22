"""Each eval run's own Mnemolith: a scratch home folder and a free port, so no run reads or writes your real
~/.mnemolith or reaches the engine you use day to day.

A scratch MNEMOLITH_HOME alone isn't enough: the engine is a server, and Mnemolith uses whatever engine of ours
answers on its port. With your engine running on 7380, a run with only a scratch home would still save into your
real store. So every run also gets its own port, and every process that can start or reach an engine (seed.py, the
`serve` MCP server, team setup) gets both.

    with Sandbox("batch-run") as box:
        subprocess.run(cmd, env=box.env(MNEMOLITH_PROJECT=...))

Leaving the `with` block, however it's left (done, failed, exception, Ctrl-C, or SIGTERM once
install_signal_handlers() has run), stops that run's engine and deletes its home. A run killed outright (SIGKILL,
power cut) can't clean up after itself; sweep(), which run.py calls on start, cleans up what such runs left.

Big downloads are shared through runs/.cache/, never ~/.mnemolith: the embedding model folder is linked into each
scratch home, so it downloads once per computer. Java is only needed when the computer has none; the first run that
downloads it hands its copy to the cache, and later runs link to that.

Standard library only, like run.py. Made for macOS and Linux, as the eval is (it needs make).
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# EVAL_RUNS_DIR moves everything the eval writes (batches, sandboxes, cache) somewhere else; the tests use it.
RUNS = Path(os.environ.get("EVAL_RUNS_DIR") or HERE / "runs").expanduser().resolve()
ROOT = RUNS / ".sandboxes"
CACHE = RUNS / ".cache"
OWNER = "owner.json"
DEFAULT_PORT = 7380  # Mnemolith's own default (mnemolith/config.py): never a sandbox's port, whatever is set
# Settings a run must never inherit from whoever started the eval: they would point it at a real project folder.
DROPPED = ("MNEMOLITH_REPO", "AETHER_REPO", "MEMORY_REPO")
KILL = getattr(signal, "SIGKILL", signal.SIGTERM)


class SandboxError(RuntimeError):
    pass


def caller_home() -> Path:
    """The Mnemolith home of whoever started the eval: what Mnemolith itself would use (mnemolith/config.py)."""
    value = os.environ.get("MNEMOLITH_HOME") or os.environ.get("AETHER_HOME")
    return Path(value).expanduser() if value else Path.home() / ".mnemolith"


def caller_port() -> int:
    for name in ("MNEMOLITH_PORT", "AETHER_PORT", "MEMORY_PORT"):
        if os.environ.get(name):
            return int(os.environ[name])
    return DEFAULT_PORT


def free_port(avoid=()) -> int:
    """A port nothing is listening on, from the OS. Another program could take it before the engine starts;
    the engine then fails to start and the run fails, which is safe."""
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in avoid:
            return port
    raise SandboxError("Couldn't find a free port for the run's engine.")


def port_open(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def saved_bytes(home: Path) -> int:
    """Bytes the engine in `home` has written to its append-only files: 0 for an engine nothing was saved in
    (every acknowledged write is appended to them). For the run log and the tests."""
    return sum(p.stat().st_size for p in (home / "data").rglob("*.aof") if p.is_file())


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # a child we started and that has exited stays a zombie until reaped; it isn't running
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        return done == 0
    except ChildProcessError:
        return True


def engines_of(home: Path) -> list:
    """PIDs of the processes whose command line names this home's data folder: the run's engine, found whether or
    not it got as far as writing engine.pid. Nothing else ever matches, so nothing else is ever signalled."""
    data = str(home / "data")
    try:
        p = subprocess.run(["ps", "-A", "-ww", "-o", "pid=,command="], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in p.stdout.splitlines():
        pid, _, command = line.strip().partition(" ")
        # the engine's command line has `--dir <data>`; match the whole argument so /x/data doesn't match /x/data2
        if pid.isdigit() and int(pid) != os.getpid() and (f"--dir {data} " in command + " "):
            pids.append(int(pid))
    return pids


def stop_engine(home: Path) -> list:
    """Stops the engine whose data is in `home`, if one runs. Returns problems, as sentences ([] when all went well)."""
    problems = []
    for pid in engines_of(home):
        for sig, wait in ((signal.SIGTERM, 10), (KILL, 5)):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                break
            deadline = time.time() + wait
            while time.time() < deadline and alive(pid):
                time.sleep(0.05)
            if not alive(pid):
                break
        else:
            problems.append(f"The run's engine (process {pid}) didn't stop.")
    try:
        pid = int((home / "engine.pid").read_text().strip())
    except (OSError, ValueError):
        pid = None
    if pid is not None and alive(pid) and pid not in engines_of(home) and not problems:
        problems.append(f"{home / 'engine.pid'} names process {pid}, which isn't this run's engine (its command line "
                        f"doesn't mention {home / 'data'}); it was left alone.")
    return problems


def keep_java(home: Path, cache: Path) -> None:
    """If this run downloaded Java (the computer has none), hand it to the cache so later runs don't download it."""
    mine, shared = home / "java", cache / "java"
    if mine.is_dir() and not mine.is_symlink() and any(mine.iterdir()) and not shared.exists():
        shared.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(mine, shared)
        except OSError:
            pass  # not worth failing a run over; the next one downloads again


def teardown(home: Path, cache: Path = None, check_port: bool = True) -> list:
    """Stops the engine in `home` and deletes the folder. Returns problems ([] when all went well).
    check_port: also make sure nothing answers on the run's port any more (only right just after the run: later,
    another program may have taken that port)."""
    problems = stop_engine(home)
    if check_port and not problems:
        try:
            port = int(json.loads((home / OWNER).read_text()).get("port") or 0)
        except (OSError, ValueError, AttributeError):
            port = 0
        if port and port_open(port):
            problems.append(f"Something still answers on the run's port {port} after its engine stopped.")
    if problems:
        return problems  # keep the folder: it's the evidence, and sweep() tries again next time
    if cache is not None:
        keep_java(home, cache)
    shutil.rmtree(home, ignore_errors=True)
    if home.exists():
        problems.append(f"Couldn't delete {home}.")
    return problems


class Sandbox:
    """One run's scratch home and port. See the module docstring."""

    def __init__(self, name: str, root: Path = None, cache: Path = None):
        self.root = Path(root or ROOT)
        self.cache = Path(cache or CACHE)
        self.home = self.root / name
        self.port = None
        self.problems = []

    def __enter__(self):
        real_home = caller_home().expanduser().resolve()
        home = self.home.resolve()
        if home == real_home or real_home in home.parents or home in real_home.parents:
            raise SandboxError(f"Refusing to use {home} as a scratch home: it overlaps your Mnemolith home "
                               f"{real_home}.")
        if self.home.exists():  # left by an earlier attempt at this same run
            problems = teardown(self.home, check_port=False)
            if problems:
                raise SandboxError(" ".join(problems))
        self.port = free_port(avoid={caller_port(), DEFAULT_PORT})
        self.home.mkdir(parents=True)
        (self.home / OWNER).write_text(json.dumps({"pid": os.getpid(), "port": self.port}))
        (self.cache / "models").mkdir(parents=True, exist_ok=True)
        # Java only once the cache really has one: an empty or broken folder there would stop Mnemolith downloading
        # its own (javart.download() can't replace a link)
        has_java = any((self.cache / "java").glob("*/bin/java")) or any((self.cache / "java").glob("*/Contents/Home/bin/java"))
        links = ["models"] + (["java"] if has_java else [])
        for name in links:
            try:
                (self.home / name).symlink_to(self.cache / name, target_is_directory=True)
            except OSError:
                pass  # no symlinks here: this run downloads its own copy into the scratch home
        return self

    def overrides(self) -> dict:
        """The settings that point Mnemolith at this sandbox, for an MCP server's env."""
        if self.port is None:
            raise SandboxError("The sandbox isn't open.")
        return {"MNEMOLITH_HOME": str(self.home), "MNEMOLITH_PORT": str(self.port), "MNEMOLITH_HOST": "127.0.0.1"}

    def env(self, base=None, **extra) -> dict:
        """A full environment for a subprocess: `base` (default: this process's), minus settings that point at a real
        project, plus this sandbox's settings, plus `extra`."""
        env = dict(os.environ if base is None else base)
        for name in DROPPED:
            env.pop(name, None)
        env.update(self.overrides())
        env.update({k: str(v) for k, v in extra.items()})
        return env

    def __exit__(self, kind, value, tb):
        log = os.environ.get("EVAL_SANDBOX_LOG")  # optional: one line per run, written just before clean-up
        if log:
            with open(log, "a") as f:
                f.write(json.dumps({"home": str(self.home), "port": self.port, "saved_bytes": saved_bytes(self.home),
                                    "ended_by": kind.__name__ if kind else None}) + "\n")
        self.problems = teardown(self.home, self.cache)
        if self.problems:
            # A warning, not an error: the run's results are already recorded, and what's left is in a scratch home,
            # never in yours. sweep() tries again when run.py next starts.
            print(f"\nClean-up of {self.home}: " + " ".join(self.problems), file=sys.stderr)
        return False


def sweep(root: Path = None, cache: Path = None) -> list:
    """Cleans up sandboxes whose run.py is gone (killed outright). Returns one line per sandbox it dealt with.
    A sandbox whose run.py is still running (another batch) is left alone."""
    root = Path(root or ROOT)
    report = []
    if not root.is_dir():
        return report
    for home in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            pid = int(json.loads((home / OWNER).read_text())["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            pid = None
            if time.time() - home.stat().st_mtime < 60:
                continue  # probably being set up right now
        if pid is not None and pid != os.getpid() and alive(pid):
            continue
        problems = teardown(home, Path(cache or CACHE), check_port=False)
        report.append(f"{home.name}: " + ("; ".join(problems) if problems else "cleaned up (its run was killed)"))
    return report


def install_signal_handlers() -> None:
    """Turns SIGTERM (and SIGHUP, where it exists) into SystemExit, so `with Sandbox(...)` blocks clean up on them as
    they do on Ctrl-C."""
    def leave(signum, _frame):
        raise SystemExit(128 + signum)
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), leave)
