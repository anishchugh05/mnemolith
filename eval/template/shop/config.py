import os

ENV = os.environ.get("SHOP_ENV", "production")
DATABASE_URL = "sqlite:///:memory:" if ENV == "test" else "postgres://shop-prod.internal/shop"
