"""Registering Mnemolith with AI coding tools, so they start its MCP server on their own."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

NAME = "mnemolith"
OLD_NAMES = ("aether-cache", "memstore")  # what the server was called before; replaced on connect
TOOLS = ["claude-code", "claude-desktop", "cursor", "codex"]
DESKTOP_PROJECT = "claude-desktop"  # Claude Desktop isn't opened in a project folder, so its memories share one


def settings_env() -> dict:
    """Non-default settings from this shell (a custom port or data folder) that the tools must use too."""
    return {k: os.environ[k] for k in ("MNEMOLITH_PORT", "MNEMOLITH_HOME") if os.environ.get(k)}


def server_command() -> List[str]:
    """How a tool launches the server: this exact Python, so it works even when the tool's PATH
    doesn't include the folder the `mnemolith` command was installed into."""
    return [sys.executable, "-m", "mnemolith", "serve"]


def cursor_config() -> Path:
    return Path.home() / ".cursor" / "mcp.json"


def claude_desktop_config() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "Claude" / "claude_desktop_config.json"


def codex_config() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"


def detected() -> List[str]:
    """The supported tools that look installed on this machine."""
    found = []
    if shutil.which("claude"):
        found.append("claude-code")
    if claude_desktop_config().parent.is_dir():
        found.append("claude-desktop")
    if (Path.home() / ".cursor").is_dir():
        found.append("cursor")
    if shutil.which("codex") or codex_config().parent.is_dir():
        found.append("codex")
    return found


# ---------- instruction files ----------

INSTR_START = "<!-- mnemolith:instructions:start -->"
INSTR_END = "<!-- mnemolith:instructions:end -->"
OLD_INSTR = [("<!-- aether-cache:instructions:start -->", "<!-- aether-cache:instructions:end -->")]


def _drop_old_sections(text: str) -> str:
    """Removes the instruction section written under the project's earlier name, if there is one."""
    for start, end in OLD_INSTR:
        if start in text and end in text:
            before, after = text[:text.index(start)], text[text.index(end) + len(end):]
            text = before.rstrip() + ("\n\n" + after.lstrip("\n") if before.strip() and after.strip() else after.lstrip("\n"))
    return text


def add_instructions(path: Path) -> str:
    """Adds (or refreshes) a short, clearly marked section telling the AI to use Mnemolith for project
    memory. Without it, tools tend to reach for their built-in memory instead. Nothing else is touched."""
    from . import config
    from .server import instructions
    block = f"{INSTR_START}\n## Project memory\n{instructions(config.mender_enabled())}\n{INSTR_END}"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    current = _drop_old_sections(old)
    if INSTR_START in current and INSTR_END in current:
        new = current[:current.index(INSTR_START)] + block + current[current.index(INSTR_END) + len(INSTR_END):]
    else:
        new = (current.rstrip() + "\n\n" if current.strip() else "") + block + "\n"
    if new != old:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".mnemolith-tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, path)
    return f"told it to use Mnemolith for project memory in {path}"


def remove_instructions(path: Path) -> bool:
    """Takes our section back out of an instruction file, leaving the rest exactly as it was.
    Deletes the file only if our section was all that was in it. Returns whether anything changed."""
    try:
        old = path.read_text(encoding="utf-8")
    except OSError:
        return False
    new = _drop_old_sections(old)
    if INSTR_START in new and INSTR_END in new:
        before, after = new[:new.index(INSTR_START)], new[new.index(INSTR_END) + len(INSTR_END):]
        new = before.rstrip() + ("\n\n" + after.lstrip("\n") if before.strip() and after.strip() else after.lstrip("\n"))
    if new == old:
        return False
    if not new.strip():
        path.unlink()
        return True
    if not new.endswith("\n"):
        new += "\n"
    tmp = path.with_name(path.name + ".mnemolith-tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, path)
    return True


def claude_instructions_file() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "CLAUDE.md"


# ---------- Claude Code ----------

def _claude(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([shutil.which("claude") or "claude", *args], capture_output=True, text=True, timeout=60)


def connect_claude_code() -> Tuple[bool, str]:
    if not shutil.which("claude"):
        return False, "Claude Code isn't installed (no `claude` command found)."
    notes = []
    # replace any earlier registration, including those from the project's earlier names
    for old in (NAME, *OLD_NAMES):
        if _claude("mcp", "get", old).returncode == 0:
            for scope in ("user", "local"):
                if _claude("mcp", "remove", old, "--scope", scope).returncode == 0 and old != NAME:
                    notes.append(f"replaced the old `{old}` entry")
    cmd = server_command()
    env_flags = [x for k, v in settings_env().items() for x in ("-e", f"{k}={v}")]
    out = _claude("mcp", "add", NAME, "--scope", "user", "-e", "MEMORY_SOURCE=claude-code", *env_flags, "--", *cmd)
    if out.returncode != 0:
        return False, f"`claude mcp add` failed: {(out.stderr or out.stdout).strip()}"
    notes.append(add_instructions(claude_instructions_file()))
    return True, "Claude Code: connected for all projects (" + "; ".join(notes) + ")."


def disconnect_claude_code() -> Tuple[bool, str]:
    notes = []
    if shutil.which("claude"):
        removed = [scope for name in (NAME, *OLD_NAMES) for scope in ("user", "local")
                   if _claude("mcp", "remove", name, "--scope", scope).returncode == 0]
        if removed:
            notes.append("removed the MCP server")
        if _claude("mcp", "get", NAME).returncode == 0:
            return False, "Claude Code: couldn't remove it. Run `claude mcp remove mnemolith` yourself."
    if remove_instructions(claude_instructions_file()):
        notes.append(f"removed our section from {claude_instructions_file()}")
    return True, "Claude Code: disconnected" + (f" ({'; '.join(notes)})." if notes else " (it wasn't connected).")


def claude_code_connected() -> bool:
    return bool(shutil.which("claude")) and _claude("mcp", "get", NAME).returncode == 0


# ---------- Cursor ----------

def _server_entry(source: str, workspace_var: str = "") -> dict:
    cmd = server_command()
    env = {"MEMORY_SOURCE": source, **settings_env()}
    if workspace_var:
        env["MNEMOLITH_REPO"] = workspace_var  # tells the server which project folder is open
    return {"command": cmd[0], "args": cmd[1:], "env": env}


def _read_json(path: Path):
    """(data, error): the config as a dict, {} if the file is missing or empty."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}, None
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as e:
        return None, str(e)
    return (data, None) if isinstance(data, dict) else (None, "the file isn't a JSON object")


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copyfile(path, path.with_name(path.name + ".bak"))
    tmp = path.with_name(path.name + ".mnemolith-tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _connect_json(path: Path, entry: dict, label: str, done_message: str) -> Tuple[bool, str]:
    data, error = _read_json(path)
    if data is None:
        return False, (f"Couldn't read {path} ({error}). Fix it, or add this under \"mcpServers\" yourself:\n"
                       + json.dumps({NAME: entry}, indent=2))
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        return False, f"{label}: \"mcpServers\" in {path} isn't an object. Fix it, then try again."
    for old in OLD_NAMES:
        servers.pop(old, None)  # replaced by the entry below
    servers[NAME] = entry
    _write_json(path, data)
    return True, done_message


def _disconnect_json(path: Path, label: str, restart: str) -> Tuple[bool, str]:
    if not path.exists():
        return True, f"{label}: disconnected (it wasn't connected)."
    data, error = _read_json(path)
    if data is None:
        return False, f"{label}: couldn't read {path} ({error}). Remove the \"{NAME}\" entry yourself."
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or not any(n in servers for n in (NAME, *OLD_NAMES)):
        return True, f"{label}: disconnected (it wasn't connected)."
    for n in (NAME, *OLD_NAMES):
        servers.pop(n, None)
    _write_json(path, data)
    return True, f"{label}: disconnected ({path}). {restart}"


def _json_connected(path: Path) -> bool:
    data, _ = _read_json(path)
    return bool(data) and isinstance(data.get("mcpServers"), dict) and NAME in data["mcpServers"]


def connect_cursor() -> Tuple[bool, str]:
    path = cursor_config()
    return _connect_json(path, _server_entry("cursor", "${workspaceFolder}"), "Cursor",
                         f"Cursor: connected for all projects ({path}). Restart Cursor to pick it up.")


def disconnect_cursor() -> Tuple[bool, str]:
    return _disconnect_json(cursor_config(), "Cursor", "Restart Cursor to pick it up.")


def cursor_connected() -> bool:
    return _json_connected(cursor_config())


# ---------- Claude Desktop ----------

def _desktop_entry() -> dict:
    entry = _server_entry("claude-desktop", str(Path.home()))
    entry["env"]["MNEMOLITH_PROJECT"] = DESKTOP_PROJECT
    return entry


def connect_claude_desktop() -> Tuple[bool, str]:
    path = claude_desktop_config()
    return _connect_json(path, _desktop_entry(), "Claude Desktop",
                         f"Claude Desktop: connected ({path}). Quit and reopen Claude Desktop to pick it up. "
                         f"Its memories go in the project \"{DESKTOP_PROJECT}\", and can be tied to any file in "
                         f"your home folder.")


def disconnect_claude_desktop() -> Tuple[bool, str]:
    return _disconnect_json(claude_desktop_config(), "Claude Desktop", "Quit and reopen Claude Desktop to pick it up.")


def claude_desktop_connected() -> bool:
    return _json_connected(claude_desktop_config())


# ---------- Codex ----------

def _codex_block() -> str:
    cmd = server_command()
    q = json.dumps  # TOML basic strings use the same escaping as JSON strings
    return (f"[mcp_servers.{NAME}]\n"
            f"command = {q(cmd[0])}\n"
            f"args = [{', '.join(q(a) for a in cmd[1:])}]\n"
            "env = { " + ", ".join(f"{k} = {q(v)}" for k, v in {"MEMORY_SOURCE": "codex", **settings_env()}.items())
            + " }\n")


def _is_our_header(line: str, names=(NAME, *OLD_NAMES)) -> bool:
    s = line.strip().replace('"', "").replace("'", "")
    return any(s == f"[mcp_servers.{n}]" or s.startswith(f"[mcp_servers.{n}.") for n in names)


def connect_codex() -> Tuple[bool, str]:
    path = codex_config()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    body = _without_our_section(text)  # drop our old section (if any): from its header to the next table header
    new = (body + "\n\n" if body else "") + _codex_block()
    if path.exists():
        shutil.copyfile(path, path.with_name("config.toml.bak"))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("config.toml.mnemolith-tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, path)
    note = add_instructions(path.with_name("AGENTS.md"))  # Codex's instructions for every project
    return True, f"Codex: connected ({path}; {note})."


def _without_our_section(text: str) -> str:
    kept, skipping = [], False
    for line in text.splitlines():
        if line.strip().startswith("["):
            skipping = _is_our_header(line)
        if not skipping:
            kept.append(line)
    return "\n".join(kept).rstrip()


def disconnect_codex() -> Tuple[bool, str]:
    path = codex_config()
    notes = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    if any(_is_our_header(l) for l in text.splitlines()):
        shutil.copyfile(path, path.with_name("config.toml.bak"))
        body = _without_our_section(text)
        tmp = path.with_name("config.toml.mnemolith-tmp")
        tmp.write_text(body + "\n" if body else "", encoding="utf-8")
        os.replace(tmp, path)
        notes.append(f"removed the MCP server from {path}")
    if remove_instructions(path.with_name("AGENTS.md")):
        notes.append(f"removed our section from {path.with_name('AGENTS.md')}")
    return True, "Codex: disconnected" + (f" ({'; '.join(notes)})." if notes else " (it wasn't connected).")


def codex_connected() -> bool:
    try:
        return any(_is_our_header(l, (NAME,)) for l in codex_config().read_text(encoding="utf-8").splitlines())
    except OSError:
        return False


# ---------- any other MCP tool ----------

def generic_instructions() -> str:
    entry = _server_entry("your-tool-name")
    return ("For any other MCP-capable tool, add this server to its MCP config (most use this JSON shape):\n\n"
            + json.dumps({"mcpServers": {NAME: entry}}, indent=2)
            + "\n\nOr, as a single command: " + " ".join(f'"{c}"' if " " in c else c for c in server_command())
            + "\nRun the tool from your project folder, or set MNEMOLITH_REPO to the project's path.")


CONNECTORS = {"claude-code": connect_claude_code, "claude-desktop": connect_claude_desktop,
              "cursor": connect_cursor, "codex": connect_codex}
DISCONNECTORS = {"claude-code": disconnect_claude_code, "claude-desktop": disconnect_claude_desktop,
                 "cursor": disconnect_cursor, "codex": disconnect_codex}
LABELS = {"claude-code": "Claude Code", "claude-desktop": "Claude Desktop", "cursor": "Cursor", "codex": "Codex"}
CHECKS = {"claude-code": claude_code_connected, "claude-desktop": claude_desktop_connected,
          "cursor": cursor_connected, "codex": codex_connected}


def refresh_instructions() -> List[str]:
    """Rewrites our instruction section for the tools that have one (after Mender is turned on or off)."""
    done = []
    for path in (claude_instructions_file(), codex_config().with_name("AGENTS.md")):
        try:
            if path.exists() and INSTR_START in path.read_text(encoding="utf-8"):
                add_instructions(path)
                done.append(str(path))
        except OSError:
            pass
    return done
