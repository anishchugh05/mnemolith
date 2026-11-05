FREE_SHIPPING_CENTS = 5000
SHIPPING_FEE_CENTS = 799


def shipping_cents(total_cents):
    return 0 if total_cents >= FREE_SHIPPING_CENTS else SHIPPING_FEE_CENTS
