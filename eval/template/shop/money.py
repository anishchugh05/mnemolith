def format_cents(cents):
    return f"${cents // 100}.{cents % 100:02d}"
