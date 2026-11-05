"""What the evaluation knows: the facts about the sample shop, the change that makes two of them stale, and the
ten tasks with an automatic check for each. Standard library only."""
import os
import re
import subprocess
import sys
from pathlib import Path

# Things a teammate would tell you about tinyshop that the code doesn't spell out. The CLAUDE.md setup gets them
# as a notes file; the Mnemolith setup gets them as memories tied to the files listed.
FACTS = [
    {"text": "Run the tests with `make test`. It sets SHOP_ENV=test; running unittest or importing shop code without "
             "it fails with 'refusing to connect to the live database'.",
     "files": ["Makefile", "shop/db.py"]},
    {"text": "Money is always integer cents, never floats. Discounts round up, in the customer's favor.",
     "files": ["shop/pricing.py", "shop/money.py"]},
    {"text": "Never edit shop/generated/schema.py by hand: change schema/products.json and run `make gen`. "
             "CI runs `make gen-check`.",
     "files": ["schema/products.json", "tools/gen_schema.py"]},
    {"text": "All outgoing HTTP goes through shop.net.client.get, which adds retries and timeouts. The requests "
             "library isn't installed in production.",
     "files": ["shop/net/client.py"]},
    {"text": "Timestamps are UTC ISO 8601 strings ending in Z. Get one from shop.timeutil.now_iso().",
     "files": ["shop/timeutil.py"]},
    {"text": "IDs are a prefix, an underscore and 12 hex characters, made with shop.ids.new_id(prefix): "
             "ord for orders, ref for refunds, cus for customers.",
     "files": ["shop/ids.py"]},
    {"text": "Never use print in shop/: log with shop.log.get_logger(__name__). `make lint` fails on print.",
     "files": ["tools/lint.py", "shop/log.py"]},
    {"text": "Sales tax is 8%, set as TAX_RATE in shop/tax.py.",
     "files": ["shop/tax.py"]},
    {"text": "Orders of $50 or more ship free (FREE_SHIPPING_CENTS in shop/shipping.py).",
     "files": ["shop/shipping.py"]},
]

# Committed after the notes and memories are written, so the last two facts above are no longer true.
DRIFT = {
    "shop/tax.py": ("TAX_RATE = 0.08", "TAX_RATE = 0.0625"),
    "shop/shipping.py": ("FREE_SHIPPING_CENTS = 5000", "FREE_SHIPPING_CENTS = 7500"),
}
DRIFT_MESSAGE = "New state tax rate; raise the free shipping threshold to $75"


def run(cmd, cwd, code=None):
    env = dict(os.environ, SHOP_ENV="test", PYTHONPATH=str(cwd), PYTHONDONTWRITEBYTECODE="1")
    if code is not None:
        cmd = [sys.executable, "-c", code]
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
    return p.returncode == 0, (p.stdout + p.stderr).strip()[-400:]


def make(cwd, target):
    return run(["make", "-s", target], cwd)


def source(cwd, rel):
    path = Path(cwd) / rel
    return path.read_text() if path.exists() else ""


def all_of(*results):
    """Each result is (ok, note). Passes only if every one passes; the note says what failed."""
    failed = [note for ok, note in results if not ok]
    return (not failed), ("; ".join(failed) if failed else "ok")


def check_cart_count(cwd, answer):
    return all_of(
        run(None, cwd, "from shop.orders import cart_count; assert cart_count([{'qty': 2}, {'qty': 3}]) == 5"),
        (bool(re.search(r"cart_count", " ".join(p.read_text() for p in Path(cwd, "tests").glob("*.py")))),
         "no test mentions cart_count"),
        make(cwd, "test"),
    )


def check_save10(cwd, answer):
    return all_of(
        run(None, cwd, "from shop.pricing import apply_coupon as a; r = a(1999, 'SAVE10'); "
                       "assert r == 1799 and isinstance(r, int), r; assert a(1000, 'HALF') == 500"),
        make(cwd, "test"),
    )


def check_weight(cwd, answer):
    return all_of(
        ("weight_grams" in source(cwd, "schema/products.json"), "schema/products.json has no weight_grams"),
        ("weight_grams" in source(cwd, "shop/generated/schema.py"), "generated schema has no weight_grams"),
        make(cwd, "gen-check"),
    )


def check_fx(cwd, answer):
    code = source(cwd, "shop/fx.py")
    return all_of(
        (bool(code), "shop/fx.py missing"),
        (not re.search(r"\brequests\b|urllib|http\.client|httpx", code), "makes HTTP calls without shop.net.client"),
        run(None, cwd, "import shop.net.client as c\n"
                       "seen = []\n"
                       "c.get = lambda url, *a, **k: seen.append(url) or {'rate': 1.25}\n"
                       "import shop.fx as fx\n"
                       "assert fx.fetch_rate('EUR') == 1.25\n"
                       "assert seen and seen[0].endswith('/v1/EUR'), seen"),
    )


def check_created_at(cwd, answer):
    return all_of(
        run(None, cwd, "import re; from shop.orders import create_order\n"
                       "o = create_order([{'price_cents': 100, 'qty': 1}])\n"
                       "assert re.fullmatch(r'\\d{4}-\\d\\d-\\d\\dT\\d\\d:\\d\\d:\\d\\d(\\.\\d+)?Z', o['created_at']), "
                       "o.get('created_at')"),
        make(cwd, "test"),
    )


def check_refund(cwd, answer):
    return all_of(
        run(None, cwd, "import re; from shop.orders import create_refund\n"
                       "r = create_refund('ord_0123456789ab', 500)\n"
                       "assert re.fullmatch(r'ref_[0-9a-f]{12}', r['id']), r['id']\n"
                       "assert r['order_id'] == 'ord_0123456789ab' and r['amount_cents'] == 500"),
        make(cwd, "test"),
    )


def check_unknown_coupon(cwd, answer):
    return all_of(
        run(None, cwd, "from shop.pricing import apply_coupon; assert apply_coupon(1000, 'NOPE') == 1000"),
        ("get_logger" in source(cwd, "shop/pricing.py"), "doesn't use shop.log.get_logger"),
        make(cwd, "lint"),
        make(cwd, "test"),
    )


def check_tax_question(cwd, answer):
    # Only the first line is the answer; an explanation after it may mention the old 8% as out of date.
    first = (answer.strip().splitlines() or [""])[0].replace(" ", "")
    return ("6.25" in first and "8%" not in first), f"answered: {answer.strip()[:80]!r}"


def check_shipping_question(cwd, answer):
    first = re.sub(r"[^a-z]", " ", answer.strip().lower()).split()
    return (bool(first) and first[0] == "no"), f"answered: {answer.strip()[:80]!r}"


def check_total_with_tax(cwd, answer):
    return all_of(
        run(None, cwd, "from shop.tax import order_total_with_tax as t; r = t({'total_cents': 2000}); "
                       "assert r == 2125 and isinstance(r, int), r"),
        make(cwd, "test"),
    )


TASKS = [
    {"id": "cart-count", "check": check_cart_count,
     "prompt": "Add a function cart_count(items) to shop/orders.py that returns the total quantity across order "
               "items (each item is a dict with a qty). Add a unit test for it and make sure the whole test suite passes."},
    {"id": "save10", "check": check_save10,
     "prompt": "Add a coupon code SAVE10 that takes 10% off the order total."},
    {"id": "weight", "check": check_weight,
     "prompt": "Products need a weight_grams integer field. Add it."},
    {"id": "fx", "check": check_fx,
     "prompt": "Add fetch_rate(currency) in a new module shop/fx.py. It fetches "
               "https://rates.example.com/v1/<currency> and returns the rate value from the JSON."},
    {"id": "created-at", "check": check_created_at,
     "prompt": "Orders should record when they were created. Add a created_at field to what create_order returns."},
    {"id": "refund", "check": check_refund,
     "prompt": "Add create_refund(order_id, amount_cents) to shop/orders.py. It returns a dict with an id, "
               "the order_id and the amount_cents."},
    {"id": "unknown-coupon", "check": check_unknown_coupon,
     "prompt": "apply_coupon should log a warning and return the total unchanged when it gets a coupon code it "
               "doesn't know, instead of crashing."},
    {"id": "tax-question", "check": check_tax_question, "stale": True,
     "prompt": "What's our sales tax rate? Reply with just the percentage."},
    {"id": "shipping-question", "check": check_shipping_question, "stale": True,
     "prompt": "A customer's order comes to $60. Do they get free shipping? Start your answer with yes or no."},
    {"id": "total-with-tax", "check": check_total_with_tax, "stale": True,
     "prompt": "Add order_total_with_tax(order) to shop/tax.py. It takes an order dict with total_cents and "
               "returns the total including sales tax."},
]
