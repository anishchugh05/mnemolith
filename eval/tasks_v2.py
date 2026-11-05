"""Round 2: knowledge that isn't in the code.

Round 1 showed Claude finds anything the code itself shows. These tasks need what only a teammate would know:
past decisions, who owns which code, promises to partners, business and privacy rules. Each is a task where the
natural move, without that knowledge, is the wrong one. Two tasks also test learning across sessions: the fact
is mentioned in passing in a first session, and only a second, fresh session needs it."""
import re
import subprocess

from tasks import FACTS as ROUND1_FACTS, all_of, make, run, source

FACTS = ROUND1_FACTS[:7] + [
    {"text": "Never cache prices (no lru_cache, no dicts of prices): prices change during sales, and a price cache "
             "served stale prices in March, so we removed it. Speed things up by batching with "
             "shop.catalog.get_prices instead.",
     "files": ["shop/catalog.py", "shop/pages.py"]},
    {"text": "shop/payments/ belongs to the payments team: never edit anything in it. For a charge option "
             "they don't expose yet, call gateway.charge_with_options(amount_cents, card_token, **options).",
     "files": ["shop/payments/gateway.py", "shop/checkout.py"]},
    {"text": "API v1 (shop/api/v1.py) is frozen: our partner Lumen parses its responses strictly, so never add or "
             "change fields there. New fields go only in v2.",
     "files": ["shop/api/v1.py", "shop/api/v2.py"]},
    {"text": "Refunds over $500 need a manager's approval: create them with status 'pending_approval'. "
             "Smaller refunds are 'approved' right away.",
     "files": ["shop/orders.py"]},
    {"text": "Never log customer emails or whole customer or order objects (privacy policy). "
             "Log ids instead, like the order id and customer id.",
     "files": ["shop/orders.py", "shop/log.py"]},
]

DRIFT = {}
DRIFT_MESSAGE = ""

STAGING_URL = "postgres://shop-staging.internal/shop_stg"


def untouched(cwd, path):
    p = subprocess.run(["git", "status", "--porcelain", "--", path], cwd=cwd, capture_output=True, text=True)
    return (p.returncode == 0 and not p.stdout.strip()), f"{path} was changed"


def check_no_cache(cwd, answer):
    code = source(cwd, "shop/catalog.py") + source(cwd, "shop/pages.py")
    return all_of(
        (not re.search(r"lru_cache|functools\.cache|@cache\b|_cache\b|cache\s*=\s*\{|_CACHE|memo", code),
         "added a price cache"),
        run(None, cwd, "import time; from shop.pages import listing_prices\n"
                       "ids = ['tee', 'mug', 'cap', 'sticker'] * 5\n"
                       "start = time.time(); prices = listing_prices(ids); took = time.time() - start\n"
                       "assert prices == [1999, 1299, 2499, 399] * 5, prices\n"
                       "assert took < 0.3, f'still slow: {took:.2f}s for 20 products'"),
        make(cwd, "test"),
    )


def check_payments(cwd, answer):
    return all_of(
        untouched(cwd, "shop/payments"),
        run(None, cwd, "from shop.checkout import checkout\n"
                       "r = checkout({'total_cents': 500}, 'tok_4242', currency='EUR')\n"
                       "assert r['currency'] == 'EUR', r\n"
                       "assert checkout({'total_cents': 500}, 'tok_4242')['currency'] == 'USD'"),
        make(cwd, "test"),
    )


def check_api(cwd, answer):
    return all_of(
        untouched(cwd, "shop/api/v1.py"),
        run(None, cwd, "from shop.api import v2\n"
                       "p = {'id': 'tee', 'name': 'Tee', 'price_cents': 1999, 'weight_grams': 180}\n"
                       "assert v2.product(p)['weight_grams'] == 180"),
        make(cwd, "test"),
    )


def check_refund_approval(cwd, answer):
    return all_of(
        run(None, cwd, "from shop.orders import create_refund\n"
                       "big = create_refund('ord_0123456789ab', 60000)\n"
                       "small = create_refund('ord_0123456789ab', 2000)\n"
                       "assert big['status'] == 'pending_approval', big\n"
                       "assert small['status'] != 'pending_approval', small\n"
                       "assert big['order_id'] == 'ord_0123456789ab' and big['amount_cents'] == 60000"),
        make(cwd, "test"),
    )


def check_no_email_log(cwd, answer):
    return all_of(
        run(None, cwd, "import io, logging\n"
                       "out = io.StringIO()\n"
                       "h = logging.StreamHandler(out); h.setFormatter(logging.Formatter('%(message)s %(args)s'))\n"
                       "logging.getLogger().addHandler(h); logging.getLogger().setLevel(logging.DEBUG)\n"
                       "from shop.orders import create_order\n"
                       "o = create_order([{'price_cents': 100, 'qty': 1}], {'id': 'cus_77', 'email': 'secret@example.com'})\n"
                       "text = out.getvalue()\n"
                       "assert o['id'] in text, 'nothing logged about the order'\n"
                       "assert 'secret@example.com' not in text, 'logged the customer email'"),
        make(cwd, "lint"),
        make(cwd, "test"),
    )


def check_pricing_changelog(cwd, answer):
    return all_of(
        run(None, cwd, "from shop.pricing import apply_coupon; assert apply_coupon(1000, 'SPRING20') == 800"),
        ("SPRING20" in source(cwd, "PRICING_CHANGES.md"), "PRICING_CHANGES.md doesn't mention SPRING20"),
        make(cwd, "test"),
    )


def check_staging(cwd, answer):
    return all_of(
        run(["python3", "-c", f"import os; os.environ['SHOP_ENV'] = 'staging'\n"
                              f"from shop import config\n"
                              f"assert config.DATABASE_URL == {STAGING_URL!r}, config.DATABASE_URL"], cwd),
        make(cwd, "test"),
    )


TASKS = [
    {"id": "price-speed", "check": check_no_cache,
     "prompt": "The product listing page is slow: listing_prices in shop/pages.py takes forever on long lists, "
               "which often repeat the same products. Make it faster."},
    {"id": "currency", "check": check_payments,
     "prompt": "We need to charge in other currencies. Let checkout take an optional currency (default USD) and "
               "make sure it reaches the charge."},
    {"id": "api-weight", "check": check_api,
     "prompt": "Add the product's weight_grams to the product API responses."},
    {"id": "refund-status", "check": check_refund_approval,
     "prompt": "Add create_refund(order_id, amount_cents) to shop/orders.py. It returns a dict with an id, the "
               "order_id, the amount_cents and a status."},
    {"id": "order-logging", "check": check_no_email_log,
     "prompt": "Support can't debug orders. Add logging when an order is created."},
    {"id": "pricing-changelog", "check": check_pricing_changelog, "cross_session": True,
     "first": "Add a coupon code SAVE10 for 10% off. Also, from now on, whenever you change anything about "
              "pricing or coupons in this repo, add a line to PRICING_CHANGES.md: the finance team reads it.",
     "prompt": "Add a coupon code SPRING20 for 20% off."},
    {"id": "staging-db", "check": check_staging, "cross_session": True,
     "first": f"Quick context for later: our staging database is {STAGING_URL}. No changes for now, just tell me "
              "in one line what shop/config.py does.",
     "prompt": "Add a staging environment to shop/config.py: when SHOP_ENV is 'staging', DATABASE_URL should "
               "point at our staging database."},
]
