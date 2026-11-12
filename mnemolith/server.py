"""
The MCP server: the tools AI coding tools (Claude Code, Cursor, Codex, ...) call to use Mnemolith.

It turns text into embeddings, links memories to git file versions, and stores and searches them in the
Java engine over the Redis protocol. The engine is started automatically if it isn't running.
Settings are environment variables; see config.py.
"""
import hashlib
import os
import re
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import redis

from . import __version__, config, engine, legacy

try:
    from mcp.server.mcpserver import MCPServer  # mcp 2.x
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer  # mcp 1.x

SESSION = uuid.uuid4().hex[:8]  # each AI tool session starts its own copy of this server

# Shown to the AI by every MCP client, and also written into the tools' own instruction files by
# `mnemolith connect`, because AI tools otherwise default to their built-in memory.
INSTRUCTIONS = (
    "Mnemolith is this project's shared memory, kept across sessions and across AI tools (Claude Code, "
    "Cursor, Codex), and checked against the code so it never goes stale silently. For project knowledge, use "
    "its tools instead of any built-in memory, notes file, or memory directory:\n"
    "- At the start of every task, call recall with a short description of the task.\n"
    "- When the user asks you to remember, save, or note something about the project, or you learn something "
    "durable that a future session would need (a convention, a setup gotcha, a decision and its reason), call "
    "remember. Pass files= with the files the fact depends on, so it's retired automatically when they change. "
    "Don't save what recall already showed you.\n"
    "- If a memory turns out to be wrong, call mark_outdated. If a new fact makes an old one untrue, save it with "
    "remember(..., replaces=\"<old id>\") in one call."
)
MENDER_INSTRUCTIONS = (
    "\n- Mender is on: memories whose files changed get a drafted fix that the user approves in the dashboard. "
    "When recall says memories went stale, finish the user's task first; then call list_stale, check the code, "
    "and call propose_fix for each one without a draft, instead of remember/replace_memory. Nothing changes "
    "until the user approves.")


TEAM_INSTRUCTIONS = (
    "\n- This repo has a team memory: memories shared by everyone who works on it, kept as files in the repo. "
    "recall shows them first, with ids like team:name. When the user asks you to share something with the team, "
    "or wants their teammates' AI tools to know it, call share_with_team (with files= it depends on) instead of "
    "remember or a notes file.")


def instructions(mender_on: bool, team_on: bool = False) -> str:
    return INSTRUCTIONS + (TEAM_INSTRUCTIONS if team_on else "") + (MENDER_INSTRUCTIONS if mender_on else "")


def _team_at_startup() -> bool:
    """Whether the repo this session started in has a team memory this person uses. Worked out before the
    rest of this module exists, because it decides the instructions the server is created with."""
    try:
        start = Path(config.repo_override()).expanduser() if config.repo_override() else Path.cwd()
        if not start.is_dir():
            start = Path.cwd()
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start, capture_output=True, text=True, timeout=5)
        root = Path(top.stdout.strip()) if top.returncode == 0 and top.stdout.strip() else start
        return str(root.resolve()) in config.team_repos() and (root / ".mnemolith" / "team").is_dir()
    except Exception:
        return False


MENDER_ON = config.mender_enabled()  # read once per session: tools appear or not when the AI tool starts
TEAM_ON = _team_at_startup()  # likewise: share_with_team is only offered where there's a team to share with

try:
    mcp = MCPServer("mnemolith", instructions=instructions(MENDER_ON, TEAM_ON), version=__version__)
except Exception:  # mcp 1.x has no version argument; set it on the underlying server instead
    mcp = MCPServer("mnemolith", instructions=instructions(MENDER_ON, TEAM_ON))
    try:
        mcp._mcp_server.version = __version__
    except AttributeError:
        pass

def quiet_libraries() -> None:
    """Hides the download libraries' request logs, warnings and progress bars, which otherwise flood the
    terminal (and spill out after setup says it's done). Our own messages say what's happening."""
    import logging
    import warnings
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    warnings.filterwarnings("ignore", message="Cannot enable progress bars")  # fastembed asks; we said no
    # Windows without Developer Mode can't make symlinks; the model cache still works, it just warns loudly
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    warnings.filterwarnings("ignore", message=".*symlinks.*")
    os.environ.setdefault("HF_HUB_VERBOSITY", "error")
    for name in ("httpx", "httpcore", "huggingface_hub", "filelock", "urllib3"):
        logging.getLogger(name).setLevel(logging.ERROR)
    try:
        from loguru import logger  # fastembed logs its retries through loguru
        logger.disable("fastembed")
    except ImportError:
        pass


quiet_libraries()

_db = None
_embedder = None
_embedder_lock = threading.Lock()


class EngineDown(Exception):
    pass


# ---------- engine and embeddings ----------

def db() -> redis.Redis:
    global _db
    if _db is None:
        _db = engine.client()
        try:
            legacy.migrate_keys(_db)  # one-time rename of what Aether Cache stored, if the engine is up
        except Exception:
            pass
    return _db


def run(*args):
    """Sends one command to the engine. If the engine isn't running, starts it and tries once more."""
    try:
        return db().execute_command(*args)
    except (redis.ConnectionError, redis.TimeoutError):
        try:
            engine.ensure_running()
        except engine.EngineError as e:
            raise EngineDown(f"its engine isn't running and couldn't be started. {e}")
        return db().execute_command(*args)


def store_down(error: Exception) -> str:
    return (f"Mnemolith hit a problem: {error}\nRun `mnemolith doctor` in a terminal for help, "
            f"or tell us at {config.CONTACT_URL}")


def fake_embed(text: str, dim: int = 384) -> List[float]:
    """Testing only: hashes each word into a slot, so texts sharing words come out similar."""
    v = [0.0] * dim
    for word in text.lower().split():
        h = int(hashlib.md5(word.strip(".,!?\"'").encode()).hexdigest(), 16)
        v[h % dim] += 1.0 if (h >> 64) % 2 else -1.0
    return v


def embedder():
    """The embedding model, loaded (and downloaded, the first time) on first use."""
    global _embedder
    with _embedder_lock:
        if _embedder is None:
            from fastembed import TextEmbedding  # imported lazily so the server starts instantly
            config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
            # a fixed cache folder: fastembed's default is a temp folder, which the OS may wipe on reboot
            _embedder = TextEmbedding(config.MODEL, cache_dir=str(config.MODELS_DIR))
    return _embedder


def embed(text: str) -> List[str]:
    """Text -> embedding, formatted as strings ready to send."""
    if config.FAKE_EMBEDDER:
        vec = fake_embed(text)
    else:
        try:
            vec = next(iter(embedder().embed([text]))).tolist()
        except Exception as e:
            raise EngineDown(f"the embedding model couldn't be loaded ({e}). The first run downloads it from "
                             f"Hugging Face (huggingface.co, about 90 MB), so check that your network allows it.")
    return [f"{x:.6f}" for x in vec]


def embed_many(texts: List[str]) -> List[List[str]]:
    """Like embed, for many texts at once (the model is much faster in batches)."""
    if config.FAKE_EMBEDDER:
        vecs = [fake_embed(t) for t in texts]
    else:
        try:
            vecs = [v.tolist() for v in embedder().embed(texts)]
        except Exception as e:
            raise EngineDown(f"the embedding model couldn't be loaded ({e}). The first run downloads it from "
                             f"Hugging Face (huggingface.co, about 90 MB), so check that your network allows it.")
    return [[f"{x:.6f}" for x in v] for v in vecs]


def warm_up() -> None:
    """Starts the engine and loads the model in the background, so the AI's first call is fast."""
    def work():
        try:
            engine.ensure_running()
        except Exception:
            pass  # reported properly when a tool is actually called
        try:
            if not config.FAKE_EMBEDDER:
                embedder()
        except Exception:
            pass
    threading.Thread(target=work, daemon=True).start()


def when(ms: str) -> str:
    return datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d %H:%M")


# ---------- the project and its files ----------

def workdir() -> Path:
    override = config.repo_override()
    if override and Path(override).expanduser().is_dir():
        return Path(override).expanduser()
    return Path.cwd()


def git_toplevel() -> Optional[Path]:
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=workdir(),
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None  # git not installed, or too slow
    if top.returncode == 0 and top.stdout.strip():
        return Path(top.stdout.strip())
    return None


REPO_KEY = "mnemolith:repo:"  # mnemolith:repo:<project> -> the repo's folder, so the dashboard can check its files
_registered = set()


def project_name() -> str:
    """The project memories belong to: MNEMOLITH_PROJECT, else the git repo's folder name, else this folder's."""
    name = config.project_override() or (git_toplevel() or workdir()).name
    _remember_repo_location(name)
    return name


def _remember_repo_location(project: str) -> None:
    """Records where this project's repo lives (once per session), so `mnemolith ui`, which runs
    from anywhere, can still check its memories' files. Best effort: never gets in the AI's way."""
    top = git_toplevel()
    if top is None or (project, str(top)) in _registered:
        return
    _registered.add((project, str(top)))
    try:
        db().set(REPO_KEY + project, str(top))
    except Exception:
        _registered.discard((project, str(top)))


def repo_root() -> Path:
    """File paths in memories are relative to this: the git repo's top folder, else the current folder."""
    return git_toplevel() or workdir()


def relative(path: str, root: Optional[Path] = None) -> str:
    """Makes a path repo-relative, so memories still match if the repo is opened from a subfolder."""
    p = Path(path.strip())
    if p.is_absolute():
        try:
            return p.resolve().relative_to((root or repo_root()).resolve()).as_posix()
        except ValueError:
            return str(p)
    return p.as_posix()


def blob_hash(path: Path) -> str:
    """The same hash `git hash-object` gives for a file's contents, computed directly, so it works without git."""
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def content_hashes(paths: List[str], root: Optional[Path] = None) -> dict:
    """path -> hash of the file's current contents, or "missing"."""
    root = root or repo_root()
    result = {}
    for p in paths:
        try:
            result[p] = blob_hash(root / p) if (root / p).is_file() else "missing"
        except OSError:
            result[p] = "missing"
    return result


def link_files(paths: List[str], root: Optional[Path] = None) -> str:
    """["a.py", "b.yml"] -> "a.py@<hash>,b.yml@<hash>": the files plus a snapshot of their contents."""
    root = root or repo_root()
    rel = [relative(p, root) for p in paths if p.strip()]
    hashes = content_hashes(rel, root)
    return ",".join(f"{p}@{hashes[p]}" for p in rel)


def check_staleness(project: Optional[str] = None, root: Optional[Path] = None) -> List[str]:
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
            newly_stale.append(f"[{mid}] \"{text}\" is now marked outdated: {what}.")
    return newly_stale


def confirm(memory_id: str, root: Optional[Path] = None) -> Optional[bool]:
    """Marks a memory still true and re-links it to the current versions of its files.
    Returns whether it has linked files, or None if there's no such memory."""
    r = run("MEM.GET", memory_id)
    if r is None:
        return None
    files = [f.rsplit("@", 1)[0] for f in dict(zip(r[0::2], r[1::2]))["files"].split(",") if f]
    if files:
        run("MEM.FILES", memory_id, link_files(files, root), "REASON", "confirmed still true")
    run("MEM.STATUS", memory_id, "active", "REASON", "confirmed still true")
    return bool(files)


def status_reason(memory_id: str) -> str:
    """Why a memory has its current status, from the latest status change in its history."""
    r = run("MEM.WHY", memory_id) or []
    for _t, event, details in reversed(dict(zip(r[0::2], r[1::2])).get("history", [])):
        if "->" in event:
            return details
    return ""


FILE_CHANGE = "since this was saved"  # how check_staleness phrases a file-change reason
TEAM_MOVED = "moved to team memory"  # team.MOVED: why a personal memory was retired when it was shared


def add_memory(project: str, fact: str, linked_files: str = "", source: str = "",
               vector: Optional[List[str]] = None) -> tuple:
    """Saves a memory for a project. linked_files is "path@hash,..." as link_files returns.
    Returns the engine's (id, outcome, related id). Used by the remember tool and the dashboard."""
    args = ["MEM.ADD", project, fact, "SOURCE", source or config.SOURCE, "SESSION", SESSION]
    if linked_files:
        args += ["FILES", linked_files]
    return tuple(run(*args, "VECTOR", *(vector or embed(fact))))


def past_versions(project: str, vector: List[str], limit: int = 3) -> List[str]:
    """Retired, replaced, or possibly outdated memories close to a query, labeled so the AI knows what
    changed instead of finding out the hard way. Best effort: returns [] on any problem."""
    lines = []
    try:
        hits = []
        for status in ("stale", "superseded"):
            hits += [(float(sc), mid, text, status)
                     for mid, sc, text in run("MEM.SEARCH", project, limit, "STATUS", status, "VECTOR", *vector)]
        for score, mid, text, status in sorted(hits, reverse=True)[:limit]:
            if score < config.RELATED_SIMILARITY:
                continue
            reason = status_reason(mid)
            if reason.startswith(TEAM_MOVED):
                continue  # it was shared with the team, where recall already finds it
            if status == "superseded":
                label = f"no longer true, {reason}" if reason else "no longer true, replaced by a newer memory"
            elif reason.endswith(FILE_CHANGE):
                label = f"possibly outdated: {reason}; check the code before relying on it"
            else:
                label = f"no longer true, retired: {reason}" if reason else "no longer true, retired"
            lines.append(f"[{mid}] \"{text}\" ({label})")
    except Exception:
        return []
    return lines


# ---------- tools the AI can call ----------

def core_tool():
    """For recall and remember, which every task uses: Claude Code otherwise hides MCP tools until the AI looks
    them up, costing a turn before the first recall. This asks it to load them at the start of the session."""
    try:
        return mcp.tool(meta={"anthropic/alwaysLoad": True})
    except TypeError:  # older mcp 1.x releases have no meta
        return mcp.tool()


@core_tool()
def remember(fact: str, files: Optional[List[str]] = None, replaces: str = "", new: bool = False) -> str:
    """Save a durable fact about this project so future sessions, in any AI tool, know it.
    Use this, not a built-in memory or notes file, whenever the user asks you to remember something
    about the project.

    Good facts: conventions ("we use pnpm, not npm"), setup gotchas ("tests need DOCKER_HOST set"),
    decisions and their reasons. Not: temporary task details or anything in the code already.
    files: repo-relative paths this fact depends on (e.g. ["docker-compose.yml"]), so the memory
    can be flagged when those files change.
    replaces: the id of a memory this fact makes untrue (e.g. "m42"), to save and retire it in one call.
    new: save it even though a similar memory exists, because it's a separate fact.
    """
    try:
        vector = embed(fact)
        if not replaces and not new:
            # Check before saving: re-saving something already known, then retiring the copy, wastes calls.
            hits = run("MEM.SEARCH", project_name(), 3, "VECTOR", *vector)
            close = [(mid, float(sc), text) for mid, sc, text in hits
                     if config.SAME_FACT_SIMILARITY <= float(sc) < 0.97]
            if close:
                listed = "\n".join(f"[{mid}] (similarity {sc:.2f}) {text}" for mid, sc, text in close)
                return ("Not saved: this looks like something already known:\n" + listed +
                        f"\nIf that covers it, you're done. If yours makes it untrue, call remember again with "
                        f"replaces=\"{close[0][0]}\". If it's a separate fact, call remember again with new=True.")
            shared = _team_hits(vector, 2, config.SAME_FACT_SIMILARITY)
            if shared:
                return ("Not saved: your team's shared memory already has this:\n" + "\n".join(shared) +
                        "\nIf that covers it, you're done. If it's a separate fact, call remember again with new=True.")
        mem_id, outcome, related = add_memory(project_name(), fact, link_files(files) if files else "", vector=vector)
        if outcome == "duplicate":
            return f"Already known as {mem_id}; noted that it came up again. Nothing new was stored."
        reply = f"Saved as {mem_id}."
        if replaces:
            reply += " " + replace_memory(replaces, mem_id)
            return reply
        if outcome == "possible_conflict":
            old = dict(zip(*[iter(run("MEM.GET", related))] * 2))
            reply += (f" It is very similar to {related}: \"{old.get('text')}\" and may contradict it. "
                      f"If unsure, ask the user, then call resolve_conflict.")
        # Similarity can't tell "contradicts" from "related" (a real contradiction can score low), so show
        # the closest existing memories and let the AI judge whether the new fact replaces any of them.
        hits = run("MEM.SEARCH", project_name(), 4, "VECTOR", *vector)
        others = [(mid, float(sc), text) for mid, sc, text in hits
                  if mid not in (mem_id, related) and float(sc) >= config.RELATED_SIMILARITY]
        if others:
            reply += "\nRelated existing memories:\n" + "\n".join(
                f"[{mid}] (similarity {sc:.2f}) {text}" for mid, sc, text in others)
            reply += (f"\nIf {mem_id} makes any of these no longer true, call replace_memory(old_id, \"{mem_id}\"). "
                      f"Otherwise do nothing.")
        return reply
    except EngineDown as e:
        return store_down(e)


# Memories about how to run, build or test the project matter to almost every task, but a search for the task
# itself ("charge in other currencies") rarely finds them. In our evaluation, missing "run tests with `make
# test`" led the AI to run the tests the wrong way, hit the error, and re-save the fact: several wasted turns.
_SETUP = re.compile(r"`(make|npm|pnpm|yarn|bun|pytest|python3? -m|uv|cargo|go|mvn|gradle|docker|just)\b[^`]*`"
                    r"|\b(run|running) (the |all )?tests?\b|\btests? (need|needs|require|requires|must)\b", re.I)


def _setup_memories(project: str, already: set, limit: int = 2) -> List[str]:
    try:
        rows = run("MEM.LIST", project)
    except EngineDown:
        return []
    found = [f"[{mid}] {text}" for mid, _status, _files, text in rows
             if mid not in already and _SETUP.search(text or "")][:limit]
    if len(found) < limit:
        try:
            from . import team
            root = repo_root()
            if team.joined(root):
                found += [team.line(m, root) for m in team.current(root)
                          if m.id not in already and _SETUP.search(m.text)][:limit - len(found)]
        except Exception:
            pass  # team memory never gets in the way of recall
    return found


# ---------- team memory: shared files in the repo (see team.py) ----------

TEAM_HEADER = "Team memories (shared by everyone on this repo; each line below is the whole memory):"


def _team_hits(vector: List[str], limit: int, floor: float, own: Optional[list] = None) -> List[str]:
    """The team's memories closest to a query, as lines for the AI. own is this person's hits [(id, text)]: a
    team memory very close to one of them says so, since they either repeat or contradict each other.
    Best effort: [] on any problem, and always [] unless this person joined the repo's team memory."""
    try:
        from . import team
        root = repo_root()
        hits = team.search(root, [float(x) for x in vector], limit, floor)
        close = team.overlaps(root, own or [], [m for _, m in hits])
        lines = []
        for score, m in hits:
            text = team.line(m, root, score)
            if m.id in close:
                text += (f" (very close to your own [{close[m.id][0]}]: if they say the same thing, retire yours "
                         f"with mark_outdated; if they disagree, tell the user instead of picking one)")
            lines.append(text)
        return lines
    except Exception:
        return []


def _team_on_hold() -> List[str]:
    """A line for recall when team memories are being left out because git couldn't merge two people's changes
    to them: otherwise the AI just works without them and nobody knows why."""
    try:
        from . import team
        root = repo_root()
        if not team.joined(root):
            return []
        n = sum(1 for m in team.load(root) if team.conflicted(m))
    except Exception:
        return []
    if not n:
        return []
    return [f"{n} team {'memory is' if n == 1 else 'memories are'} left out because two people changed "
            f"{'it' if n == 1 else 'them'} and git couldn't merge the changes. Tell the user: `mnemolith team` shows "
            f"both versions and which file to fix."]


def _team_id(memory_id: str) -> bool:
    return memory_id.strip().startswith("team:")


@core_tool()
def recall(query: str, limit: int = 5) -> str:
    """Search this project's shared memory by meaning. Call it at the start of every task, and whenever
    project-specific knowledge (setup, conventions, past decisions) might matter.
    Current (active) memories come first. Related memories that were retired, replaced, or may be outdated
    are listed after them, labeled, so you know what changed: never rely on those."""
    try:
        stale = check_staleness()  # so we never hand back a memory whose files changed underneath it
        vector = embed(query)
        hits = run("MEM.SEARCH", project_name(), max(1, min(limit, 20)), "VECTOR", *vector)
    except EngineDown as e:
        return store_down(e)
    lines = [f"[{mid}] (similarity {float(score):.2f}) {text}" for mid, score, text in hits]
    shared = _team_hits(vector, max(1, min(limit, 20)), config.RELATED_SIMILARITY,
                        [(mid, text) for mid, _score, text in hits])
    if not lines and not shared:
        lines = ["No current memories match this."]
    on_hold = _team_on_hold()
    if shared:  # the team's first: they were reviewed, and they're what everyone else's AI is working from
        lines = [TEAM_HEADER] + shared + (["", "Your own memories (only you have these):"] + lines if lines else [])
    shown = {mid for mid, _, _ in hits} | {s.split("]", 1)[0].lstrip("[") for s in shared}
    if on_hold:
        lines += [""] + on_hold
    basics = _setup_memories(project_name(), shown)
    if basics:
        lines += ["", "How to build and test this project (comes with every recall):"] + basics
    just_stale = {line.split("]", 1)[0] for line in stale}
    history = [h for h in past_versions(project_name(), vector) if h.split("]", 1)[0] not in just_stale]
    if history:
        lines += ["", "Related memories that are no longer current (don't rely on them; they show what changed):"] + history
    if stale and MENDER_ON:
        _refresh_drafts()
        lines += ["", "These memories were just retired because files they depend on changed. Mender drafts fixes "
                      "for the user to approve: call list_stale to see what changed, and propose_fix for any without "
                      "a draft:"] + stale
    if MENDER_ON:
        waiting = _waiting_fixes()
        if waiting:
            lines += ["", f"Mender has {waiting} drafted {'fix' if waiting == 1 else 'fixes'} for stale memories waiting "
                          f"for the user's approval. Tell the user they can review {'it' if waiting == 1 else 'them'} "
                          f"with `mnemolith ui`."]
    if stale and not MENDER_ON:
        lines += ["", "These memories were just retired because files they depend on changed. If one is still "
                      "true, call confirm_memory; if it's wrong, save the corrected fact with remember:"] + stale
    return "\n".join(lines)


@mcp.tool()
def explain_memory(memory_id: str) -> str:
    """Show where a memory came from (which tool and session saved it, which files it's linked to)
    and everything that has happened to it since. Use it when a memory looks wrong or surprising."""
    if _team_id(memory_id):
        return _explain_team(memory_id)
    try:
        r = run("MEM.WHY", memory_id)
    except EngineDown as e:
        return store_down(e)
    if r is None:
        return f"No memory with id {memory_id}."
    d = dict(zip(r[0::2], r[1::2]))
    lines = [f"{d['id']}: \"{d['text']}\"",
             f"status: {d['status']}, project: {d['project']}",
             f"saved by: {d['source']}" + (f" (session {d['session']})" if d["session"] else ""),
             f"linked files: {d['files'] or 'none'}",
             "history:"]
    lines += [f"  {when(t)}  {event}" + (f": {details}" if details else "") for t, event, details in d["history"]]
    return "\n".join(lines)


def _explain_team(memory_id: str) -> str:
    from . import team
    root = repo_root()
    mem = team.find(root, memory_id)
    if mem is None:
        return f"No team memory with id {memory_id}."
    what = team.changed(mem, root)
    return "\n".join([
        f"{mem.id}: \"{mem.one_line}\"",
        f"a team memory, shared with everyone on this repo: {mem.path.relative_to(root).as_posix()}",
        f"status: {mem.status}" + (f", possibly outdated: {', '.join(what)} since it was confirmed" if what else ""),
        f"written by: {mem.meta.get('author') or 'unknown'}" + (f" on {mem.meta['created']}" if mem.meta.get("created") else ""),
        f"linked files: {', '.join(p for p, _ in mem.files) or 'none'}",
        "Its history is the file's git history (git log on that file)."])


@mcp.tool()
def mark_outdated(memory_id: str, reason: str) -> str:
    """Mark a memory as no longer true, with a short reason. It stops counting as current but keeps its
    history. If you know what's true now, save that with remember(..., replaces=<this id>) instead, so
    the next session knows the new fact, not just that the old one is wrong."""
    if _team_id(memory_id):
        return (f"{memory_id} is a team memory, shared with everyone on this repo, so it isn't retired from here. "
                f"Tell the user what looks wrong; to fix or retire it, edit its file in .mnemolith/team/memories/ "
                f"(set `status: retired` to retire it), which the user reviews and commits.")
    try:
        reply = run("MEM.STATUS", memory_id, "stale", "REASON", reason)
    except EngineDown as e:
        return store_down(e)
    except redis.ResponseError as e:
        return f"Couldn't update {memory_id}: {e}"
    return f"Marked {memory_id} as outdated." if reply == "OK" else str(reply)


@mcp.tool()
def replace_memory(old_id: str, new_id: str) -> str:
    """Retire an older memory because a newer one replaces it (e.g. "we use Postgres" replaced by
    "we migrated to MySQL"). The old memory stops showing up in recall but keeps its history."""
    try:
        new = run("MEM.GET", new_id)
        if new is None:
            return f"No memory with id {new_id}."
        new_text = dict(zip(new[0::2], new[1::2]))["text"]
        run("MEM.STATUS", old_id, "superseded", "REASON", f"replaced by {new_id}: \"{new_text}\"")
    except EngineDown as e:
        return store_down(e)
    except redis.ResponseError as e:
        return f"Couldn't replace: {e}"
    return f"{old_id} retired; {new_id} replaces it."


@mcp.tool()
def check_stale() -> str:
    """Check whether any memory's linked files have changed since it was saved, and retire those
    memories. recall already does this automatically; call it directly after big changes."""
    try:
        stale = check_staleness()
    except EngineDown as e:
        return store_down(e)
    if not stale:
        return "All memories linked to files are still up to date."
    if MENDER_ON:
        _refresh_drafts()
        return "\n".join(stale) + "\nMender drafts fixes for these: call list_stale, then propose_fix for any without a draft."
    return "\n".join(stale)


@mcp.tool()
def confirm_memory(memory_id: str) -> str:
    """Mark a memory as still true (e.g. after it was retired because a linked file changed, but the
    fact itself didn't). Re-links it to the current version of its files."""
    if _team_id(memory_id):
        from . import team
        try:
            mem = team.confirm(repo_root(), memory_id)
        except team.TeamError as e:
            return str(e)
        return (f"Confirmed {mem.id}: {mem.path.relative_to(repo_root()).as_posix()} now records the current "
                f"versions of its files. Tell the user it's a change in the repo for them to commit.")
    try:
        linked = confirm(memory_id)
    except EngineDown as e:
        return store_down(e)
    if linked is None:
        return f"No memory with id {memory_id}."
    return f"Confirmed {memory_id}; it's active again" + (" and linked to the current file versions." if linked else ".")


# ---------- syncing to AGENTS.md / CLAUDE.md ----------

SYNC_START = "<!-- mnemolith:start -->"
SYNC_END = "<!-- mnemolith:end -->"
OLD_MARKERS = [("<!-- aether-cache:start -->", "<!-- aether-cache:end -->"),  # the project's earlier names
               ("<!-- memstore:start -->", "<!-- memstore:end -->")]
SYNC_FILES = ["AGENTS.md", "CLAUDE.md"]


def _one_line(text: str) -> str:
    """A memory as one markdown bullet: line breaks folded into spaces, and anything that looks like an HTML
    comment neutralised, so a memory can never break out of its bullet or end the managed section early."""
    return " ".join(text.split()).replace("<!--", "&lt;!--").replace("-->", "--&gt;")


def memory_block() -> str:
    rows = run("MEM.LIST", project_name())
    lines = [SYNC_START,
             "## Project memory (managed by Mnemolith)",
             "<!-- Generated by Mnemolith; edits inside this section are overwritten on the next sync. -->",
             ""]
    lines += [f"- {_one_line(text)} ({mid})" for mid, _status, _files, text in rows] or ["_No memories yet._"]
    lines.append(SYNC_END)
    return "\n".join(lines)


def sync_files() -> str:
    """Writes the project's active memories into the managed section of AGENTS.md / CLAUDE.md.
    Only the section between the markers is touched; files that don't exist aren't created, unless
    neither exists, in which case AGENTS.md is."""
    root = repo_root()
    block = memory_block()
    targets = [root / name for name in SYNC_FILES if (root / name).exists()] or [root / "AGENTS.md"]
    report = []
    for path in targets:
        raw = path.read_bytes().decode("utf-8") if path.exists() else ""
        newline = "\r\n" if "\r\n" in raw else "\n"  # keep the file's own line endings, so git sees only our lines
        old = raw.replace("\r\n", "\n")
        new = None
        for start, end in ((SYNC_START, SYNC_END), *OLD_MARKERS):
            if start in old and end in old:
                new = old[:old.index(start)] + block + old[old.index(end) + len(end):]
                break
        if new is None:
            new = (old.rstrip() + "\n\n" if old.strip() else "") + block + "\n"
        if new == old:
            report.append(f"{path.name}: already up to date")
            continue
        tmp = path.with_name(path.name + ".mnemolith-tmp")
        with open(tmp, "w", encoding="utf-8", newline=newline) as f:
            f.write(new)
        os.replace(tmp, path)  # swap in the new version all at once, so a crash can't leave half a file
        report.append(f"{path.name}: {'updated' if old else 'created'}")
    return "\n".join(report)


@mcp.tool()
def sync_to_files() -> str:
    """Write this project's current memories into the managed section of AGENTS.md / CLAUDE.md, so they're
    committed with the code: teammates and tools without Mnemolith see them, and changes show up in reviews.
    Call it when the user asks, or after a batch of new memories."""
    try:
        check_staleness()  # never write out a memory whose files have changed
        return sync_files()
    except EngineDown as e:
        return store_down(e)


@mcp.tool()
def list_conflicts() -> str:
    """List pairs of memories in this project that may contradict each other and need a decision."""
    try:
        rows = run("MEM.CONFLICTS", project_name())
    except EngineDown as e:
        return store_down(e)
    if not rows:
        return "No conflicts waiting for review."
    return "\n".join(f"{new_id} \"{new_text}\" may contradict {old_id} \"{old_text}\" (similarity {sim})"
                     for new_id, new_text, old_id, old_text, sim in rows)


@mcp.tool()
def resolve_conflict(new_id: str, old_id: str, replaces_old: bool) -> str:
    """Settle a possible conflict. replaces_old=True: the new memory replaces the old one (the old one
    is retired). replaces_old=False: both are true, keep both. Ask the user if you're not sure."""
    try:
        run("MEM.RESOLVE", new_id, old_id, "SUPERSEDE" if replaces_old else "KEEP")
    except EngineDown as e:
        return store_down(e)
    except redis.ResponseError as e:
        return f"Couldn't resolve: {e}"
    return f"{old_id} retired; {new_id} replaces it." if replaces_old else f"Kept both {new_id} and {old_id}."


# ---------- Mender: drafted fixes for stale memories (only offered to the AI when it's turned on) ----------

def _refresh_drafts() -> None:
    try:
        from . import mender
        mender.refresh(project_name(), git_toplevel())
    except Exception:
        pass  # drafting is a convenience; it never gets in the way of recall


def _waiting_fixes() -> int:
    try:
        from . import mender
        return mender.waiting(project_name())
    except Exception:
        return 0


def list_stale() -> str:
    """List this project's memories that went stale because files they depend on changed, with what changed in
    those files, and whether a fix is already drafted. Then check the code and call propose_fix for each one
    without a draft. The user approves drafts in the dashboard; nothing changes until then."""
    try:
        from . import mender
        check_staleness()
        return mender.describe_stale(project_name(), git_toplevel())
    except EngineDown as e:
        return store_down(e)


def propose_fix(memory_id: str, new_text: str = "", reason: str = "", still_true: bool = False,
                files: Optional[List[str]] = None) -> str:
    """Draft the fix for a stale memory, for the user to approve in the dashboard (nothing changes until they do).
    new_text: what's true now, as one short fact in the same style as the old memory.
    reason: what changed and what you checked in the repo; quote only text that's really in the files.
    still_true: True if the change doesn't affect the memory (then leave new_text empty).
    files: repo-relative files the new fact depends on, if different from the old memory's (e.g. after a move)."""
    try:
        from . import mender
        return mender.propose(memory_id, new_text, reason, still_true, config.SOURCE, files, git_toplevel())
    except EngineDown as e:
        return store_down(e)


def share_with_team(memory_id: str = "", fact: str = "", files: Optional[List[str]] = None) -> str:
    """Share a memory with everyone on this repo, as a file in .mnemolith/team/memories/ that the user reviews
    and commits. Only when the user asks you to share something with the team, or asks for starter team memories.
    Before calling, tell the user: it will be visible to everyone with access to this repo.

    memory_id: one of the user's own memories to share (e.g. "m42"); it moves to the team.
    fact: or a new fact to share directly. Always pass files= with the files it depends on: that's how everyone's
    AI tools learn when it may have gone out of date. For starter memories, read the repo and
    share at most 30 of the most useful facts, one call each.
    What belongs: decisions and their reasons, conventions, who owns what, setup gotchas, promises to partners.
    Not personal preferences, and never passwords, keys or tokens (those are refused)."""
    from . import team
    root = repo_root()
    if not team.joined(root):
        return "This repo has no team memory in use here. The user can start or join one with `mnemolith team init`."
    if not team.ai_allowed(root):
        return ("The user hasn't allowed AI tools to share memories with the team in this repo yet. Ask them to run "
                "`mnemolith team ai on` once (or turn it on in `mnemolith ui`), or to share it themselves with "
                f"`mnemolith promote {memory_id or '<id>'}`. Don't run those commands for them.")
    try:
        if memory_id.strip():
            mem = team.promote(root, project_name(), memory_id.strip())
        elif fact.strip():
            mem = team.add(root, fact, files)
        else:
            return "Pass memory_id (one of the user's memories) or fact (a new one)."
    except team.SecretFound as e:
        return str(e) + (" If the user is sure it isn't a secret, they can share it themselves from the terminal "
                         "or the dashboard." if e.overridable else "")
    except team.TeamError as e:
        return str(e)
    except EngineDown as e:
        return store_down(e)
    return (f"Shared as {mem.id}: {mem.path.relative_to(root).as_posix()} (mnemolith.md was updated too). They're "
            f"changes in the repo for the user to review and commit; don't commit them yourself.")


if TEAM_ON:
    mcp.tool()(share_with_team)

if MENDER_ON:
    mcp.tool()(list_stale)
    mcp.tool()(propose_fix)


def serve() -> None:
    warm_up()
    mcp.run()  # talks to the AI tool over stdin/stdout
