"""
Mender: drafts the fix for memories that went stale, and waits for you to approve it.

When a file a memory depends on changes, Mnemolith marks the memory stale. Mender then tries to draft what's true
now, two ways, neither of which needs an API key or sends anything anywhere:

1. Rules, in plain code. Many changes are mechanical: `postgres:16` became `mysql:8`, `redis==5.0.0` became
   `redis==5.2.1`, a file moved, or only a comment changed. Mender compares the version of each file the memory
   was saved against (from git) with the file now, and rewrites the memory to match, or suggests it's still true.
2. Your AI tool. For changes rules can't settle, the AI tool you already use (Claude Code, Cursor, Codex) reads
   the stale memories with `list_stale` and submits a draft with `propose_fix`.

Either way, a draft is only a suggestion: nothing changes until you approve it in the dashboard. Approving is
refused if the files changed again after the draft was made (it would be based on old code), or if the memory was
already settled some other way. Drafts from AI tools are checked against the code: quotes and new version numbers
must actually appear in the files, or the draft is marked unverified.

Mender is optional. `mnemolith setup` asks whether to turn it on; `mnemolith mender on|off` changes it later.
"""
import difflib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config, server

SETTINGS_FILE = config.SETTINGS_FILE
DRAFTS_KEY = "mnemolith:mender:"  # mnemolith:mender:<project> -> JSON {memory id: draft}
MAX_FILE_BYTES = 400_000          # bigger files aren't diffed (they're rarely what a memory is about)


# ---------- on / off ----------

def enabled() -> bool:
    return config.mender_enabled()


def set_enabled(on: bool) -> None:
    try:
        settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            settings = {}
    except (OSError, ValueError):
        settings = {}
    settings["mender"] = bool(on)
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, SETTINGS_FILE)


# ---------- the drafts store (in the engine, so the dashboard and every AI session share it) ----------

def load_drafts(project: str) -> Dict[str, dict]:
    raw = server.db().get(DRAFTS_KEY + project)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def save_drafts(project: str, drafts: Dict[str, dict]) -> None:
    server.db().set(DRAFTS_KEY + project, json.dumps(drafts) if drafts else "")  # the engine has no DEL


def put_draft(project: str, draft: dict) -> None:
    drafts = load_drafts(project)
    drafts[draft["memory_id"]] = draft
    save_drafts(project, drafts)


def drop_draft(project: str, memory_id: str) -> None:
    drafts = load_drafts(project)
    if drafts.pop(memory_id, None) is not None:
        save_drafts(project, drafts)


# ---------- reading memories and files ----------

def _pairs(reply) -> dict:
    return dict(zip(reply[0::2], reply[1::2]))


def _links(files: str) -> List[Tuple[str, str]]:
    """"a.py@<hash>,b.yml@<hash>" -> [("a.py", "<hash>"), ...]"""
    return [tuple(f.rsplit("@", 1)) for f in files.split(",") if "@" in f]


def get_memory(memory_id: str) -> Optional[dict]:
    got = server.run("MEM.GET", memory_id)
    return _pairs(got) if got else None


def needs_decision(memory: dict) -> bool:
    """Stale because a linked file changed (not retired on purpose by someone)."""
    return memory.get("status") == "stale" and server.status_reason(memory["id"]).endswith(server.FILE_CHANGE)


def _git(root: Path, *args: str, stdin: Optional[str] = None) -> Optional[str]:
    try:
        r = subprocess.run(["git", *args], cwd=root, input=stdin, capture_output=True, text=True,
                           timeout=10, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def old_version(root: Path, blob: str) -> Optional[str]:
    """A file's contents as they were when the memory was saved, if git has that version (it does once the
    file was committed or staged at that version)."""
    if not blob or blob == "missing" or not re.fullmatch(r"[0-9a-f]{40}", blob):
        return None
    out = _git(root, "cat-file", "-p", blob)
    return out if out is not None and len(out) <= MAX_FILE_BYTES else None


def current_version(root: Path, path: str) -> Optional[str]:
    p = _inside(root, path)
    if p is None or not p.is_file():
        return None
    try:
        if p.stat().st_size > MAX_FILE_BYTES:
            return None
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _inside(root: Path, path: str) -> Optional[Path]:
    p = (root / path).resolve()
    r = root.resolve()
    return p if p == r or r in p.parents else None


def find_move(root: Path, path: str, blob: str) -> Optional[str]:
    """Where a missing file went: another file with exactly the saved contents, or git's own rename detection
    (for files that were moved and edited). Returns the new repo-relative path, or None."""
    if blob and blob != "missing":
        for line in (_git(root, "ls-files", "-s") or "").splitlines():
            meta, _, other = line.partition("\t")
            parts = meta.split()
            if len(parts) >= 2 and parts[1] == blob and other != path and (root / other).is_file():
                return other
        untracked = [u for u in (_git(root, "ls-files", "-o", "--exclude-standard") or "").splitlines() if u]
        if untracked:
            hashes = server.content_hashes(untracked, root)
            for other in untracked:
                if hashes.get(other) == blob:
                    return other
    for line in (_git(root, "diff", "HEAD", "-M40%", "--name-status") or "").splitlines():
        cols = line.split("\t")
        if len(cols) == 3 and cols[0].startswith("R") and cols[1] == path:
            return cols[2]
    return None


# ---------- the rules ----------

TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*")
TOKEN_CHARS = re.compile(r"[A-Za-z0-9_.\-]")
VERSION = re.compile(r"\d+(?:\.\d+)+")
COMMENT = re.compile(r"^\s*(#|//|/\*|\*|--|;|<!--|%)")
# Names people write with particular capitals, so "Postgres" -> "MySQL", not "Mysql"
NAMES = {n.lower(): n for n in (
    "MySQL", "PostgreSQL", "Postgres", "SQLite", "MariaDB", "MongoDB", "Redis", "Valkey", "Memcached", "DynamoDB",
    "CockroachDB", "ClickHouse", "Cassandra", "Elasticsearch", "OpenSearch", "Kafka", "RabbitMQ", "NATS",
    "Node", "Deno", "Bun", "npm", "pnpm", "Yarn", "Python", "Poetry", "uv", "pip", "Ruby", "Go", "Rust", "Java",
    "Kotlin", "TypeScript", "JavaScript", "React", "Vue", "Svelte", "Angular", "Next.js", "Nuxt", "Vite", "Webpack",
    "Jest", "Vitest", "pytest", "Django", "Flask", "FastAPI", "Rails", "Spring", "Express", "Nginx", "Caddy",
    "Apache", "Docker", "Podman", "Kubernetes", "Terraform", "AWS", "GCP", "Azure", "GitHub", "GitLab",
    "Ubuntu", "Debian", "Alpine", "Gunicorn", "Uvicorn", "Celery", "Sidekiq", "GraphQL", "gRPC", "Prisma")}
# Words too common to mean a changed line is about the memory
COMMON = {"the", "and", "for", "with", "this", "that", "our", "use", "uses", "used", "are", "was", "were", "has",
          "have", "not", "but", "you", "its", "all", "any", "can", "into", "from", "when", "then", "than", "also",
          "set", "run", "runs", "need", "needs", "make", "file", "files", "project", "should", "must", "will",
          "via", "per", "true", "false", "yes", "none", "null"}


def _tokens(text: str) -> List[str]:
    return [t.rstrip(".-") for t in TOKEN.findall(text) if t.rstrip(".-")]


def _key_words(text: str) -> set:
    """The words in a memory that a changed line would have to mention to be about it."""
    return {t.lower() for t in _tokens(text)
            if (len(t) >= 3 or any(c.isdigit() for c in t)) and t.lower() not in COMMON}


def _word_re(word: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(word) + r"(?![A-Za-z0-9_])", re.IGNORECASE)


def _styled(new: str, found: str) -> str:
    """The replacement, written the way the memory wrote the old word (and with a known name's own capitals)."""
    if found.isupper() and len(found) > 1:
        return new.upper()
    if new.lower() in NAMES and any(c.isupper() for c in found):
        return NAMES[new.lower()]
    if found[:1].isupper() and new[:1].islower():
        return new[:1].upper() + new[1:]
    return new


def _changed_span(old: str, new: str) -> Tuple[str, str]:
    """The part of a line that changed, widened to whole words: "image: postgres:16" vs "image: mysql:8"
    -> ("postgres:16", "mysql:8")."""
    i = 0
    while i < min(len(old), len(new)) and old[i] == new[i]:
        i += 1
    while i > 0 and TOKEN_CHARS.match(old[i - 1]):
        i -= 1
    j = 0
    while j < min(len(old), len(new)) - i and old[len(old) - 1 - j] == new[len(new) - 1 - j]:
        j += 1
    while j > 0 and TOKEN_CHARS.match(old[len(old) - j]):
        j -= 1
    return old[i:len(old) - j].strip(), new[i:len(new) - j].strip()


def _line_pairs(old_lines: List[str], new_lines: List[str]) -> Tuple[List[Tuple[str, str]], List[str], List[str]]:
    """Changed lines, as (old, new) pairs where a line was edited, plus lines only removed and only added."""
    pairs, removed, added = [], [], []
    sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        olds, news = old_lines[i1:i2], new_lines[j1:j2]
        used = set()
        for o in olds:
            best, best_j = 0.0, None
            for j, n in enumerate(news):
                if j in used:
                    continue
                r = difflib.SequenceMatcher(None, o, n).ratio()
                if r > best:
                    best, best_j = r, j
            if best_j is not None and best >= 0.45:
                used.add(best_j)
                pairs.append((o, news[best_j]))
            else:
                removed.append(o)
        added += [n for j, n in enumerate(news) if j not in used]
    return pairs, removed, added


def swaps_for(old_line: str, new_line: str) -> List[Tuple[str, str]]:
    """Value swaps one edited line implies, most specific first: the whole changed value ("postgres:16" ->
    "mysql:8"), then single words and versions inside it ("postgres" -> "mysql")."""
    out = []
    o, n = _changed_span(old_line, new_line)
    if o and n and o != n:
        out.append((o, n))
    ot, nt = _tokens(o), _tokens(n)
    sm = difflib.SequenceMatcher(None, [t.lower() for t in ot], [t.lower() for t in nt], autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "replace" and i2 - i1 == j2 - j1:
            for a, b in zip(ot[i1:i2], nt[j1:j2]):
                if a.lower() != b.lower() and (a, b) not in out:
                    out.append((a, b))
    return out


def _usable_swap(old: str, new: str) -> bool:
    if not old or not new or len(old) > 120 or len(new) > 120:
        return False
    if old.isdigit() and len(old) < 2:  # "8" -> "9" alone is too easy to match by accident
        return False
    return len(old) >= 2


def apply_swaps(text: str, swaps: List[Tuple[str, str]]) -> Tuple[str, List[Tuple[str, str]]]:
    """Rewrites a memory with the swaps that appear in it. Returns the new text and the swaps used."""
    used = []
    for old, new in swaps:
        if not _usable_swap(old, new):
            continue
        pattern = _word_re(old)
        if not pattern.search(text):
            continue
        text = pattern.sub(lambda m: _styled(new, m.group(0)), text)
        used.append((old, new))
    return text, used


def _related(line: str, key_words: set) -> bool:
    return bool({t.lower() for t in _tokens(line)} & key_words)


def draft_with_rules(memory: dict, root: Path) -> Optional[dict]:
    """A draft for a stale memory, worked out from what changed in its linked files, or None when the change
    needs judgment (then the AI tool can propose one)."""
    text = memory["text"]
    links = _links(memory.get("files", ""))
    if not links:
        return None
    current = server.content_hashes([p for p, _ in links], root)
    key_words = _key_words(text)
    new_text = text
    files, evidence, swaps_used = [], [], []
    undecided = False   # a change we can't judge from the files alone
    touched = False     # a change that's about this memory

    for path, saved in links:
        now = current.get(path)
        if now == saved:
            files.append(path)
            continue
        if now == "missing":
            moved = find_move(root, path, saved)
            if not moved:
                undecided = True  # deleted: whether the fact still holds is a human (or AI) call
                evidence.append({"kind": "file", "label": f"{path} was deleted", "detail": ""})
                continue
            files.append(moved)
            evidence.append({"kind": "file", "label": f"{path} moved to {moved}", "detail": ""})
            before = new_text
            new_text, used = apply_swaps(new_text, [(path, moved)] + (
                [(Path(path).name, Path(moved).name)] if Path(path).name != Path(moved).name else []))
            swaps_used += used
            touched |= new_text != before
            # the moved file may also have been edited
            old_body, new_body = old_version(root, saved), current_version(root, moved)
            if old_body is not None and new_body is not None and old_body != new_body:
                verdict = _compare(old_body, new_body, new_text, key_words, path)
                new_text, undecided, touched = verdict["text"], undecided or verdict["undecided"], touched or verdict["touched"]
                swaps_used += verdict["swaps"]
                evidence += verdict["evidence"]
            continue
        files.append(path)
        old_body, new_body = old_version(root, saved), current_version(root, path)
        if old_body is None or new_body is None:
            undecided = True  # the saved version isn't in git (never committed), so there's nothing to compare
            evidence.append({"kind": "file", "label": f"{path} changed, but the saved version isn't in git",
                             "detail": ""})
            continue
        verdict = _compare(old_body, new_body, new_text, key_words, path)
        new_text, undecided, touched = verdict["text"], undecided or verdict["undecided"], touched or verdict["touched"]
        swaps_used += verdict["swaps"]
        evidence += verdict["evidence"]

    if new_text != text:
        reason = "; ".join(f"{a} → {b}" for a, b in _dedupe(swaps_used))
        return _draft(memory, "replace", new_text, f"Changed in the linked files: {reason}.", files, root,
                      evidence, warnings=["Other changed lines may matter too: check the diff."] if undecided else [])
    if undecided or touched:
        return None
    # every changed line was a comment, whitespace, or about something this memory doesn't mention
    return _draft(memory, "confirm", text, "Only lines this memory doesn't mention changed (or only comments).",
                  files, root, evidence or change_evidence(memory, root))


def _compare(old_body: str, new_body: str, text: str, key_words: set, path: str) -> dict:
    pairs, removed, added = _line_pairs(old_body.splitlines(), new_body.splitlines())
    swaps, related, evidence = [], False, []
    for o, n in pairs:
        if COMMENT.match(o) and COMMENT.match(n):
            continue
        if o.strip() == n.strip():
            continue
        o_span, n_span = _changed_span(o, n)
        # an edited line is about the memory only if what changed in it is: "postgres:16" -> "postgres:17"
        # doesn't touch "we use Postgres"
        if _related(o_span, key_words) or _related(n_span, key_words):
            related = True
            swaps += swaps_for(o, n)
            evidence.append({"kind": "diff", "label": path, "detail": f"- {o.strip()}\n+ {n.strip()}"})
    for line, sign in [(l, "-") for l in removed] + [(l, "+") for l in added]:
        if not line.strip() or COMMENT.match(line):
            continue
        if _related(line, key_words):
            related = True
            evidence.append({"kind": "diff", "label": path, "detail": f"{sign} {line.strip()}"})
    new_text, used = apply_swaps(text, swaps)
    # Related lines that no swap explains (a line added or removed outright, or an edit the memory words
    # differently) need judgment, so the change isn't settled by rules.
    explained = bool(used) and not removed_or_added_related(removed, added, key_words)
    return {"text": new_text, "swaps": used, "touched": related, "evidence": evidence[:6],
            "undecided": related and not explained}


def removed_or_added_related(removed: List[str], added: List[str], key_words: set) -> bool:
    return any(_related(l, key_words) and not COMMENT.match(l) for l in removed + added if l.strip())


def change_evidence(memory: dict, root: Path, limit: int = 8) -> List[dict]:
    """The lines that changed in a memory's linked files, as evidence to show next to a draft."""
    out = []
    for path, saved in _links(memory.get("files", "")):
        old, now = old_version(root, saved), current_version(root, path)
        if now is None and not (root / path).exists():
            moved = find_move(root, path, saved)
            out.append({"kind": "file", "label": f"{path} moved to {moved}" if moved else f"{path} was deleted", "detail": ""})
            continue
        if old is None or now is None or old == now:
            continue
        lines = [l for l in difflib.unified_diff(old.splitlines(), now.splitlines(), lineterm="", n=0)
                 if l[:1] in "+-" and not l.startswith(("+++", "---")) and l[1:].strip()]
        if lines:
            shown = [f"{l[0]} {l[1:].strip()}" for l in lines[:limit]]
            out.append({"kind": "diff", "label": path, "detail": "\n".join(shown) + ("\n…" if len(lines) > limit else "")})
    return out


def _dedupe(items):
    seen, out = set(), []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _draft(memory: dict, kind: str, text: str, reason: str, files: List[str], root: Path,
           evidence: List[dict], by: str = "rules", warnings: Optional[List[str]] = None) -> dict:
    paths = sorted(set(files) | {p for p, _ in _links(memory.get("files", ""))})
    return {"memory_id": memory["id"], "kind": kind, "text": " ".join(text.split()), "reason": reason,
            "files": _dedupe(files), "file_hashes": server.content_hashes(paths, root), "evidence": evidence,
            "by": by, "warnings": warnings or [], "created_at": int(time.time() * 1000)}


# ---------- keeping drafts up to date ----------

def drifted(draft: dict, root: Path) -> bool:
    """True when a file the draft was based on changed after it was drafted."""
    saved = draft.get("file_hashes") or {}
    return bool(saved) and server.content_hashes(list(saved), root) != saved


def refresh(project: str, root: Optional[Path]) -> Dict[str, dict]:
    """Drafts for this project's stale memories: drops drafts whose memory was settled some other way, redoes
    rule drafts whose files changed again, and drafts any new stale memory the rules can handle. Returns the
    current drafts. Does nothing when Mender is off."""
    if not enabled():
        return {}
    drafts = load_drafts(project)
    before = json.dumps(drafts, sort_keys=True)
    stale = {}
    for mid, status, files, text in server.run("MEM.LIST", project, "STATUS", "stale"):
        stale[mid] = {"id": mid, "status": status, "files": files, "text": text}
    for mid in list(drafts):
        if mid not in stale or not needs_decision(stale[mid]):
            del drafts[mid]
    if root is not None:
        for mid, memory in stale.items():
            if mid in drafts and drafts[mid].get("dismissed"):
                if not drifted(drafts[mid], root):
                    continue  # you dismissed this one and nothing changed since
                del drafts[mid]
            existing = drafts.get(mid)
            if existing and not (existing.get("by") == "rules" and drifted(existing, root)):
                continue
            if not needs_decision(memory):
                continue
            new = draft_with_rules(memory, root)
            if new:
                drafts[mid] = new
            elif existing:
                del drafts[mid]
    if json.dumps(drafts, sort_keys=True) != before:
        save_drafts(project, drafts)
    return drafts


# ---------- drafts from AI tools ----------

QUOTE = re.compile(r'"([^"\n]{4,200})"|“([^”\n]{4,200})”|`([^`\n]{2,200})`')


def _in_repo(root: Path, needle: str) -> bool:
    out = _git(root, "grep", "-I", "-F", "-q", "-i", "--", needle)
    return out is not None


def ground(memory: dict, new_text: str, reason: str, root: Optional[Path]) -> List[str]:
    """Checks a draft from an AI tool against the code. Returns warnings for claims that can't be found:
    quotes in the reason, and numbers or versions in the new memory that weren't in the old one."""
    if root is None:
        return ["Couldn't check this draft against the files: the repo's location isn't known."]
    bodies = []
    for path, saved in _links(memory.get("files", "")):
        for body in (old_version(root, saved), current_version(root, path)):
            if body:
                bodies.append(body.lower())
    seen = "\n".join(bodies)
    warnings = []
    for groups in QUOTE.findall(reason or ""):
        quote = next(g for g in groups if g).strip()
        if quote.lower() not in seen and not _in_repo(root, quote):
            warnings.append(f"The reason quotes “{quote}”, which isn't in the linked files or the repo.")
    old_numbers = {t.lower() for t in _tokens(memory["text"]) if any(c.isdigit() for c in t)}
    for t in _tokens(new_text):
        if any(c.isdigit() for c in t) and len(t) >= 2 and t.lower() not in old_numbers:
            if t.lower() not in seen and not _in_repo(root, t):
                warnings.append(f"“{t}” isn't in the linked files or the repo.")
    return _dedupe(warnings)[:4]


def propose(memory_id: str, new_text: str, reason: str, still_true: bool, by: str,
            files: Optional[List[str]], root: Optional[Path]) -> str:
    """Saves a draft from an AI tool for the user to approve. Returns a message for the AI."""
    memory = get_memory(memory_id)
    if memory is None:
        return f"No memory with id {memory_id}."
    if not needs_decision(memory):
        return (f"{memory_id} isn't waiting for a decision (it's {memory['status']}), so there's nothing to fix. "
                f"Call list_stale to see the ones that are.")
    text = " ".join((new_text or "").split())
    if not still_true and not text:
        return "Pass new_text (what's true now), or still_true=True if the memory is still right."
    if len(text) > 2000:
        return "That's too long for one memory. Keep it to a sentence or two."
    if root is None:
        return "Couldn't find this project's repo, so the draft can't be checked against the files."
    linked = [p for p, _ in _links(memory.get("files", ""))]
    paths = [server.relative(p, root) for p in (files or [])] or linked
    paths = [p for p in paths if _inside(root, p) is not None]
    missing = [p for p in paths if not (root / p).is_file()]
    if missing:
        return f"These files don't exist: {', '.join(missing)}. Pass the files the new fact depends on now."
    evidence = change_evidence(memory, root)
    if still_true:
        draft = _draft(memory, "confirm", memory["text"], " ".join((reason or "Still true.").split())[:600], paths,
                       root, evidence, by=by)
    else:
        draft = _draft(memory, "replace", text, " ".join((reason or "").split())[:600] or "Drafted by your AI tool.",
                       paths, root, evidence, by=by, warnings=ground(memory, text, reason, root))
    put_draft(memory["project"], draft)
    note = f" Unverified: {' '.join(draft['warnings'])}" if draft["warnings"] else ""
    return (f"Draft saved for {memory_id}. Nothing changes until the user approves it in the dashboard "
            f"(`mnemolith ui`), so tell them it's waiting.{note}")


# ---------- approving ----------

class Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


BY_LABEL = {"rules": "Mender's rules"}


def by_label(by: str) -> str:
    return BY_LABEL.get(by, by.replace("-", " ").title() if by and by != "unknown" else "your AI tool")


def approve(project: str, memory_id: str, root: Optional[Path], text: Optional[str] = None) -> dict:
    """Applies a draft: replaces the memory with the drafted (or edited) text linked to the current files, or
    confirms it's still true. Refuses if the files changed since the draft, or the memory was already settled."""
    drafts = load_drafts(project)
    draft = drafts.get(memory_id)
    if not draft or draft.get("dismissed"):
        raise Refused(404, f"There's no draft for {memory_id}.")
    memory = get_memory(memory_id)
    if memory is None or not needs_decision(memory):
        drop_draft(project, memory_id)
        raise Refused(409, f"{memory_id} was already settled some other way, so the draft was dropped.")
    if root is None:
        raise Refused(409, f"The dashboard doesn't know where the {project} repo is yet. Add it with Add project.")
    if drifted(draft, root):
        raise Refused(409, "The files changed again since this was drafted, so it may be out of date. "
                           "Reload to get a fresh draft.")
    files = [f for f in draft.get("files", []) if (root / f).is_file()]
    who = by_label(draft.get("by", ""))
    before = {"old_id": memory_id, "old_reason": server.status_reason(memory_id), "old_files": memory.get("files", ""),
              "by": draft.get("by", ""), "draft": draft}
    if draft["kind"] == "confirm" and text is None:
        server.run("MEM.FILES", memory_id, server.link_files(files, root) if files else "",
                   "REASON", f"confirmed still true (suggested by {who})")
        server.run("MEM.STATUS", memory_id, "active", "REASON", f"confirmed still true (suggested by {who})")
        drop_draft(project, memory_id)
        _record(project, memory_id, dict(before, kind="confirm"))
        return {"ok": True, "message": f"{memory_id} confirmed still true."}
    new_text = " ".join((text if text is not None else draft["text"]).split())
    if not new_text:
        raise Refused(400, "The memory can't be empty.")
    if len(new_text) > 2000:
        raise Refused(400, "That's too long for one memory. Keep it to a sentence or two.")
    if new_text == " ".join(memory["text"].split()):
        server.run("MEM.FILES", memory_id, server.link_files(files, root) if files else "",
                   "REASON", "confirmed still true")
        server.run("MEM.STATUS", memory_id, "active", "REASON", "confirmed still true")
        drop_draft(project, memory_id)
        _record(project, memory_id, dict(before, kind="confirm"))
        return {"ok": True, "message": f"{memory_id} confirmed still true."}
    new_id, _outcome, _related = server.add_memory(project, new_text, server.link_files(files, root) if files else "",
                                                   source="mender")
    if new_id == memory_id:
        server.run("MEM.STATUS", memory_id, "active", "REASON", "confirmed still true")
    else:
        edited = text is not None and " ".join(text.split()) != draft["text"]
        how = f"drafted by {who}" + (", edited" if edited else "") + ", approved in the dashboard"
        server.run("MEM.STATUS", memory_id, "superseded", "REASON", f'replaced by {new_id}: "{new_text}" ({how})')
    drop_draft(project, memory_id)
    _record(project, new_id, dict(before, kind="replace" if new_id != memory_id else "confirm", new_id=new_id))
    return {"ok": True, "new_id": new_id, "message": f"{memory_id} replaced by {new_id}. Your AI tools will use the new fact."}


# ---------- reverting an approved fix, any time later ----------

APPLIED_KEY = "mnemolith:mender-applied:"  # mnemolith:mender-applied:<project> -> JSON {memory id now in use: record}
MAX_APPLIED = 300


def applied(project: str) -> Dict[str, dict]:
    raw = server.db().get(APPLIED_KEY + project)
    try:
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _record(project: str, memory_id: str, record: dict) -> None:
    """Remembers what an approval changed, so it can be reverted from the memory's details later."""
    records = applied(project)
    record["at"] = int(time.time() * 1000)
    records[memory_id] = record
    if len(records) > MAX_APPLIED:
        for old in sorted(records, key=lambda k: records[k].get("at", 0))[:len(records) - MAX_APPLIED]:
            del records[old]
    server.db().set(APPLIED_KEY + project, json.dumps(records))


def _forget(project: str, memory_id: str) -> None:
    records = applied(project)
    if records.pop(memory_id, None) is not None:
        server.db().set(APPLIED_KEY + project, json.dumps(records) if records else "")


def fix_info(project: str, memory_id: str) -> Optional[dict]:
    """How Mender fixed this memory, if it did and that can still be reverted."""
    record = applied(project).get(memory_id)
    if not record:
        return None
    current = get_memory(memory_id)
    if current is None or current.get("status") != "active":
        return None  # changed again since: reverting would undo more than this fix
    return {"kind": record["kind"], "old_id": record["old_id"], "by": by_label(record.get("by", "")),
            "at": record.get("at")}


def revert(project: str, memory_id: str, root: Optional[Path]) -> dict:
    """Takes back a fix you approved: the new memory is retired, and the old one goes back to waiting for a decision,
    linked to the files it had. Mender won't suggest the same fix again until the files change."""
    record = applied(project).get(memory_id)
    if not record or fix_info(project, memory_id) is None:
        raise Refused(404, f"{memory_id} isn't a fix Mender made that can still be reverted.")
    old_id = record["old_id"]
    old = get_memory(old_id)
    if old is None:
        _forget(project, memory_id)
        raise Refused(404, f"{old_id} no longer exists.")
    if record["kind"] == "replace":
        server.run("MEM.STATUS", memory_id, "stale", "REASON", f"fix reverted in the dashboard; {old_id} is back")
    if record.get("old_files") is not None and record["old_files"] != old.get("files", ""):
        server.run("MEM.FILES", old_id, record["old_files"], "REASON", "fix reverted")
    reason = record.get("old_reason") or ""
    if not reason.endswith(server.FILE_CHANGE):
        reason = "a linked file changed since this was saved"
    server.run("MEM.STATUS", old_id, "stale", "REASON", reason)
    _forget(project, memory_id)
    # Don't suggest the fix that was just reverted again, until the files change.
    drafts = load_drafts(project)
    hashes = (record.get("draft") or {}).get("file_hashes") or {}
    if root is not None and hashes:
        hashes = server.content_hashes(list(hashes), root)
    drafts[old_id] = {"memory_id": old_id, "dismissed": True, "reverted": True, "file_hashes": hashes,
                      "previous": record.get("draft")}
    save_drafts(project, drafts)
    what = f"{memory_id} retired and {old_id} is back" if record["kind"] == "replace" else f"{old_id} is back"
    return {"ok": True, "old_id": old_id,
            "message": f"Fix reverted: {what}, waiting for your decision. Mender won't suggest that fix again unless the files change."}


def waiting(project: str) -> int:
    """How many drafted fixes are waiting for approval in this project (as of the last check)."""
    return sum(1 for d in load_drafts(project).values() if not d.get("dismissed"))


def dismiss(project: str, memory_id: str, root: Optional[Path]) -> dict:
    """Hides a draft without changing the memory. Mender won't draft it again until its files change."""
    drafts = load_drafts(project)
    if memory_id not in drafts:
        raise Refused(404, f"There's no draft for {memory_id}.")
    keep = drafts[memory_id].get("file_hashes") or {}
    if root is not None and keep:
        keep = server.content_hashes(list(keep), root)
    previous = drafts[memory_id] if not drafts[memory_id].get("dismissed") else drafts[memory_id].get("previous")
    drafts[memory_id] = {"memory_id": memory_id, "dismissed": True, "file_hashes": keep, "previous": previous}
    save_drafts(project, drafts)
    return {"ok": True, "message": "Draft dismissed. The memory is still waiting for a decision."}


def undismiss(project: str, memory_id: str) -> dict:
    """Brings back a draft you just dismissed (the dashboard's Undo)."""
    drafts = load_drafts(project)
    entry = drafts.get(memory_id)
    if not entry or not entry.get("dismissed") or not entry.get("previous"):
        raise Refused(404, f"There's no dismissed draft for {memory_id} to bring back.")
    drafts[memory_id] = entry["previous"]
    save_drafts(project, drafts)
    return {"ok": True, "message": "Draft back."}


# ---------- what an AI tool sees ----------

def describe_stale(project: str, root: Optional[Path], limit: int = 10) -> str:
    """The stale memories waiting for a decision, with what changed in their files, for list_stale."""
    drafts = refresh(project, root)
    rows = [r for r in server.run("MEM.LIST", project, "STATUS", "stale")]
    waiting = []
    for mid, status, files, text in rows:
        memory = {"id": mid, "status": status, "files": files, "text": text}
        if needs_decision(memory):
            waiting.append(memory)
    if not waiting:
        return "No stale memories: every memory's linked files still match. Nothing to fix."
    out = []
    for memory in waiting[:limit]:
        mid = memory["id"]
        draft = drafts.get(mid)
        lines = [f"[{mid}] \"{memory['text']}\"", f"  Why stale: {server.status_reason(mid)}."]
        if draft and not draft.get("dismissed"):
            what = "still true" if draft["kind"] == "confirm" else f"\"{draft['text']}\""
            lines.append(f"  A draft is already waiting for the user's approval ({by_label(draft.get('by', ''))}): {what}. "
                         f"Skip this one unless you think the draft is wrong.")
        if root is not None:
            for path, saved in _links(memory["files"]):
                now = current_version(root, path)
                old = old_version(root, saved)
                if now is None and not (root / path).exists():
                    moved = find_move(root, path, saved)
                    lines.append(f"  {path}: deleted" + (f" (looks moved to {moved})" if moved else ""))
                elif old is None:
                    lines.append(f"  {path}: changed; the saved version isn't in git, so read the file as it is now.")
                elif now is not None and old != now:
                    diff = list(difflib.unified_diff(old.splitlines(), now.splitlines(), f"a/{path}", f"b/{path}",
                                                     lineterm="", n=1))
                    body = "\n".join("    " + d for d in diff[2:40])
                    lines.append(f"  {path} changed:\n{body}" + ("\n    ..." if len(diff) > 42 else ""))
        out.append("\n".join(lines))
    more = f"\n\n(and {len(waiting) - limit} more)" if len(waiting) > limit else ""
    return ("Stale memories waiting for a decision. For each one without a draft, check the code, then call "
            "propose_fix with what's true now (or still_true=True). Base it only on what you read in the repo, and "
            "say in the reason what you checked. The user approves each draft in the dashboard; nothing changes "
            "until then.\n\n" + "\n\n".join(out) + more)
