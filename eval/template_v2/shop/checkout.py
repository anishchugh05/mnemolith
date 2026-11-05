from shop.payments import gateway


def checkout(order, card_token):
    return gateway.charge(order["total_cents"], card_token)
