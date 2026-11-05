def charge(amount_cents, card_token):
    return charge_with_options(amount_cents, card_token)


def charge_with_options(amount_cents, card_token, **options):
    return {"status": "charged", "amount_cents": amount_cents, "currency": options.get("currency", "USD"),
            "card": card_token[-4:]}
