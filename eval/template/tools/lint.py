import re
import sys
from pathlib import Path

bad = []
for path in Path(__file__).resolve().parent.parent.joinpath("shop").rglob("*.py"):
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if re.search(r"(^|\W)print\(", line):
            bad.append(f"{path.name}:{number}: print() is not allowed in shop/")
if bad:
    print("\n".join(bad))
    sys.exit(1)
print("lint ok")
