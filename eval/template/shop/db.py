from shop import config

if config.ENV != "test":
    raise RuntimeError("SHOP_ENV is not 'test': refusing to connect to the live database")

_rows = {}


def save(table, row):
    _rows.setdefault(table, []).append(row)
    return row


def all_rows(table):
    return list(_rows.get(table, []))
