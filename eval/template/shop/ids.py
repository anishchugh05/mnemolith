import secrets


def new_id(prefix):
    return f"{prefix}_{secrets.token_hex(6)}"
