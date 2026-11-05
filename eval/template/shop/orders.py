from shop import db
from shop.ids import new_id


def create_order(items):
    total = sum(item["price_cents"] * item["qty"] for item in items)
    return db.save("orders", {"id": new_id("ord"), "items": items, "total_cents": total})
