import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "schema" / "products.json"
TARGET = ROOT / "shop" / "generated" / "schema.py"


def render():
    raw = SOURCE.read_bytes()
    fields = json.loads(raw)["fields"]
    lines = [
        "# Generated from schema/products.json by tools/gen_schema.py.",
        f"# source-sha256: {hashlib.sha256(raw).hexdigest()}",
        "",
        "PRODUCT_FIELDS = {",
    ]
    lines += [f"    {name!r}: {kind}," for name, kind in fields.items()]
    lines += ["}", ""]
    return "\n".join(lines)


def main():
    text = render()
    if "--check" in sys.argv:
        if not TARGET.exists() or TARGET.read_text() != text:
            print("shop/generated/schema.py is out of date with schema/products.json")
            sys.exit(1)
        print("generated schema is up to date")
        return
    TARGET.write_text(text)


if __name__ == "__main__":
    main()
