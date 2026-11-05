from shop import db
from shop.ids import new_id


def create_order(items, customer):
    total = sum(item["price_cents"] * item["qty"] for item in items)
    order = {"id": new_id("ord"), "customer": customer, "items": items, "total_cents": total}
    return db.save("orders", order)
