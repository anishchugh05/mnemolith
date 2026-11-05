from shop.catalog import get_price


def listing_prices(product_ids):
    """Prices for a product listing page, in the order given."""
    return [get_price(product_id) for product_id in product_ids]
