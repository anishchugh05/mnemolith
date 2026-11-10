"""The `mnemolith` command."""
import argparse
import os
import sys
from collections import Counter

from . import __version__, config, connect, engine

OK, WARN, FAIL = "  ok  ", "  !!  ", " FAIL "


def _say(mark: str, text: str) -> None:
    lines = text.splitlines() or [""]
    print(f"[{mark}] {lines[0]}")
    for line in lines[1:]:
        print(f"         {line}")


# ---------- commands ----------

def _java_label(java: str) -> str:
    return f"private copy in {java_dir()}" if engine.is_private(java) else java


def java_dir():
    from .javart import JAVA_DIR
    return JAVA_DIR


def _java_for_setup():
    """The computer's own Java if it has a recent enough one; otherwise downloads a private copy."""
    try:
        return engine.find_java()
    except engine.EngineError as missing:
        from . import javart
        reason = str(missing).splitlines()[0]  # "Java isn't installed." or "Found Java 8, which is too old."
        print(f"[ .... ] {reason} Downloading a private copy of Java {javart.FEATURE_VERSION} (Eclipse Temurin, "
              f"from adoptium.net, about 50 MB, only the first time).\n"
              f"         It goes in {javart.JAVA_DIR}, only Mnemolith uses it, and nothing else on your "
              f"computer changes.", flush=True)
        try:
            javart.download()
        except javart.JavaDownloadError as e:
            raise engine.EngineError(str(e))
        return engine.find_java()


def cmd_setup(args) -> int:
    print(f"Setting up Mnemolith {__version__}\n")
    try:
        java, major = _java_for_setup()
        _say(OK, f"Java {major} ({_java_label(java)})")
        _say(OK, engine.start())
    except engine.EngineError as e:
        _say(FAIL, str(e))
        print("\nFix that, then run `mnemolith setup` again."
              + ("" if config.CONTACT_URL in str(e) else f"\nStill stuck? Tell us: {config.CONTACT_URL}"))
        return 1

    if config.FAKE_EMBEDDER:
        _say(OK, "Embedding model: test mode, nothing to download")
    else:
        if any(config.MODELS_DIR.glob("*")):
            print("[ .... ] Loading the embedding model...", flush=True)
        else:
            print(f"[ .... ] Downloading the embedding model from Hugging Face (huggingface.co, about 90 MB, only the "
                  f"first time)\n         into {config.MODELS_DIR}. It's what lets Mnemolith search memories by "
                  f"meaning...", flush=True)
        try:
            from .server import embedder
            next(iter(embedder().embed(["warm up"])))
            _say(OK, f"Embedding model ready ({config.MODEL})")
        except Exception as e:
            detail = (str(e).strip().splitlines() or [type(e).__name__])[0][:160]
            _say(WARN, f"Couldn't download the embedding model from Hugging Face ({detail}).\n"
                       f"If your network blocks huggingface.co (common on work or school networks), try another\n"
                       f"network, then run `mnemolith setup` again. Otherwise it downloads the first time your\n"
                       f"AI saves or searches a memory. Still stuck? Tell us: {config.CONTACT_URL}")

    _setup_mender(args)

    tools = args.tools or connect.detected()
    if not tools:
        _say(WARN, "No supported AI coding tools found (Claude Code, Cursor, Codex).")
        print("\n" + connect.generic_instructions())
    failed = []
    for tool in tools:
        done, message = connect.CONNECTORS[tool]()
        _say(OK if done else FAIL, message)
        if not done:
            failed.append(tool)
    if failed:
        print(f"\nAlmost done: fix the {', '.join(failed)} problem above, then run "
              f"`mnemolith connect {' '.join(failed)}`.")
        return 1

    print("\nDone. Open your AI tool in any git repo and try:\n"
          '  "Remember that our tests need DOCKER_HOST set. It depends on docker-compose.yml."\n'
          "Then, in a new session:\n"
          '  "How do I run the tests?"\n'
          "\nSee and fix what your AI remembers any time with `mnemolith ui`.")
    return 0


MENDER_PITCH = ("Mender (optional): when a code change makes a memory stale, Mender drafts the corrected memory\n"
                "         for you to approve in the dashboard. Simple changes are drafted on your machine by plain rules;\n"
                "         your AI tool drafts the rest. No API key, nothing extra to install, and nothing changes until\n"
                "         you approve it.")


def _setup_mender(args) -> None:
    from . import mender
    choice = getattr(args, "mender", None)
    if choice is None and config.mender_enabled():
        _say(OK, "Mender is on (drafted fixes for stale memories). Turn it off with `mnemolith mender off`.")
        return
    if choice is None:
        print(f"[  ??  ] {MENDER_PITCH}")
        if not sys.stdin.isatty():
            _say(OK, "Mender left off. Turn it on any time with `mnemolith mender on`.")
            return
        try:
            answer = input("         Turn on Mender? [Y/n] ").strip().lower()
        except EOFError:
            answer = "n"
        choice = answer in ("", "y", "yes")
    mender.set_enabled(choice)
    _say(OK, "Mender is on: stale memories get a drafted fix to approve in `mnemolith ui`." if choice
         else "Mender left off. Turn it on any time with `mnemolith mender on`.")


def cmd_mender(args) -> int:
    from . import mender
    if args.action == "revert":
        from .server import EngineDown, git_toplevel, project_name, store_down
        if not args.id:
            print("Which fix? Pass the id of the memory Mender saved, e.g. `mnemolith mender revert m7` "
                  "(see `mnemolith list`).")
            return 1
        try:
            print(mender.revert(project_name(), args.id, git_toplevel())["message"])
        except mender.Refused as e:
            print(e)
            return 1
        except EngineDown as e:
            print(store_down(e))
            return 1
        return 0
    if args.action == "status":
        on = config.mender_enabled()
        print(f"Mender is {'on' if on else 'off'}." + (
            " Stale memories get a drafted fix: approve them in `mnemolith ui`." if on else
            " Turn it on with `mnemolith mender on`."))
        if os.environ.get("MNEMOLITH_MENDER"):
            print("(MNEMOLITH_MENDER is set in this terminal, so it decides here.)")
        return 0
    on = args.action == "on"
    mender.set_enabled(on)
    refreshed = connect.refresh_instructions()
    print(f"Mender is {'on' if on else 'off'}." + (
        " When code changes make a memory stale, you'll find a drafted fix next to it in `mnemolith ui`.\n"
        "Your AI tools offer to draft fixes too, from their next session (restart any that are open)." if on else
        " Stale memories still show up in `mnemolith ui`; you decide them yourself."))
    if refreshed:
        print("Updated the Mnemolith instructions in " + ", ".join(refreshed) + ".")
    return 0


def cmd_mend(args) -> int:
    """Drafts fixes for this project's stale memories now and lists them."""
    from . import mender
    from .server import EngineDown, check_staleness, git_toplevel, project_name, run, store_down
    if not config.mender_enabled():
        print("Mender is off. Turn it on with `mnemolith mender on`.")
        return 1
    root = git_toplevel()
    if root is None:
        print("This folder isn't in a git repo, so Mender can't compare file versions here.")
        return 1
    try:
        check_staleness()
        project = project_name()
        drafts = mender.refresh(project, root)
        stale = [r for r in run("MEM.LIST", project, "STATUS", "stale")
                 if mender.needs_decision({"id": r[0], "status": r[1]})]
    except EngineDown as e:
        print(store_down(e))
        return 1
    if not stale:
        print(f"No stale memories in {project}. Nothing to fix.")
        return 0
    ready = 0
    for mid, _status, _files, text in stale:
        d = drafts.get(mid)
        if d and not d.get("dismissed"):
            ready += 1
            fix = "still true" if d["kind"] == "confirm" else f"\"{d['text']}\""
            print(f"{mid:>5}  \"{text}\"\n       -> {fix}  ({mender.by_label(d.get('by', ''))})")
        elif d:
            print(f"{mid:>5}  \"{text}\"\n       -> you dismissed the draft: decide it in `mnemolith ui`")
        else:
            print(f"{mid:>5}  \"{text}\"\n       -> no draft yet: ask your AI tool to fix the stale memories")
    print(f"\n{ready} of {len(stale)} drafted. Approve them in `mnemolith ui`; nothing changes until you do.")
    return 0


def cmd_start(args) -> int:
    try:
        print(engine.start())
        return 0
    except engine.EngineError as e:
        print(e)
        return 1


def cmd_stop(args) -> int:
    print(engine.stop())
    return 0


def cmd_status(args) -> int:
    state = engine.probe()
    if state == "ours":
        pid = engine.running_pid()
        print(f"Engine: running on {config.HOST}:{config.PORT}" + (f" (pid {pid})" if pid else ""))
    elif state == "other":
        print(f"Engine: not running. {engine.port_taken_message()}")
        return 1
    else:
        print("Engine: not running (it starts automatically when your AI tool needs it, or run `mnemolith start`)")
    print(f"Data:   {config.DATA_DIR}")
    if state == "ours":
        from .server import project_name, run
        project = project_name()
        counts = Counter(status for _, status, _, _ in run("MEM.LIST", project, "STATUS", "any"))
        summary = ", ".join(f"{counts[s]} {s}" for s in ("active", "stale", "superseded") if counts[s])
        print(f"This project ({project}): {summary or 'no memories yet'}")
    from pathlib import Path
    from . import repos, team
    top = repos.git_top(Path.cwd())
    if top and team.exists(top):
        n = len(team.current(top))
        print(f"Team memory: {n} shared {'memory' if n == 1 else 'memories'}" + (
            "" if team.joined(top) else ", not in use here (run `mnemolith team init` to use them)"))
    connected = [t for t in connect.TOOLS if connect.CHECKS[t]()]
    print("Connected tools: " + (", ".join(connected) if connected else "none (run `mnemolith connect`)"))
    if config.mender_enabled():
        waiting = 0
        if state == "ours":
            from . import mender
            from .server import check_staleness, git_toplevel
            try:  # check the files first, so this counts drafts for what's stale right now
                check_staleness()
                mender.refresh(project, git_toplevel())
            except Exception:
                pass
            waiting = mender.waiting(project)
        print("Mender: on" + (f", {waiting} {'fix' if waiting == 1 else 'fixes'} waiting for your approval "
                              f"(`mnemolith ui`)" if waiting else " (drafted fixes for stale memories)"))
    else:
        print("Mender: off (turn on with `mnemolith mender on`)")
    return 0


def cmd_connect(args) -> int:
    if args.print:
        print(connect.generic_instructions())
        return 0
    tools = args.tools or connect.detected()
    if not tools:
        print("No supported AI coding tools found (Claude Code, Cursor, Codex).\n")
        print(connect.generic_instructions())
        return 1
    failed = False
    for tool in tools:
        done, message = connect.CONNECTORS[tool]()
        _say(OK if done else FAIL, message)
        failed |= not done
    return 1 if failed else 0


def cmd_doctor(args) -> int:
    problems = 0
    _say(OK, f"Mnemolith {__version__}, Python {sys.version.split()[0]} ({sys.executable})")
    try:
        java, major = engine.find_java()
        _say(OK, f"Java {major} ({_java_label(java)})")
    except engine.EngineError as e:
        _say(FAIL, str(e))
        problems += 1
    if engine.JAR.is_file():
        _say(OK, f"Engine file present ({engine.JAR.name})")
    else:
        _say(FAIL, f"Engine file missing ({engine.JAR}). Reinstall Mnemolith.")
        problems += 1
    state = engine.probe()
    if state == "ours":
        _say(OK, f"Engine answering on {config.HOST}:{config.PORT}")
    elif state == "other":
        _say(FAIL, engine.port_taken_message())
        problems += 1
    else:
        _say(WARN, f"Engine not running. It starts automatically when needed; `mnemolith start` starts it now.\n"
                   f"If it won't start, see {config.LOG_FILE}")
    if config.FAKE_EMBEDDER:
        _say(WARN, "Test embedder is on (MNEMOLITH_EMBEDDER=fake): search quality is poor. Unset it for real use.")
    elif any(config.MODELS_DIR.glob("*")):
        _say(OK, f"Embedding model downloaded ({config.MODELS_DIR})")
    else:
        _say(WARN, "Embedding model not downloaded yet; it downloads on first use (about 90 MB).")
    for tool in connect.TOOLS:
        if connect.CHECKS[tool]():
            _say(OK, f"Connected to {tool}")
        elif tool in connect.detected():
            _say(WARN, f"{tool} is installed but not connected. Run `mnemolith connect {tool}`.")
    _say(OK, "Mender is on: stale memories get drafted fixes to approve in `mnemolith ui`" if config.mender_enabled()
         else "Mender is off (optional: `mnemolith mender on` drafts fixes for stale memories)")
    from .server import git_toplevel, project_name
    top = git_toplevel()
    _say(OK if top else WARN, f"This folder's project: {project_name()}"
         + ("" if top else " (not a git repo, so memories can't be linked to file versions here)"))
    print("\nEverything looks good." if problems == 0 else
          f"\n{problems} problem(s) to fix above. Still stuck? Tell us: {config.CONTACT_URL}")
    return 1 if problems else 0


def cmd_list(args) -> int:
    from .server import EngineDown, project_name, run, store_down
    try:
        rows = run("MEM.LIST", project_name(), "STATUS", args.status)
    except EngineDown as e:
        print(store_down(e))
        return 1
    if not rows:
        print(f"No {'' if args.status == 'any' else args.status + ' '}memories in {project_name()} yet.")
    for mid, status, files, text in rows:
        linked = ", ".join(f.rsplit("@", 1)[0] for f in files.split(",") if f)
        print(f"{mid:>5}  {status:<10}  {text}" + (f"   [{linked}]" if linked else ""))
    return 0


def _tool_output(name: str, *tool_args) -> int:
    from . import server
    print(getattr(server, name)(*tool_args))
    return 0


def cmd_add(args) -> int:
    from . import repos
    from .server import EngineDown, store_down
    try:
        engine.ensure_running()
        added = repos.add(args.path)
    except repos.RepoError as e:
        print(e)
        return 1
    except (engine.EngineError, EngineDown) as e:
        print(store_down(e))
        return 1
    print(f"Added {added['name']} ({added['path']})" + (f", github.com/{added['github']}" if added["github"] else "") + ".")
    return 0


def cmd_remove(args) -> int:
    """Removes a project, like Remove project in the dashboard: retires its memories (kept, with their history)
    and hides it until it's used again or added back."""
    from pathlib import Path
    from . import repos, ui
    from .server import EngineDown, store_down
    name = args.name or config.project_override()
    if not name:  # this folder's project, named the way the AI tools name it
        here = Path.cwd()
        name = (repos.git_top(here) or here.resolve()).name
    try:
        engine.ensure_running()
        projects = {p["name"]: p for p in ui.api_projects(None, None)["projects"]}
        if name not in projects:
            print(f"There's no project called {name} in Mnemolith."
                  + (f" Projects: {', '.join(sorted(projects))}." if projects else ""))
            return 1
        counts = projects[name]["counts"]
        live = counts["active"] + counts["stale"]
        print(f"Removing {name}." + (f" Its {live} {'memory' if live == 1 else 'memories'} will be retired, so your AI "
                                     f"tools stop using {'it' if live == 1 else 'them'}." if live else "")
              + " Nothing is deleted: export first with `mnemolith export` if you want a copy.")
        if not args.yes and not _ask(f"Remove {name}?"):
            print("Nothing changed." + ("" if sys.stdin.isatty() else " Add --yes to remove without asking."))
            return 1
        result = ui.api_remove(None, {"name": name})
    except (engine.EngineError, EngineDown) as e:
        print(store_down(e))
        return 1
    retired = result.get("retired", 0)
    print(f"Removed {name}" + (f" and retired {retired} {'memory' if retired == 1 else 'memories'}." if retired else ".")
          + " Using it with an AI tool again, or `mnemolith add`, brings it back.")
    return 0


def _ask(question: str) -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def cmd_import(args) -> int:
    from pathlib import Path
    from . import portability, repos, server
    folder = Path(args.path).expanduser()
    if not folder.is_dir():
        print(f"{args.path} isn't a folder.")
        return 1
    root = repos.git_top(folder) or folder.resolve()
    project = root.name
    try:
        engine.ensure_running()
        found = portability.scan(project, root)
    except (engine.EngineError, server.EngineDown) as e:
        print(server.store_down(e))
        return 1
    if not found["files"]:
        print(f"No notes files in {root}.\nMnemolith looks for: " + ", ".join(portability.NOTE_FILES)
              + ", and .cursor/rules/.")
        return 0
    notes = found["notes"]
    new = [n for n in notes if n["selected"]]
    left_out = [n for n in notes if not n["known"] and not n["selected"]]
    known = [n for n in notes if n["known"]]
    print(f"Found {len(notes)} notes in " + ", ".join(f["path"] for f in found["files"]) + f" ({project})\n")
    for i, n in enumerate(new, 1):
        print(f"  {i:>3}. {n['text']}" + (f"\n       linked to {', '.join(n['files'])}" if n["files"] else ""))
    if left_out:
        print(f"\nLeft out ({len(left_out)}):")
        for n in left_out:
            if n["similar"]:
                what = n["similar"]["id"] or "another note here"
                why = f"very similar to {what}: \"{n['similar']['text']}\""
            elif n["missing"]:
                why = f"mentions {', '.join(n['missing'])}, which doesn't exist (probably out of date)"
            else:
                why = "doesn't look like a fact about the project"
            print(f"     - {n['text']}\n       {why}")
    if known:
        print(f"\nAlready remembered: {len(known)}")
    if not new:
        print("\nNothing new to import.")
        return 0
    if left_out:
        print("\nTo pick notes one by one, including the left-out ones, use Import notes in `mnemolith ui`.")
    if not args.yes and not _ask(f"\nImport {len(new)} {'memory' if len(new) == 1 else 'memories'} into {project}?"):
        print("Nothing imported." + ("" if sys.stdin.isatty() else " Pass --yes to import without asking."))
        return 0
    try:
        result = portability.apply(project, root)
        repos.add(str(root))  # so the dashboard can check the imported memories' files
    except repos.RepoError:
        pass
    except server.EngineDown as e:
        print(server.store_down(e))
        return 1
    saved = len(result["saved"])
    print(f"Imported {saved} {'memory' if saved == 1 else 'memories'} into {project}."
          + (f" {result['duplicates']} matched memories you already had." if result["duplicates"] else "")
          + (f"\n{result['conflicts']} may contradict an existing memory: decide in `mnemolith ui`."
             if result["conflicts"] else ""))
    return 0


def cmd_export(args) -> int:
    import json
    from pathlib import Path
    from . import portability, server
    try:
        engine.ensure_running()
        data = portability.export(args.project or None)
    except (engine.EngineError, server.EngineDown) as e:
        print(server.store_down(e))
        return 1
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if args.output == "-":
        sys.stdout.write(text)
        return 0
    out = Path(args.output or portability.export_filename(args.project[0] if len(args.project or []) == 1 else None))
    out = out.expanduser()
    out.write_text(text, encoding="utf-8")
    n = sum(len(p["memories"]) for p in data["projects"])
    print(f"Exported {n} {'memory' if n == 1 else 'memories'} from {len(data['projects'])} "
          f"{'project' if len(data['projects']) == 1 else 'projects'} to {out.resolve()}")
    return 0


def _uninstall_command() -> str:
    exe = sys.executable.replace("\\", "/")
    if "/uv/tools/" in exe or "/uv/tool/" in exe:
        return "uv tool uninstall mnemolith"
    if "/pipx/" in exe:
        return "pipx uninstall mnemolith"
    return f"{sys.executable} -m pip uninstall mnemolith"


def cmd_uninstall(args) -> int:
    import shutil
    print("Uninstalling Mnemolith\n")
    failed = False
    for tool in connect.TOOLS:
        done, message = connect.DISCONNECTORS[tool]()
        _say(OK if done else FAIL, message)
        failed |= not done
    print("         Restart any AI tools that are open, so they stop using Mnemolith.")
    count = None
    if engine.probe() == "ours":
        try:
            from .ui import all_memories
            count = len(all_memories())
        except Exception:
            pass
    stopped = engine.stop()
    still_running = engine.probe() == "ours"
    _say(FAIL if still_running else OK, stopped)
    failed |= still_running

    has_data = config.HOME.exists()
    if still_running and has_data:  # it would rewrite them anyway
        print(f"\nYour memories are kept in {config.HOME}: stop the engine first to delete them.")
        print(f"\nLast step, remove the program itself:\n  {_uninstall_command()}")
        return 1
    delete = args.delete_data
    if has_data and not delete:
        what = f"Your {count} {'memory is' if count == 1 else 'memories are'}" if count else "Your memories are"
        print(f"\n{what} in {config.HOME}.")
        delete = _ask("Delete them too? This can't be undone (keep a copy first with `mnemolith export`).")
    if has_data and delete:
        shutil.rmtree(config.HOME, ignore_errors=True)
        _say(OK, f"Deleted {config.HOME}")
    elif has_data:
        from . import javart
        if javart.remove():
            _say(OK, f"Removed Mnemolith's private copy of Java ({javart.JAVA_DIR})")
        _say(OK, f"Kept your memories in {config.HOME}. Reinstall any time and they're back.")
    print(f"\nLast step, remove the program itself:\n  {_uninstall_command()}")
    return 1 if failed else 0


def _share_ok(args) -> bool:
    """The "everyone on this repo will see this" question, unless this person turned it off."""
    from . import team
    if args.yes or not team.warning_on():
        return True
    print(team.VISIBILITY)
    if not sys.stdin.isatty():
        print("Nothing shared. Add --yes to share without asking.")
        return False
    try:
        answer = input("Share? [y/N/a = yes, and stop asking] ").strip().lower()
    except EOFError:
        answer = ""
    if answer in ("a", "always"):
        team.set_warning(False)
        print("Okay, sharing won't ask again. Turn the question back on in `mnemolith ui`, Settings.")
        return True
    if answer not in ("y", "yes"):
        print("Nothing shared.")
        return False
    return True


def _team_root():
    from pathlib import Path
    from . import repos
    root = repos.git_top(Path.cwd())
    if root is None:
        print("This folder isn't in a git repo. Team memory lives in a repo, so your team gets it with the code.")
    return root


def cmd_promote(args) -> int:
    """Shares one of your memories with the team."""
    from . import team
    from .server import EngineDown, project_name, run, store_down
    root = _team_root()
    if root is None:
        return 1
    if not team.exists(root):
        print(f"{root.name} has no team memory yet. Start one with `mnemolith team init`.")
        return 1
    try:
        got = run("MEM.GET", args.id)
        if got is None:
            print(f"No memory with id {args.id}. See yours with `mnemolith list`.")
            return 1
        print(f"{args.id}: \"{dict(zip(got[0::2], got[1::2]))['text']}\"")
        if not _share_ok(args):
            return 1
        try:
            mem = team.promote(root, project_name(), args.id)
        except team.SecretFound as e:
            print(e)
            if not e.overridable:
                return 1
            if not _ask("If you're sure none of that is a secret, share it anyway?"):
                print("Nothing shared." if sys.stdin.isatty() else
                      "Nothing shared. Run this in a terminal to look at it and decide.")
                return 1
            mem = team.promote(root, project_name(), args.id, allow_soft=True)
    except team.TeamError as e:
        print(e)
        return 1
    except EngineDown as e:
        print(store_down(e))
        return 1
    print(f"Shared as {mem.name}: {mem.path.relative_to(root).as_posix()}\n"
          f"It moved out of your own memories, and {team.INDEX} was updated. Commit both when you're ready:\n"
          f"your team gets it when they pull.")
    return 0


def cmd_team(args) -> int:
    """Team memory: memories shared with everyone on a repo, as files in .mnemolith/team/."""
    from . import team
    root = _team_root()
    if root is None:
        return 1
    where = f"{team.FOLDER}/"
    if args.action == "init":
        result = team.init(root)
        n = result["count"]
        if result["created"]:
            print(f"Started a team memory for {root.name} in {where}\n"
                  f"Everyone who can see this repo will be able to see what goes in it (anyone on the internet, if\n"
                  f"the repo is public), so passwords and keys are refused.")
        else:
            print(f"You're now using {root.name}'s team memory ({n} {'memory' if n == 1 else 'memories'} in {where}).\n"
                  f"Your AI tools get them in recall from their next session, next to your own memories.\n"
                  f"This only changed your own setup: nothing in the repo was touched. `mnemolith team remove` undoes it.")
        if result["ignored"]:
            _say(WARN, f"{where} is ignored by this repo's .gitignore, so it won't be shared until that's changed.")
        if n == 0:
            _offer_import(root, args)
            print(f"\nNext:\n"
                  f"  - Share a memory you already have: `mnemolith promote <id>` (ids are in `mnemolith list`).\n"
                  f"  - Or let your AI tool draft starter memories: run `mnemolith team ai on`, restart the tool, and ask\n"
                  f"    it to \"draft starter team memories for this repo\".\n"
                  f"  - Commit {where} and {team.INDEX} when you're ready (Mnemolith never commits for you). Teammates\n"
                  f"    then run `mnemolith team init` in their copy.")
        return 0
    if args.action == "remove":
        if team.leave(root):
            print(f"Stopped using {root.name}'s team memory on this computer. The files in {where} weren't touched, "
                  f"your teammates aren't affected, and your own memories are kept. `mnemolith team init` brings it back.")
        else:
            print(f"You weren't using a team memory in {root.name}. Nothing changed.")
        return 0
    if not team.exists(root):
        print(f"{root.name} has no team memory yet. `mnemolith team init` starts one.")
        return 0 if args.action == "status" else 1
    if args.action == "confirm":
        if not args.name:
            print("Which memory? Pass its name as `mnemolith team` shows it, e.g. `mnemolith team confirm use-pnpm`.")
            return 1
        try:
            mem = team.confirm(root, args.name)
        except team.TeamError as e:
            print(e)
            return 1
        print(f"Confirmed {mem.name}: {mem.path.relative_to(root).as_posix()} now records the current versions of "
              f"its files. Commit that change so it's confirmed for your team too.")
        return 0
    if args.action == "retire":
        if not args.name:
            print("Which memory? Pass its name as `mnemolith team` shows it, e.g. `mnemolith team retire use-pnpm`.")
            return 1
        try:
            mem = team.retire(root, args.name)
        except team.TeamError as e:
            print(e)
            return 1
        print(f"Retired {mem.name}: its file now says `status: retired`, so AI tools stop using it and its history "
              f"stays in git. Commit that change so it's retired for your team too.")
        return 0
    if args.action == "sync":
        print(f"Updated {team.INDEX}. Commit it with your other changes." if team.write_index(root)
              else f"{team.INDEX} is already up to date.")
        return 0
    if args.action == "import":
        return 0 if _offer_import(root, args, asked_for=True) else 1
    if args.action == "ai":
        if args.name not in ("on", "off"):
            print(f"AI tools {'may' if team.ai_allowed(root) else 'may not'} share memories with {root.name}'s team. "
                  f"Change it with `mnemolith team ai on` or `mnemolith team ai off`.")
            return 0
        team.allow_ai(root, args.name == "on")
        print(f"Your AI tools may now share memories with {root.name}'s team when you ask them to. They still can't\n"
              f"commit, and passwords and keys are still refused. Restart any AI tool that's open.\n"
              f"Your AI tool may also ask you to approve the first share itself (Claude Code does, in auto mode):\n"
              f"that's the tool's own check, separate from this one."
              if args.name == "on" else
              f"Your AI tools can no longer share memories with {root.name}'s team. You still can, with `mnemolith promote`.")
        return 0
    # status
    mems = team.load(root)
    live = team.current(root)
    print(f"Team memory for {root.name}: {len(live)} {'memory' if len(live) == 1 else 'memories'} in {where}memories/"
          + (" (you're using it)" if team.joined(root) else "\nYou aren't using it yet: run `mnemolith team init`."))
    width = max((len(m.name) for m in mems), default=0)
    def short(text: str, limit: int = 100) -> str:
        return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"

    for m in mems:
        text = short(m.one_line)
        if team.conflicted(m):
            file = m.path.relative_to(root).as_posix()
            both = team.sides(m)
            if both:
                text = "(two versions)"
                note = (f"\n      !! two people changed this and git couldn't merge them:\n"
                        f"         yours:  {short(both[0], 160)}\n"
                        f"         theirs: {short(both[1], 160)}\n"
                        f"         Open {file}, keep the right wording, remove the <<<<<<< ======= >>>>>>> lines,\n"
                        f"         then `git add` it and commit. Your AI tools don't see this memory until then.")
            else:
                text = "(in conflict)"
                note = (f"\n      !! two people changed this and git couldn't merge them. Open {file}, settle it, and\n"
                        f"         remove the <<<<<<< ======= >>>>>>> lines. Your AI tools don't see this memory until then.")
        elif not m.readable:
            note = "   !! written by a newer Mnemolith: update to read it"
        elif m.status != "active":
            note = f"   ({m.status})"
        else:
            what = team.changed(m, root)
            note = (f"\n      !! possibly outdated: {', '.join(what)}. Check it, then `mnemolith team confirm {m.name}` "
                    f"if it's still true, or `mnemolith team retire {m.name}` if it isn't") if what else ""
        print(f"  {m.name:<{width}}  {text}{note}")
    if team.index_outdated(root):
        print(f"\n{team.INDEX} doesn't match these files any more. `mnemolith team sync` rebuilds it.")
    return 0


def _offer_import(root, args, asked_for: bool = False) -> bool:
    """Turns the notes a repo already has (CLAUDE.md, AGENTS.md, Cursor rules) into team memories, after asking."""
    from . import team
    found = team.notes_to_import(root)
    notes = found["notes"]
    if not notes:
        if asked_for:
            print("No notes to import: CLAUDE.md, AGENTS.md and Cursor rules have nothing the team doesn't already have.")
        return False
    print(f"\nThis repo's notes files have {len(notes)} {'note' if len(notes) == 1 else 'notes'} that could become "
          f"team memories:")
    for n in notes:
        print(f"  - {n['text'] if len(n['text']) <= 110 else n['text'][:109].rstrip() + '…'}"
              + (f"   [{', '.join(n['files'])}]" if n["files"] else ""))
    if found["more"]:
        print(f"  ({found['more']} more were left out: {team.MAX_IMPORT} is plenty to review at once. Run "
              f"`mnemolith team import` again later for the rest.)")
    if found["skipped_secrets"]:
        print(f"  ({found['skipped_secrets']} left out because they look like they contain a password or key.)")
    one = len(notes) == 1
    if not args.yes and not _ask(f"Add {'it as a team memory' if one else 'them as team memories'}? Everyone with "
                                 f"access to this repo will see {'it' if one else 'them'}."):
        print("Nothing added." + ("" if sys.stdin.isatty() else " Add --yes to add without asking.")
              + ("" if asked_for else " `mnemolith team import` does this later."))
        return False
    made = team.import_notes(root)
    print(f"Added {len(made)} team {'memory' if len(made) == 1 else 'memories'} to {team.FOLDER}/memories/. "
          f"Review {'it' if len(made) == 1 else 'them'} like any other change before you commit.")
    return True


def cmd_serve(args) -> int:
    from .server import serve
    serve()
    return 0


def main(argv=None) -> None:
    try:
        sys.stdout.reconfigure(errors="replace")  # never crash printing on a limited console
    except (AttributeError, ValueError):
        pass
    from . import legacy
    moved = legacy.migrate_home()  # first run after the rename from Aether Cache
    if moved:
        print(moved + "\n", file=sys.stderr)  # stderr: when an AI tool runs `serve`, stdout carries the MCP protocol
    parser = argparse.ArgumentParser(
        prog="mnemolith",
        description="Shared memory for AI coding tools that stays true to your code.")
    parser.add_argument("--version", action="version", version=f"mnemolith {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p = sub.add_parser("setup", help="one-time setup: start the engine, download the model, connect your AI tools")
    p.add_argument("tools", nargs="*", metavar="tool",
                   help="which tools to connect (default: every supported tool found)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--mender", dest="mender", action="store_true", default=None,
                   help="turn on Mender (drafted fixes for stale memories) without asking")
    g.add_argument("--no-mender", dest="mender", action="store_false", help="leave Mender off without asking")
    p.set_defaults(func=cmd_setup)
    p = sub.add_parser("connect", help="connect AI tools: claude-code, claude-desktop, cursor, codex")
    p.add_argument("tools", nargs="*", metavar="tool",
                   help="which tools to connect (default: every supported tool found)")
    p.add_argument("--print", action="store_true", help="print config for any other MCP tool instead")
    p.set_defaults(func=cmd_connect)
    sub.add_parser("start", help="start the engine in the background").set_defaults(func=cmd_start)
    sub.add_parser("stop", help="stop the engine").set_defaults(func=cmd_stop)
    sub.add_parser("status", help="is it running, and what's in this project's memory").set_defaults(func=cmd_status)
    sub.add_parser("doctor", help="check everything and say how to fix problems").set_defaults(func=cmd_doctor)
    p = sub.add_parser("list", help="list this project's memories")
    p.add_argument("--status", default="active", choices=["active", "stale", "superseded", "any"])
    p.set_defaults(func=cmd_list)
    sub.add_parser("recall", help="search this project's memories").add_argument("query")
    sub.choices["recall"].set_defaults(func=lambda a: _tool_output("recall", a.query))
    sub.add_parser("explain", help="where a memory came from and its history").add_argument("id")
    sub.choices["explain"].set_defaults(func=lambda a: _tool_output("explain_memory", a.id))
    sub.add_parser("sync", help="write current memories into AGENTS.md / CLAUDE.md").set_defaults(
        func=lambda a: _tool_output("sync_to_files"))
    p = sub.add_parser("mender", help="turn Mender (drafted fixes for stale memories) on or off, or revert a fix")
    p.add_argument("action", nargs="?", default="status", choices=["on", "off", "status", "revert"])
    p.add_argument("id", nargs="?", help="with revert: the memory a fix saved (e.g. m7)")
    p.set_defaults(func=cmd_mender)
    sub.add_parser("mend", help="draft fixes for this project's stale memories now (Mender)").set_defaults(func=cmd_mend)
    sub.add_parser("check", help="retire memories whose linked files changed").set_defaults(
        func=lambda a: _tool_output("check_stale"))
    p = sub.add_parser("add", help="add a repo so the dashboard can check its files (default: this folder)")
    p.add_argument("path", nargs="?", default=".")
    p.set_defaults(func=cmd_add)
    p = sub.add_parser("remove", help="remove a project, like Remove project in the dashboard (default: this folder's)")
    p.add_argument("name", nargs="?", help="the project's name, as the dashboard shows it")
    p.add_argument("--yes", "-y", action="store_true", help="don't ask for confirmation")
    p.set_defaults(func=cmd_remove)
    p = sub.add_parser("import", help="turn notes in CLAUDE.md, AGENTS.md, and Cursor rules into memories")
    p.add_argument("path", nargs="?", default=".", help="the repo to import from (default: this folder)")
    p.add_argument("--yes", "-y", action="store_true", help="import without asking")
    p.set_defaults(func=cmd_import)
    p = sub.add_parser("export", help="save memories, with their history, to a JSON file")
    p.add_argument("--project", "-p", action="append", metavar="NAME", help="only this project (repeatable; default: all)")
    p.add_argument("--output", "-o", metavar="FILE", help="where to write it (default: mnemolith-<date>.json here; - for stdout)")
    p.set_defaults(func=cmd_export)
    p = sub.add_parser("team", help="team memory: memories shared with everyone on a repo, as files in the repo")
    p.add_argument("action", nargs="?", default="status",
                   choices=["init", "status", "confirm", "retire", "sync", "import", "ai", "remove"],
                   help="init: start one here, or use the one that's here. confirm/retire <name>: a memory is still "
                        "true, or no longer is. sync: rebuild mnemolith.md. import: add notes from CLAUDE.md and "
                        "AGENTS.md. ai on|off: let your AI tools share when you ask. remove: stop using it here")
    p.add_argument("name", nargs="?", help="with confirm or retire: the memory's name. With ai: on or off")
    p.add_argument("--yes", "-y", action="store_true", help="don't ask for confirmation")
    p.set_defaults(func=cmd_team)
    p = sub.add_parser("promote", help="share one of your memories with your team (it moves to the repo's team memory)")
    p.add_argument("id", help="the memory's id, e.g. m42 (see `mnemolith list`)")
    p.add_argument("--yes", "-y", action="store_true", help="don't ask before sharing")
    p.set_defaults(func=cmd_promote)
    p = sub.add_parser("uninstall", help="disconnect your AI tools, stop the engine, and optionally delete memories")
    p.add_argument("--delete-data", action="store_true", help="also delete all memories, without asking")
    p.set_defaults(func=cmd_uninstall)
    p = sub.add_parser("ui", help="open the dashboard: see and fix what your AI tools remember")
    p.add_argument("--port", type=int, help="port for the dashboard (default 7381)")
    p.add_argument("--no-open", action="store_true", help="don't open a browser, just print the link")
    p.set_defaults(func=lambda a: __import__("mnemolith.ui", fromlist=["serve"]).serve(a.port, not a.no_open))
    sub.add_parser("serve", help="run the MCP server (your AI tool runs this for you)").set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    unknown = [t for t in getattr(args, "tools", None) or [] if t not in connect.TOOLS]
    if unknown:
        parser.error(f"unknown tool {unknown[0]!r}; choose from {', '.join(connect.TOOLS)} "
                     f"(or `mnemolith connect --print` for any other MCP tool)")
    if not getattr(args, "func", None):
        parser.print_help()
        print("\nNew here? Run `mnemolith setup`.")
        sys.exit(0)
    try:
        sys.exit(args.func(args))
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:  # output piped into something like `head` that stopped reading
        sys.stderr.close()
        sys.exit(0)
