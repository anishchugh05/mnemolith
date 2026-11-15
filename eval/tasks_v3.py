"""Round 3: two teammates.

Teammate A tells their AI something about the project and asks for the rest of the team's AI tools to know it.
A commits and pushes what that session left in the repo. Teammate B, in their own clone with their own personal
memories, then gets a task that needs it, in a fresh session.

Four tasks stop there. In three, another teammate changes the code between the two sessions, so what A shared
is no longer true, and B's AI has to notice instead of following it. Those commits describe the change the way
real ones do, without saying that the rule ended: the code shows it, the commit message doesn't spell it out.

Four setups: no memory, a CLAUDE.md notes file in the repo, Mnemolith with personal memories only (each person
has their own, so nothing A's AI saves reaches B), and Mnemolith with team memory."""
import re
from pathlib import Path

from tasks import FACTS as ROUND1_FACTS, all_of, make, run, source
from tasks_v2 import (STAGING_URL, check_api, check_pricing_changelog, check_refund_approval, check_staging,
                      untouched)

TEAM = True  # run.py: two clones and two sessions per run (see run_team)
ARMS = ["none", "notes", "mnemolith", "team"]

# What both teammates start with in every setup that has memory: how the repo works day to day.
FACTS = ROUND1_FACTS[:7]

DRIFT = {}
DRIFT_MESSAGE = ""

SHARE = " Make sure the rest of the team's AI tools know this too."


def commit(cwd, message):
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=cwd, check=True)
    subprocess.run(["git", "-c", "user.name=Priya", "-c", "user.email=priya@example.com", "commit", "-q", "-m", message],
                   cwd=cwd, check=True)


# ---------- what another teammate changes, in the tasks where the shared fact goes out of date ----------

def unfreeze_v1(cwd):
    (Path(cwd) / "shop/api/v1.py").write_text(
        'def product(p):\n    return {"id": p["id"], "name": p["name"], "price": p["price_cents"]}\n')
    commit(cwd, "Add id to API v1 product")


def take_over_payments(cwd):
    (Path(cwd) / "shop/payments/gateway.py").write_text(
        'def charge(amount_cents, card_token):\n'
        '    return {"status": "charged", "amount_cents": amount_cents, "currency": "USD", "card": card_token[-4:]}\n')
    commit(cwd, "Simplify the payment gateway")


def move_pricing_log(cwd):
    old, new = Path(cwd) / "PRICING_CHANGES.md", Path(cwd) / "docs/pricing-log.md"
    new.parent.mkdir(exist_ok=True)
    lines = old.read_text() if old.exists() else ""
    new.write_text("# Pricing log\n\nEvery change to pricing or coupons, one line each. Finance reads this page.\n\n"
                   + "\n".join(line for line in lines.splitlines() if line.strip() and not line.startswith("#")) + "\n")
    if old.exists():
        old.unlink()
    commit(cwd, "Move pricing changelog into docs/")


# ---------- checks ----------
#
# In the three tasks where the code changed after sharing, two answers are right: do what the code now needs, or
# notice that what was shared looks out of date and check with the user instead of changing anything. The wrong
# answer is following the old rule as if nothing had changed.

FLAGGED = re.compile(r"out of date|outdated|stale|no longer (?:true|accurate|applies|holds|frozen|exists|there)|"
                     r"(?:is|was) (?:gone|removed|deleted)|has been removed|doesn'?t exist|changed since|"
                     r"may have changed|might have changed|still (?:frozen|true|accurate|appl|hold|the case)|"
                     r"(?:confirm|check) with (?:you|the|your|whoever|priya)", re.I)


def checked_instead(cwd, answer, paths):
    """The AI changed none of `paths` and said the shared rule looked out of date. Returns (ok, note)."""
    import subprocess
    p = subprocess.run(["git", "status", "--porcelain", "--", *paths], cwd=cwd, capture_output=True, text=True)
    touched = [line for line in p.stdout.splitlines() if "__pycache__" not in line]
    return (not touched and bool(FLAGGED.search(answer))), "followed the old rule without saying it looked out of date"


def either(right, careful):
    return right if right[0] else careful if careful[0] else (False, f"{right[1]} (and {careful[1]})")

def check_api_both(cwd, answer):
    return either(_api_both(cwd), checked_instead(cwd, answer, ["shop/api/v1.py"]))


def _api_both(cwd):
    return all_of(
        run(None, cwd, "from shop.api import v1, v2\n"
                       "p = {'id': 'tee', 'name': 'Tee', 'price_cents': 1999, 'weight_grams': 180}\n"
                       "assert v2.product(p).get('weight_grams') == 180, 'v2 has no weight_grams'\n"
                       "assert 180 in v1.product(p).values(), 'v1 was left without the weight (it is no longer frozen)'"),
        make(cwd, "test"),
    )


def check_payments_ours(cwd, answer):
    return either(_payments_ours(cwd), checked_instead(cwd, answer, ["shop/payments", "shop/checkout.py"]))


def _payments_ours(cwd):
    return all_of(
        run(None, cwd, "from shop.checkout import checkout\n"
                       "from shop.payments import gateway\n"
                       "r = checkout({'total_cents': 500}, 'tok_4242', currency='EUR')\n"
                       "assert r['currency'] == 'EUR', r\n"
                       "assert checkout({'total_cents': 500}, 'tok_4242')['currency'] == 'USD'\n"
                       "def takes_currency(f):\n"
                       "    try:\n"
                       "        return f(500, 'tok_4242', currency='EUR').get('currency') == 'EUR'\n"
                       "    except Exception:\n"
                       "        return False\n"
                       "fns = [getattr(gateway, n) for n in dir(gateway) if not n.startswith('_') and callable(getattr(gateway, n))]\n"
                       "assert any(takes_currency(f) for f in fns), 'the currency never reaches the gateway'"),
        make(cwd, "test"),
    )


def check_pricing_log_moved(cwd, answer):
    return all_of(
        run(None, cwd, "from shop.pricing import apply_coupon; assert apply_coupon(1000, 'SPRING20') == 800"),
        (not (Path(cwd) / "PRICING_CHANGES.md").exists(), "brought back PRICING_CHANGES.md, which finance no longer reads"),
        ("SPRING20" in source(cwd, "docs/pricing-log.md"), "docs/pricing-log.md doesn't mention SPRING20"),
        make(cwd, "test"),
    )


# ---------- what teammate A says in the first session ----------

TELL_STAGING = (f"Quick context for later: our staging database is {STAGING_URL}.{SHARE} No code changes for now, "
                "just tell me in one line what shop/config.py does.")
TELL_PRICING = ("Add a coupon code SAVE10 for 10% off. Also, from now on, whenever anyone changes anything about "
                "pricing or coupons in this repo, they add a line to PRICING_CHANGES.md: the finance team reads it."
                + SHARE)
TELL_API = ("Heads up: API v1 (shop/api/v1.py) is frozen. Our partner Lumen parses its responses strictly, so nobody "
            f"should add or change fields there; new fields go only in v2.{SHARE} No code changes for now, just tell "
            "me in one line what shop/api/v1.py returns.")
TELL_REFUNDS = ("A rule from finance: refunds over $500 need a manager's approval, so they're created with status "
                f"'pending_approval'. Smaller refunds are 'approved' right away.{SHARE} No code changes for now, just "
                "tell me in one line what shop/orders.py does.")
TELL_PAYMENTS = ("Heads up: shop/payments/ belongs to the payments team, so nobody on our team edits anything in it. "
                 "For a charge option they don't expose yet, we call gateway.charge_with_options(amount_cents, "
                 f"card_token, **options).{SHARE} No code changes for now, just tell me in one line what "
                 "shop/checkout.py does.")

ASK_WEIGHT = "Add the product's weight_grams to the product API responses."
ASK_CURRENCY = ("We need to charge in other currencies. Let checkout take an optional currency (default USD) and "
                "make sure it reaches the charge.")
ASK_SPRING = "Add a coupon code SPRING20 for 20% off."

TASKS = [
    {"id": "staging-db", "check": check_staging, "cross_session": True, "first": TELL_STAGING,
     "prompt": "Add a staging environment to shop/config.py: when SHOP_ENV is 'staging', DATABASE_URL should "
               "point at our staging database."},
    {"id": "pricing-changelog", "check": check_pricing_changelog, "cross_session": True, "first": TELL_PRICING,
     "prompt": ASK_SPRING},
    {"id": "api-frozen", "check": check_api, "cross_session": True, "first": TELL_API, "prompt": ASK_WEIGHT},
    {"id": "refund-approval", "check": check_refund_approval, "cross_session": True, "first": TELL_REFUNDS,
     "prompt": "Add create_refund(order_id, amount_cents) to shop/orders.py. It returns a dict with an id, the "
               "order_id, the amount_cents and a status."},
    {"id": "api-unfrozen", "check": check_api_both, "cross_session": True, "stale": True, "first": TELL_API,
     "drift": unfreeze_v1, "prompt": ASK_WEIGHT},
    {"id": "payments-ours", "check": check_payments_ours, "cross_session": True, "stale": True,
     "first": TELL_PAYMENTS, "drift": take_over_payments, "prompt": ASK_CURRENCY},
    {"id": "pricing-log-moved", "check": check_pricing_log_moved, "cross_session": True, "stale": True,
     "first": TELL_PRICING, "drift": move_pricing_log, "prompt": ASK_SPRING},
]
