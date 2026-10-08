"""Run once after deploy: `serverless invoke -f migrate`."""
from app import migrate


def handler(event=None, context=None):
    return {"applied": migrate.run()}
