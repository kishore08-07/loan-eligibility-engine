"""Run once after migrate: `serverless invoke -f seed` (loads the illustrative seed products)."""
from app.seed import seed


def handler(event=None, context=None):
    return {"seeded": seed()}
