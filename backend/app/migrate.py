"""Minimal forward-only SQL migration runner (migrations/NNN_name.sql)."""
import pathlib

from app import db
from app.log import get_logger, log

logger = get_logger("migrate")
MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"


def run() -> list[str]:
    conn = db.get_conn()
    applied_now: list[str] = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(727274)")  # serialise concurrent runners
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                     "(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
            applied_now.append(path.name)
            log(logger, "migration applied", version=path.name)
    return applied_now


if __name__ == "__main__":
    print("applied:", run() or "nothing to do")
