"""Postgres connection handling. The connection is cached across warm Lambda invocations."""
import psycopg

from app import config

_conn: psycopg.Connection | None = None


def get_conn() -> psycopg.Connection:
    """Return a live connection, reconnecting if the cached one is closed or broken."""
    global _conn
    if _conn is not None and not _conn.closed and not _conn.broken:
        return _conn
    _conn = psycopg.connect(config.database_url(), connect_timeout=5)
    return _conn


def reset() -> None:
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
    _conn = None
