"""Load seed products into the database (idempotent). Run standalone or via bootstrap_local.py."""
import _env

_env.load()

from app.seed import seed  # noqa: E402,F401

if __name__ == "__main__":
    print("seeded", seed(), "products")
