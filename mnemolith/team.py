"""
Team memory: memories a whole team shares, kept as plain files in the repo.

  .mnemolith/team/README.md           what this folder is, for people who find it
  .mnemolith/team/memories/<name>.md  one memory per file

The files are the only copy. Nothing about team memories is stored in the engine, so they follow git: a branch,
a merge or a pull changes what the team knows, and every change is reviewed like code. Mnemolith only reads and
writes these files; it never commits.

A memory file is a short header, then the fact:

  ---
  mnemolith: 1
  status: active
  author: Sam Lee
  created: 2026-11-09
  files:
    - package.json@3f2a9c1b04de
  ---
  We use pnpm, not npm.

Each linked file carries a fingerprint of its contents from when the memory was last confirmed. When the file
no longer matches, the memory shows up as possibly outdated, for everyone, until someone confirms or fixes it.

Reading a repo's team memories is opt-in per person (`mnemolith team init`), because they are text written by
other people that goes straight to your AI tool.
"""
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config, server

FORMAT = 1  # the `mnemolith:` number in each file; raised if the format ever changes incompatibly
FOLDER = ".mnemolith/team"
PREFIX = "team:"  # how a team memory's id looks to the AI: team:<file name without .md>
MAX_TEXT = 1000  # characters of a memory shown in recall, the same limit import uses for a note
MAX_CACHED = 3000
NAME = re.compile(r"^[\w][\w.-]*$")

README = """# Team memory

This folder is your team's shared memory for AI coding tools, managed by [Mnemolith](https://mnemolith.dev/).

Each file in `memories/` is one fact about this project that everyone's AI tool should know: a convention,
a setup step, a decision and the reason for it. They are plain text, so you can read, edit and review them
like any other file in the repo.

- **Everyone who can see this repo can see these files.** Never put passwords, keys or anything private here.
- To use them, install Mnemolith and run `mnemolith team init` in this repo. It only changes your own setup.
- To change a memory, edit its file. To retire one, set `status: retired` in its header.
- `mnemolith.md` at the top of the repo lists every memory in one line, for teammates and AI tools without
  Mnemolith. It's generated: `mnemolith team sync` rebuilds it after you edit a memory by hand.
- `files:` lists the files (or folders) a memory depends on. When one changes, Mnemolith shows the memory as possibly
  outdated until someone runs `mnemolith team confirm <name>` or fixes it.
- Mnemolith never commits. Changes here are yours to commit and review.
"""


class TeamError(Exception):
    pass


@dataclass
class Memory:
    name: str
    path: Path
    text: str
    meta: Dict[str, str] = field(default_factory=dict)  # header lines other than files, in the file's order
    files: List[Tuple[str, str]] = field(default_factory=list)  # (repo-relative path, fingerprint or "")

    @property
    def id(self) -> str:
        return PREFIX + self.name

    @property
    def status(self) -> str:
        return (self.meta.get("status") or "active").strip().lower()

    @property
    def version(self) -> int:
        try:
            return int(self.meta.get("mnemolith", FORMAT))
        except ValueError:
            return FORMAT

    @property
    def readable(self) -> bool:
        return self.version <= FORMAT

    @property
    def one_line(self) -> str:
        return " ".join(self.text.split())[:MAX_TEXT]


# ---------- the files ----------

def folder(root: Path) -> Path:
    return root / FOLDER


def exists(root: Path) -> bool:
    return folder(root).is_dir()


def _unquote(value: str) -> str:
    value = value.strip()
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'" else value


FINGERPRINT = re.compile(r"^[0-9a-f]{12}$")


def _file_entry(item: str) -> Tuple[str, str]:
    """"shop/a.py@3f2a9c1b04de" -> ("shop/a.py", "3f2a9c1b04de"). Only a real fingerprint after the last @ counts,
    so a path with an @ of its own (packages/@acme/ui/button.js) stays whole."""
    item = _unquote(item)
    path, at, last = item.rpartition("@")
    if not at or not FINGERPRINT.match(last.strip()):
        path, last = item, ""
    return path.strip().removeprefix("./"), last.strip()


def parse(raw: str) -> Tuple[Dict[str, str], List[Tuple[str, str]], str]:
    """A memory file's text -> (header, linked files, the fact). Forgiving, because people edit these by hand:
    a file with no header at all is just a fact."""
    raw = raw.replace("\r\n", "\n").lstrip("﻿")
    if not raw.startswith("---\n"):
        return {}, [], raw.strip()
    end = raw.find("\n---", 3)
    if end == -1:
        return {}, [], raw.strip()
    meta: Dict[str, str] = {}
    files: List[Tuple[str, str]] = []
    in_files = False
    for line in raw[4:end].split("\n"):
        item = re.match(r"^\s*-\s+(.+)$", line)
        if in_files and item:
            files.append(_file_entry(item.group(1)))
            continue
        pair = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if not pair:
            continue
        key, value = pair.group(1).lower(), pair.group(2).strip()
        in_files = key == "files"
        if in_files:
            if value.startswith("["):  # files: [a.py, b.yml]
                files += [_file_entry(v) for v in value.strip("[]").split(",") if v.strip()]
        else:
            meta[key] = _unquote(value)
    body = raw[end + 4:]
    return meta, [f for f in files if f[0]], body.split("\n", 1)[1].strip() if "\n" in body else ""


def render(mem: Memory) -> str:
    lines = ["---"] + [f"{key}: {value}" for key, value in mem.meta.items()]
    if mem.files:
        lines += ["files:"] + [f"  - {path}@{fp}" if fp else f"  - {path}" for path, fp in mem.files]
    return "\n".join(lines + ["---", mem.text.strip(), ""])


def load(root: Path) -> List[Memory]:
    """Every memory file in the repo's team folder, by name. Unreadable files are skipped."""
    found = []
    for path in sorted((folder(root) / "memories").glob("*.md")):
        try:
            meta, files, text = parse(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        found.append(Memory(path.stem, path, text, meta, files))
    return found


def current(root: Path) -> List[Memory]:
    """The memories recall uses: active, written in a format this version understands, and not empty."""
    return [m for m in load(root) if m.status == "active" and m.readable and m.text.strip() and not conflicted(m)]


def find(root: Path, name: str) -> Optional[Memory]:
    name = name.strip().removeprefix(PREFIX).removesuffix(".md")
    if not NAME.match(name):  # also keeps a made-up id from reaching outside the folder
        return None
    return next((m for m in load(root) if m.name == name), None)


def _write(mem: Memory) -> None:
    raw = mem.path.read_bytes().decode("utf-8", errors="replace") if mem.path.exists() else ""
    newline = "\r\n" if "\r\n" in raw else "\n"  # keep the file's own line endings, so git sees only our change
    tmp = mem.path.with_name(mem.path.name + ".mnemolith-tmp")
    with open(tmp, "w", encoding="utf-8", newline=newline) as f:
        f.write(render(mem))
    os.replace(tmp, mem.path)


# ---------- staleness ----------

def fingerprint(path: Path) -> str:
    """A short hash of a file's contents. Windows line endings are read as plain ones first, so the same commit
    gives every teammate the same fingerprint whatever their git line-ending setting."""
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha1(data).hexdigest()[:12]


MAX_FOLDER_FILES = 500  # a linked folder is fingerprinted file by file on every recall, so it has to stay small


def _folder_files(root: Path, path: str) -> Optional[List[str]]:
    """The files in a linked folder, as repo-relative paths: what git tracks or would track, so build output and
    ignored files don't count. None if there are too many to fingerprint on every recall."""
    try:
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "--", path], cwd=root,
                             capture_output=True, text=True, timeout=10)
        names = sorted(n for n in out.stdout.splitlines() if n) if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        names = None
    if names is None:  # not a git repo, or no git: every file that isn't hidden
        base = root / path
        names = sorted(p.relative_to(root).as_posix() for p in base.rglob("*")
                       if p.is_file() and not any(part.startswith(".") for part in p.relative_to(base).parts))
    names = [n for n in names if not n.startswith(FOLDER + "/") and (root / n).is_file()]
    return names if len(names) <= MAX_FOLDER_FILES else None


def _now(root: Path, path: str) -> str:
    """The fingerprint of a linked file or folder as it is now; "missing" if it's gone (or, for a folder, empty
    or too big to follow). A folder's fingerprint covers the names and contents of the files in it."""
    try:
        target = root / path
        if target.is_file():
            return fingerprint(target)
        if not target.is_dir():
            return "missing"
        names = _folder_files(root, path)
        if not names:
            return "missing"
        h = hashlib.sha1()
        for name in names:
            h.update(f"{name}\0{fingerprint(root / name)}\n".encode("utf-8"))
        return h.hexdigest()[:12]
    except OSError:
        return "missing"


def changed(mem: Memory, root: Path) -> List[str]:
    """What happened to the files this memory depends on since it was last confirmed: ["a.py changed", ...].
    Files listed without a fingerprint (typed by hand) aren't tracked until the memory is confirmed."""
    out = []
    for path, saved in mem.files:
        if saved:
            now = _now(root, path)
            if now != saved:
                out.append(f"{path} {'was deleted' if now == 'missing' else 'changed'}")
    return out


def linkable(path: str) -> bool:
    """Whether a memory may depend on this path: inside the repo, and not one of team memory's own files, which
    change every time anyone shares something and would flag the memory over and over."""
    p = Path(path)
    if p.is_absolute() or ".." in p.parts:
        return False
    rel = p.as_posix().rstrip("/")
    return rel not in (INDEX, ".mnemolith", FOLDER) and not rel.startswith(FOLDER + "/")


def confirm(root: Path, name: str) -> Memory:
    """Says a memory is still true: records the current fingerprint of each file it depends on, in its file."""
    mem = find(root, name)
    if mem is None:
        raise TeamError(f"There's no team memory called {name.removeprefix(PREFIX)} in {FOLDER}/memories/.")
    if not mem.readable:
        raise TeamError(f"{mem.name} was written by a newer Mnemolith. Update Mnemolith to change it.")
    gone = [path for path, _ in mem.files if _now(root, path) == "missing"]
    if gone:
        raise TeamError(f"{mem.name} depends on {', '.join(gone)}, which no longer exists. Edit "
                        f"{mem.path.relative_to(root).as_posix()} to fix or retire it.")
    mem.files = [(path, _now(root, path)) for path, _ in mem.files if linkable(path)]
    mem.meta.setdefault("mnemolith", str(FORMAT))
    _write(mem)
    return mem


# ---------- joining ----------

def _settings() -> dict:
    try:
        settings = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
        return settings if isinstance(settings, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_settings(settings: dict) -> None:
    config.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, config.SETTINGS_FILE)


def _key(root: Path) -> str:
    return str(root.resolve())


def joined(root: Path) -> bool:
    """Whether this person chose to use this repo's team memory. Stored on their machine, not in the repo."""
    return _key(root) in config.team_repos()


def _set_joined(root: Path, on: bool) -> None:
    settings = _settings()
    team = [p for p in settings.get("team", []) if isinstance(p, str) and p != _key(root)] \
        if isinstance(settings.get("team"), list) else []
    settings["team"] = team + ([_key(root)] if on else [])
    _save_settings(settings)


def _ignored(root: Path) -> bool:
    try:
        out = subprocess.run(["git", "check-ignore", "-q", FOLDER + "/README.md"], cwd=root, capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


ATTRIBUTES = ".gitattributes"
MERGE_RULE = "mnemolith.md merge=union"
MERGE_NOTE = "# Mnemolith: when two branches both add team memories, keep both lines of the list instead of conflicting"


def ensure_merge_rule(root: Path) -> bool:
    """Makes sure the repo's .gitattributes tells git to merge mnemolith.md by keeping both sides' lines. Without
    it, two people who each share a memory on their own branch get a merge conflict in that one file every time.
    Returns whether the file changed."""
    path = root / ATTRIBUTES
    try:
        raw = path.read_bytes().decode("utf-8", errors="replace") if path.exists() else ""
    except OSError:
        return False
    if re.search(r"^\s*mnemolith\.md\s+.*\bmerge=", raw, re.M):
        return False  # already has a rule, ours or one the team chose
    newline = "\r\n" if "\r\n" in raw else "\n"
    add = (("" if not raw or raw.endswith(("\n", "\r")) else newline) + MERGE_NOTE + newline + MERGE_RULE + newline)
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(add)
    return True


def init(root: Path) -> dict:
    """Starts a team memory in this repo, or joins the one that's there. Returns {created, count, ignored}.
    Starting one also writes mnemolith.md and its merge rule, so the first two people to share don't collide.
    Joining never touches the repo."""
    created = not exists(root)
    (folder(root) / "memories").mkdir(parents=True, exist_ok=True)
    readme = folder(root) / "README.md"
    if not readme.exists():
        readme.write_text(README, encoding="utf-8")
    if created:
        write_index(root)
        ensure_merge_rule(root)
    _set_joined(root, True)
    return {"created": created, "count": len(current(root)), "ignored": _ignored(root)}


def leave(root: Path) -> bool:
    """Stops using this repo's team memory on this machine. The repo's files are never touched."""
    was = joined(root)
    _set_joined(root, False)
    allow_ai(root, False)
    return was


# ---------- search ----------

_vectors: Optional[Dict[str, List[float]]] = None  # text hash -> embedding, also kept on disk between sessions


def _cache_file() -> Path:
    return config.HOME / "team-vectors.json"


def _cache_id() -> str:
    return "fake" if config.FAKE_EMBEDDER else config.MODEL


def _text_key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _embeddings(texts: List[str]) -> List[List[float]]:
    """Embeddings for memory texts. Each text is embedded once and remembered, since team memories rarely change
    and recall runs on every task."""
    global _vectors
    if _vectors is None:
        try:
            data = json.loads(_cache_file().read_text(encoding="utf-8"))
            _vectors = data["vectors"] if data.get("model") == _cache_id() else {}
        except (OSError, ValueError, KeyError, AttributeError):
            _vectors = {}
    missing = [t for t in dict.fromkeys(texts) if _text_key(t) not in _vectors]
    if missing:
        for text, vec in zip(missing, server.embed_many(missing)):
            _vectors[_text_key(text)] = [round(float(x), 5) for x in vec]
        while len(_vectors) > MAX_CACHED:
            _vectors.pop(next(iter(_vectors)))  # oldest first
        try:
            _cache_file().parent.mkdir(parents=True, exist_ok=True)
            tmp = _cache_file().with_suffix(".tmp")
            tmp.write_text(json.dumps({"model": _cache_id(), "vectors": _vectors}), encoding="utf-8")
            os.replace(tmp, _cache_file())
        except OSError:
            pass  # the cache only saves time
    return [_vectors[_text_key(t)] for t in texts]


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def search(root: Path, vector: List[float], limit: int = 5, floor: float = 0.0) -> List[Tuple[float, Memory]]:
    """This repo's team memories closest in meaning to a query, best first. Empty unless this person joined."""
    if not exists(root) or not joined(root):
        return []
    mems = current(root)
    if not mems:
        return []
    scored = [(_cosine(vector, v), m) for m, v in zip(mems, _embeddings([m.one_line for m in mems]))]
    scored = [s for s in scored if s[0] >= floor]
    scored.sort(key=lambda s: -s[0])
    return scored[:limit]


def line(mem: Memory, root: Path, score: Optional[float] = None) -> str:
    """A team memory as recall shows it, saying so if the files it depends on changed."""
    what = changed(mem, root)
    note = f" (possibly outdated: {', '.join(what)} since this was confirmed; check the code before relying on it)" \
        if what else ""
    sim = f" (similarity {score:.2f})" if score is not None else ""
    return f"[{mem.id}]{sim} {mem.one_line}{note}"


# ---------- the secret check ----------
#
# Everything that goes into the repo through Mnemolith passes this first. "Hard" findings are refused outright.
# "Soft" ones might be fine (a commit id looks like a key), so a person can look and decide; an AI tool can't.

_HARD = [
    ("a private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("an AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("a GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("a GitLab token", re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}")),
    ("a Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("a Stripe key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("an API key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("a Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("a Google login token", re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}")),
    ("a Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("an npm token", re.compile(r"\bnpm_[A-Za-z0-9]{30,}\b")),
    ("a PyPI token", re.compile(r"\bpypi-[A-Za-z0-9_-]{30,}")),
    ("a SendGrid key", re.compile(r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}")),
    ("a DigitalOcean token", re.compile(r"\bdop_v1_[a-f0-9]{40,}\b")),
    ("a login token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*")),
    ("a login token (JWT)", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}")),
    ("a web address with a password in it", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@")),
]
_KEYWORD = (r"password|passwd|pwd|pw|passphrase|passcode|secret|token|api[ _-]?key|access[ _-]?key|"
            r"private[ _-]?key|auth[ _-]?key|credentials?")
# A name and a password together: "login is admin / hunter2", "creds: admin:hunter2".
_PAIR = re.compile(r"(?i)\b(?:login|log[- ]in|creds?|credentials?|username|user)\b\s*(?:is|are|=|:)?\s*"
                   r"[\w.@-]{2,}\s*[/:]\s*([^\s\"'`,;/:]{5,})")
# A password straight after the word: "ssh in with password Tr0ub4dor".
_AFTER = re.compile(r"(?i)\b(?:password|passwd|pwd|pw|passphrase|passcode)\s+[\"'`]?([^\s\"'`,;]{6,})")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_ID_NUMBER = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# NAME=value, the way config and .env files write it: password: x, DB_PASSWORD=x, SECRET_KEY='x'.
_ASSIGNED = re.compile(r"(?i)(?<![A-Za-z0-9])(?:[A-Za-z0-9]+[_-])*(" + _KEYWORD.replace("[ _-]", "[_-]") +
                       r")(?:[_-][A-Za-z0-9]+)*[\"'`]?\s*[=:]\s*[\"'`]?([^\s\"'`,;]{4,})")
# The same, said in words: "the admin password is hunter2", "staging token was abc123". Common words after "is"
# ("the password is stored in Vault", "the token is required") are how people talk about a secret, not the secret.
_SAID = re.compile(r"(?i)\b(" + _KEYWORD + r")(?:\s+(?:for|of|on|to)\s+[\w.@/-]+)?\s+(?:is|was|=|:)\s+"
                   r"[\"'`]?([^\s\"'`,;]{4,})")
_WORDS = re.compile(r"^(?:[a-z]+(?:ed|ing|ly|able|ible)|stored|kept|set|read|in|on|at|the|an|not|never|always|"
                    r"only|now|still|same|different|available|missing|wrong|correct|valid|invalid|empty|blank|"
                    r"also|just|from|with|what|where|whatever|unique|random|long|short|strong|weak|secret|private|"
                    r"public|shared|sent|reset|unset|rotated|per|each|every|one|two|three|four|five|this|that|"
                    r"there|here|kept|held|done|gone|due|case|less|more|most|much|many|some|any|none|null|"
                    r"true|false|optional|required|changeme|redacted|hidden|example)$")
_PLACEHOLDER = re.compile(r"^(?:[$<{(\[%].*|[A-Z][A-Z0-9_]*|\*+|[xX]+|\.+|(?i:none|null|true|false|required|optional|"
                          r"changeme|redacted|hidden|example|your[\w-]*|process\.env\S*|os\.environ\S*|env\S*|"
                          r"bearer|basic|jwt|oauth\S*|sha\d*|rs\d+|hs\d+|bcrypt|argon2\w*|scrypt))$")
_SOFT = [
    ("a long random-looking string, which is what keys look like", re.compile(r"\b(?=[A-Za-z_-]*\d)(?=[\d_-]*[A-Za-z])[A-Za-z0-9_-]{32,}\b")),
    ("a folder on your computer, with your user name in it", re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\Users\\)[^\s/\\]+")),
]


def _luhn(digits: List[int]) -> bool:
    """The checksum real card numbers pass and most other long numbers don't."""
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0 and len(set(digits)) > 1


def _masked(found: str) -> str:
    found = found.strip()
    return (found[:4] + "…") if len(found) > 6 else "…"


def scan_secrets(text: str) -> List[dict]:
    """Things in a memory that look like credentials or private details: [{what, hard, shown}]. shown is only the
    first few characters, so a secret is never printed or logged in full."""
    found, spans = [], []

    def add(what: str, hard: bool, start: int, end: int) -> None:
        if not any(s < end and start < e for s, e in spans):  # not already reported as something else
            spans.append((start, end))
            found.append({"what": what, "hard": hard, "shown": _masked(text[start:end])})

    for what, pattern in _HARD:
        for m in pattern.finditer(text):
            add(what, True, m.start(), m.end())
    def mixed(value: str) -> bool:
        return bool(re.search(r"\d|[^\w]", value) and re.search(r"[A-Za-z]", value))

    def harmless(value: str) -> bool:
        # where a secret lives ("token: $GITHUB_TOKEN"), a word about it ("is stored"), or a small number ("TTL=3600")
        return bool(len(value) < 4 or _PLACEHOLDER.match(value) or _WORDS.match(value.lower())
                    or re.fullmatch(r"\d{1,5}", value))

    for pattern in (_ASSIGNED, _SAID):
        for m in pattern.finditer(text):
            value = m.group(2).rstrip(".)!?")
            if not harmless(value):
                add(f"a {m.group(1).lower()} written out", mixed(value), m.start(2), m.start(2) + len(value))
    for m in _AFTER.finditer(text):
        value = m.group(1).rstrip(".)!?")
        if re.search(r"\d", value) and not harmless(value):  # "with password Tr0ub4dor", "passcode 884213"
            add("a password written out", mixed(value), m.start(1), m.start(1) + len(value))
    for m in _PAIR.finditer(text):
        value = m.group(1).rstrip(".)!?")
        if mixed(value) and not harmless(value):  # "login is admin / hunter2"
            add("a login and password written out", True, m.start(1), m.start(1) + len(value))
    for m in _CARD.finditer(text):
        digits = [int(c) for c in m.group(0) if c.isdigit()]
        if 13 <= len(digits) <= 19 and _luhn(digits):
            add("a card number", True, m.start(), m.end())
    for m in _ID_NUMBER.finditer(text):
        add("what looks like a Social Security number", False, m.start(), m.end())
    for what, pattern in _SOFT:
        for m in pattern.finditer(text):
            add(what, False, m.start(), m.end())
    return found


class SecretFound(TeamError):
    """Refused: the text looks like it contains a secret. overridable is True when a person may decide it's fine."""

    def __init__(self, findings: List[dict]):
        self.findings = findings
        self.overridable = not any(f["hard"] for f in findings)
        listed = "; ".join(f"{f['what']} ({f['shown']})" for f in findings)
        super().__init__(f"Not shared: this looks like it contains {listed}. Team memories are visible to everyone "
                         f"who can see the repo, so secrets never go in them.")


# ---------- adding and sharing ----------

MOVED = "moved to team memory"  # why a personal memory was retired when it was shared; recall hides these
MAX_IMPORT = 30  # starter memories: the most a team should have to review in one pull request
INDEX = "mnemolith.md"
INDEX_NOTE = ("<!-- Generated by Mnemolith from .mnemolith/team/memories/. Don't edit this file: edit the memory's "
              "own file, then run `mnemolith team sync`. -->")


def author(root: Path) -> str:
    try:
        out = subprocess.run(["git", "config", "user.name"], cwd=root, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def slug(text: str) -> str:
    """A file name from a memory's first words, in whatever language it's written."""
    words = re.findall(r"[^\W_]+", text.lower())
    name = ""
    for w in words[:8]:
        w = w[:24]  # languages written without spaces give one very long "word"
        if len(name) + len(w) > 48:
            break
        name += ("-" if name else "") + w
    return name or "memory"


def _free_name(root: Path, text: str) -> str:
    base, taken = slug(text), {p.stem.lower() for p in (folder(root) / "memories").glob("*.md")}
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


def add(root: Path, text: str, files: Optional[List[str]] = None, allow_soft: bool = False,
        by: str = "", index: bool = True, max_files: Optional[int] = None) -> Memory:
    """Writes a new team memory file, after the secret check. Raises SecretFound if the check finds something
    (allow_soft lets a person accept findings that only might be secrets; certain ones are always refused)."""
    from datetime import date
    text = text.strip()
    if not text:
        raise TeamError("There's nothing to share: the memory is empty.")
    if not exists(root):
        raise TeamError(f"This repo has no team memory yet. Start one with `mnemolith team init`.")
    from . import portability
    named, _missing = portability.mentioned_paths(text, root)  # "v1 (shop/api/v1.py) is frozen" depends on that file
    linked = []
    for path in dict.fromkeys([server.relative(p, root) for p in (files or []) if p.strip()] + named):
        if not linkable(path):
            continue
        now = _now(root, path)
        if now != "missing":
            linked.append((path, now))
    if max_files is not None:
        linked = linked[:max_files]  # the files passed come first, then ones the text names
    findings = scan_secrets("\n".join([text] + [path for path, _ in linked]))
    if findings and (not allow_soft or any(f["hard"] for f in findings)):
        raise SecretFound(findings)
    (folder(root) / "memories").mkdir(parents=True, exist_ok=True)
    name = _free_name(root, text)
    meta = {"mnemolith": str(FORMAT), "status": "active"}
    who = by or author(root)
    if who:
        meta["author"] = " ".join(who.split())
    meta["created"] = date.today().isoformat()
    mem = Memory(name, folder(root) / "memories" / f"{name}.md", text, meta, linked)
    _write(mem)
    if index:
        write_index(root)
    return mem


def promote(root: Path, project: str, memory_id: str, allow_soft: bool = False) -> Memory:
    """Shares one of this person's own memories with the team: writes it as a team memory file and retires the
    personal one, so the same fact isn't known twice."""
    got = server.run("MEM.GET", memory_id)
    if got is None:
        raise TeamError(f"No memory with id {memory_id}.")
    d = dict(zip(got[0::2], got[1::2]))
    if d["project"] != project:
        raise TeamError(f"{memory_id} belongs to {d['project']}, not to this repo ({project}).")
    if d["status"] != "active":
        raise TeamError(f"{memory_id} isn't current (it's {'replaced' if d['status'] == 'superseded' else 'outdated'}), "
                        f"so it can't be shared. Confirm or fix it first.")
    paths = [f.rsplit("@", 1)[0] for f in d["files"].split(",") if f]
    mem = add(root, d["text"], paths, allow_soft)
    server.run("MEM.STATUS", memory_id, "superseded", "REASON", f"{MOVED} {mem.id}")
    return mem


def retire(root: Path, name: str) -> Memory:
    mem = find(root, name)
    if mem is None:
        raise TeamError(f"There's no team memory called {name.removeprefix(PREFIX)} in {FOLDER}/memories/.")
    if not mem.readable:
        raise TeamError(f"{mem.name} was written by a newer Mnemolith. Update Mnemolith to change it.")
    mem.meta["status"] = "retired"
    _write(mem)
    write_index(root)
    return mem


def conflicted(mem: Memory) -> bool:
    """Two people changed this memory and git couldn't merge them: the file still has git's conflict markers."""
    try:
        raw = mem.path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(re.search(r"^<{7} ", raw, re.M) and re.search(r"^>{7} ", raw, re.M))


def sides(mem: Memory) -> Optional[Tuple[str, str]]:
    """The two wordings of a memory that git couldn't merge: (the one here, the one that came in). None if the
    file isn't in conflict, or the conflict is somewhere other than the fact itself."""
    try:
        raw = mem.path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    except OSError:
        return None
    m = re.search(r"^<{7} [^\n]*\n(.*?)\n(?:\|{7} [^\n]*\n.*?\n)?={7}\n(.*?)\n>{7} ", raw, re.M | re.S)
    if not m or raw.find("\n---", 3) > m.start():  # the conflict is in the header, not the fact
        return None
    return " ".join(m.group(1).split())[:MAX_TEXT], " ".join(m.group(2).split())[:MAX_TEXT]


def index_text(root: Path) -> str:
    """mnemolith.md: one line per team memory, for teammates (and their AI tools) who don't have Mnemolith.
    Sorted by name so two branches adding memories merge cleanly. A memory that fails the secret check, which
    can only happen if someone typed a secret into a file by hand, is left out."""
    lines = ["# Team memory", "", INDEX_NOTE, "",
             "What this team wants every AI coding tool to know about this project. One line per memory; the full "
             "files are in `.mnemolith/team/memories/`.", ""]
    rows = [f"- {server._one_line(m.one_line)} ({m.name})" for m in current(root)
            if not conflicted(m) and not scan_secrets(m.text)]
    return "\n".join(lines + (rows or ["_No team memories yet._"])) + "\n"


def _rows(text: str) -> List[str]:
    return sorted({line.strip() for line in text.replace("\r\n", "\n").split("\n") if line.startswith("- ")})


def index_outdated(root: Path) -> bool:
    """Whether mnemolith.md lists something other than the team's current memories. The order of its lines
    doesn't matter: after a merge that kept both branches' lines, it's still right."""
    path = root / INDEX
    if any(conflicted(m) for m in load(root)):
        return False  # settle the conflict first: rebuilding now would drop that memory from the list
    try:
        have = path.read_text(encoding="utf-8") if path.exists() else None
    except OSError:
        return False
    if have is None:
        return True
    if re.search(r"^[<=>]{7}", have, re.M):
        return True  # the list itself was left in conflict (a repo without the merge rule): rebuild it
    return _rows(have) != _rows(index_text(root))


def write_index(root: Path) -> bool:
    """Rebuilds mnemolith.md. Returns whether it changed."""
    if not index_outdated(root):
        return False
    if any(conflicted(m) for m in load(root)):
        return False
    path = root / INDEX
    raw = path.read_bytes().decode("utf-8", errors="replace") if path.exists() else ""
    tmp = path.with_name(path.name + ".mnemolith-tmp")
    with open(tmp, "w", encoding="utf-8", newline="\r\n" if "\r\n" in raw else "\n") as f:
        f.write(index_text(root))
    os.replace(tmp, path)
    return True


def notes_to_import(root: Path) -> dict:
    """Notes in CLAUDE.md, AGENTS.md and Cursor rules that could become team memories: {notes, skipped_secrets,
    more}. Leaves out what the team already has, anything that fails the secret check, and notes that don't
    read like a fact about the project."""
    from . import portability
    have = {" ".join(m.text.lower().split()).rstrip(".") for m in load(root)}
    files = portability.note_files(root)
    rels = [p.relative_to(root).as_posix() for p in files]
    notes, seen, secrets_skipped = [], set(), 0
    for path, rel in zip(files, rels):
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for found in portability.split_notes(raw):
            text = found["text"]
            key = " ".join(text.lower().split()).rstrip(".")
            if found["weak"] or key in seen or key in have or key.split(": ", 1)[-1] in have:
                continue
            seen.add(key)
            linked, missing = portability.mentioned_paths(text, root, skip=rels)
            if missing:
                continue  # mentions a file that doesn't exist, so it's probably already out of date
            if scan_secrets(text):
                secrets_skipped += 1
                continue
            notes.append({"text": text, "files": linked, "from": rel})
    return {"notes": notes[:MAX_IMPORT], "more": max(0, len(notes) - MAX_IMPORT), "skipped_secrets": secrets_skipped}


def import_notes(root: Path) -> List[Memory]:
    made = [add(root, n["text"], n["files"], index=False) for n in notes_to_import(root)["notes"]]
    write_index(root)
    return made


# ---------- choices this person made ----------

def _listed(key: str, root: Path) -> bool:
    value = _settings().get(key)
    return isinstance(value, list) and _key(root) in value


def _set_listed(key: str, root: Path, on: bool) -> None:
    settings = _settings()
    old = settings.get(key) if isinstance(settings.get(key), list) else []
    settings[key] = [p for p in old if isinstance(p, str) and p != _key(root)] + ([_key(root)] if on else [])
    _save_settings(settings)


def ai_allowed(root: Path) -> bool:
    """Whether this person said their AI tools may share memories with this repo's team when asked to."""
    return _listed("team_ai", root)


def allow_ai(root: Path, on: bool) -> None:
    _set_listed("team_ai", root, on)


def warning_on() -> bool:
    """Whether to show "everyone with access to this repo will see this" before sharing. On until turned off."""
    return _settings().get("team_warning") is not False


def set_warning(on: bool) -> None:
    settings = _settings()
    settings["team_warning"] = bool(on)
    _save_settings(settings)


VISIBILITY = ("This memory will be visible to everyone with access to this repo. If the repo is public, "
              "that's anyone on the internet.")


def overlaps(root: Path, own: List[Tuple[str, str]], shared: List[Memory], at: float = 0.80) -> Dict[str, Tuple[str, float]]:
    """Which team memories are very close in meaning to one of this person's own: team id -> (own id, similarity).
    They either say the same thing (the personal copy can go) or disagree (someone should look)."""
    if not own or not shared:
        return {}
    vecs = _embeddings([text for _id, text in own] + [m.one_line for m in shared])
    own_vecs, shared_vecs = vecs[:len(own)], vecs[len(own):]
    out = {}
    for mem, sv in zip(shared, shared_vecs):
        score, own_id = max((_cosine(sv, ov), oid) for (oid, _t), ov in zip(own, own_vecs))
        if score >= at:
            out[mem.id] = (own_id, score)
    return out


# ---------- starter memories, drafted by the user's own AI tool ----------
#
# Mnemolith has no AI of its own and needs no API key. To give a new team memory something to start with, it
# asks the AI tool the user already has (Claude Code, run once, non-interactively) to read the repo and share
# what a new teammate's AI would need. Everything it writes passes the secret check and lands as files to review.

STARTER_ENV = "MNEMOLITH_TEAM_STARTER"  # set for that one run: the most team memories the repo may end up with
STARTER_PROMPT = """Read this repository and give its team memory a useful start.

First call the Mnemolith recall tool with "project setup, conventions and decisions" to see what the team already has.
Then look through the repo: the README and docs, build and test config, CI, and the main source folders.

Share what a new teammate's AI coding tool would most need to know and would otherwise get wrong, each as one
call to share_with_team(fact=..., files=[...]):
- how to build, test, run and release it, when that isn't obvious
- conventions the code follows that a newcomer would break
- decisions and the reason for them, where the repo states the reason
- who or what owns which part, and what must not be changed
- setup gotchas

Rules:
- Aim for 10 to 20 memories, at most {limit}. Fewer good ones beat many weak ones: each one is something the
  team has to review, and something every AI reads.
- Leave out what changes nothing about how someone works on the code: benchmark numbers, feature lists,
  limitations that are simply documented, history that no longer matters.
- One fact per memory, written so it makes sense on its own months from now.
- files= is how everyone learns the memory may be out of date, so link only the files where the fact is set or
  enforced: one or two, three at most. Link the code or config that holds the fact, not docs that describe it;
  link README.md only when the fact lives nowhere else.
- Only what the repo actually says or shows. Do not guess, and do not invent reasons.
- Nothing temporary, nothing about a single bug, and never a password, key or token.
- Do not edit, create or commit any file yourself.

When you are done, reply with one line: how many you shared."""
STARTER_MAX_FILES = 3
STARTER_TOOLS = ["Read", "Glob", "Grep", "Bash(git log:*)", "Bash(ls:*)", "mcp__mnemolith__recall",
                 "mcp__mnemolith__share_with_team"]

POINTER_MARK = "<!-- mnemolith:team -->"
POINTER = ("Team knowledge for AI tools is in mnemolith.md. If you don't have Mnemolith's recall tool, read that file "
           "before starting work.")


def starter_limit() -> Optional[int]:
    """During a starter run, the most team memories the repo may hold; None the rest of the time."""
    try:
        return int(os.environ[STARTER_ENV])
    except (KeyError, ValueError):
        return None


def starter_command(root: Path, claude: str, config_file: Path, limit: int = MAX_IMPORT) -> List[str]:
    """The Claude Code command that drafts starter memories, and the settings file it needs. It can read the
    repo and call two Mnemolith tools, nothing else: no edits, no shell beyond `git log` and `ls`."""
    import sys
    cap = len(load(root)) + limit
    servers = {"mnemolith": {"command": sys.executable, "args": ["-m", "mnemolith", "serve"],
                             "env": {STARTER_ENV: str(cap), "MNEMOLITH_REPO": str(root), "MNEMOLITH_SOURCE": "starter"}}}
    config_file.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return [claude, "-p", STARTER_PROMPT.format(limit=limit), "--mcp-config", str(config_file), "--strict-mcp-config",
            "--allowedTools", *STARTER_TOOLS]


def pointer_files(root: Path) -> List[Path]:
    """Notes files AI tools read on their own (CLAUDE.md, AGENTS.md) that don't point at mnemolith.md yet."""
    out = []
    for name in ("CLAUDE.md", "AGENTS.md"):
        path = root / name
        try:
            if path.is_file() and POINTER_MARK not in path.read_text(encoding="utf-8", errors="replace"):
                out.append(path)
        except OSError:
            pass
    return out


def add_pointer(root: Path) -> List[Path]:
    """Adds one line to CLAUDE.md / AGENTS.md telling AI tools without Mnemolith to read mnemolith.md. No AI tool
    reads that file by itself; they do read these."""
    done = []
    for path in pointer_files(root):
        raw = path.read_bytes().decode("utf-8", errors="replace")
        newline = "\r\n" if "\r\n" in raw else "\n"
        gap = "" if not raw else newline if raw.endswith(("\n", "\r")) else newline * 2
        with open(path, "a", encoding="utf-8", newline="") as f:
            f.write(gap + POINTER_MARK + newline + POINTER + newline)
        done.append(path)
    return done
