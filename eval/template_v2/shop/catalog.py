import time

_PRICES = {"tee": 1999, "mug": 1299, "cap": 2499, "sticker": 399}


def get_price(product_id):
    time.sleep(0.05)  # stands in for a call to the pricing service
    return _PRICES[product_id]


def get_prices(product_ids):
    time.sleep(0.05)
    return {product_id: _PRICES[product_id] for product_id in product_ids}
