import math

COUPONS = {
    "HALF": 50,
}


def apply_coupon(total_cents, code):
    percent = COUPONS[code]
    discount = math.ceil(total_cents * percent / 100)
    return total_cents - discount
