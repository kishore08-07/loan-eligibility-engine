"""Scheduled housekeeping (EventBridge, every 15 min): fail upload jobs that can never finish."""
from app import config, db
from app.ingestion import expire_stale_jobs


def handler(event=None, context=None):
    try:
        return expire_stale_jobs(db.get_conn(), config.PENDING_EXPIRY_MINUTES, config.PROCESSING_EXPIRY_MINUTES)
    except Exception:
        db.reset()
        raise
