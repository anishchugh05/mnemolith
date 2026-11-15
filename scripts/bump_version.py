"""Moves every version number to a new release, so the release workflow's check passes the first time.

    python scripts/bump_version.py 0.4.0            # the package, server.json and the website
    python scripts/bump_version.py 0.4.0 --plugin   # the Claude Code plugin, once 0.4.0 is on PyPI
    python scripts/bump_version.py 0.4.0 --eval     # the version the evaluation installs, before an eval run

It only rewrites version numbers, prints each file it changed, and changes nothing else. Review with `git diff`.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def current() -> str:
    return re.search(r'^__version__ = "(.+)"', (ROOT / "mnemolith/__init__.py").read_text(), re.M).group(1)


def rewrite(path: Path, pairs) -> bool:
    text = path.read_text(encoding="utf-8")
    new = text
    for pattern, replacement in pairs:
        new = re.sub(pattern, replacement, new, flags=re.M)
    if new != text:
        path.write_text(new, encoding="utf-8")
        print(f"  {path.relative_to(ROOT).as_posix()}")
    return new != text


def package(old: str, new: str) -> None:
    o = re.escape(old)
    rewrite(ROOT / "pyproject.toml", [(rf'^version = "{o}"', f'version = "{new}"')])
    rewrite(ROOT / "mnemolith/__init__.py", [(rf'^__version__ = "{o}"', f'__version__ = "{new}"')])
    server = ROOT / "server.json"
    data = json.loads(server.read_text(encoding="utf-8"))
    data["version"] = new
    for p in data.get("packages", []):
        p["version"] = new
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if text != server.read_text(encoding="utf-8"):
        server.write_text(text, encoding="utf-8")
        print("  server.json")
    rewrite(ROOT / "README.md", [(rf"git tag v{o} && git push --tags", f"git tag v{new} && git push --tags")])
    for page in sorted((ROOT / "docs").glob("*.html")):
        rewrite(page, [
            (rf"<span>Mnemolith v{o}</span>", f"<span>Mnemolith v{new}</span>"),  # every footer
            (rf"Open source, v{o}\.", f"Open source, v{new}."),  # home page
            (rf"^(\s*)v{o}, available on", rf"\g<1>v{new}, available on"),  # install page
            (rf"A working v{o}\.", f"A working v{new}."),  # about page
            (rf"This is v{o}\.", f"This is v{new}."),
        ])


def plugin(old: str, new: str) -> None:
    o = re.escape(old)
    rewrite(ROOT / "plugin/.claude-plugin/plugin.json", [(rf'"version": "{o}"', f'"version": "{new}"'),
                                                         (rf'"mnemolith=={o}"', f'"mnemolith=={new}"')])
    rewrite(ROOT / "plugin/pyproject.toml", [(rf'^version = "{o}"', f'version = "{new}"'),
                                             (rf'"mnemolith=={o}"', f'"mnemolith=={new}"')])
    rewrite(ROOT / "plugin/skills/dashboard/SKILL.md", [(rf"mnemolith=={o}", f"mnemolith=={new}")])
    print("Now run `uv lock` in plugin/ (it needs the release on PyPI), then commit.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("new", help="the new version, like 0.4.0")
    ap.add_argument("--plugin", action="store_true", help="bump the Claude Code plugin instead (after the release)")
    ap.add_argument("--eval", action="store_true", help="bump the version the evaluation installs instead")
    args = ap.parse_args()
    if not VERSION.match(args.new):
        print(f"{args.new} isn't a version like 0.4.0.")
        return 1
    if args.plugin:
        old = re.search(r'"mnemolith==(.+?)"', (ROOT / "plugin/pyproject.toml").read_text()).group(1)
        print(f"Plugin {old} -> {args.new}:")
        plugin(old, args.new)
        return 0
    if args.eval:
        path = ROOT / "eval/run.py"
        old = re.search(r'^MNEMOLITH = "(.+?)"', path.read_text(), re.M).group(1)
        print(f"Evaluation {old} -> {args.new}:")
        rewrite(path, [(rf'^MNEMOLITH = "{re.escape(old)}"', f'MNEMOLITH = "{args.new}"')])
        return 0
    old = current()
    if old == args.new:
        print(f"Already {args.new}.")
        return 0
    print(f"{old} -> {args.new}:")
    package(old, args.new)
    left = [p.relative_to(ROOT).as_posix() for p in [ROOT / "pyproject.toml", ROOT / "mnemolith/__init__.py",
                                                     ROOT / "server.json", *sorted((ROOT / "docs").glob("*.html"))]
            if f"v{old}<" in p.read_text(encoding="utf-8") or f'"{old}"' in p.read_text(encoding="utf-8")]
    if left:
        print(f"Still says {old}, have a look: {', '.join(left)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
