"""Shared fixtures: real Postgres (docker compose) + moto S3 in-process.

Needs DATABASE_URL pointing at the compose Postgres; a separate `loans_test` database is created.
"""
import os

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

moto = pytest.importorskip("moto")
boto3 = pytest.importorskip("boto3")

from app import aws, config, db, ingestion, migrate  # noqa: E402

BUCKET = "test-bucket"


def _test_dsn() -> str:
    base = os.environ.get("DATABASE_URL")
    if not base:
        pytest.skip("DATABASE_URL not set")
    info = conninfo_to_dict(base)
    try:
        with psycopg.connect(make_conninfo(**{**info, "dbname": "postgres"}), autocommit=True,
                             connect_timeout=3) as admin:
            if not admin.execute("SELECT 1 FROM pg_database WHERE datname = 'loans_test'").fetchone():
                admin.execute("CREATE DATABASE loans_test")
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable")
    return make_conninfo(**{**info, "dbname": "loans_test"})


@pytest.fixture()
def env(monkeypatch):
    dsn = _test_dsn()
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)  # boto3 reads this natively; tests use in-process moto
    monkeypatch.setattr(config, "AWS_ENDPOINT_URL", None)
    monkeypatch.setattr(aws, "_s3", None)
    db.reset()
    conn = db.get_conn()
    conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    conn.commit()
    migrate.run()
    sent = []
    monkeypatch.setattr(ingestion.webhook, "send", lambda payload: sent.append(payload))
    with moto.mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        monkeypatch.setattr(aws, "_s3", s3)
        monkeypatch.setattr(aws, "_s3_public", s3)
        monkeypatch.setattr(config, "UPLOAD_BUCKET", BUCKET)
        yield conn, s3, sent
    db.reset()
