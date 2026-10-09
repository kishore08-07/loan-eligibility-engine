"""Integration tests for the ingestion service."""
import uuid

import pytest

from app import ingestion
from tests.conftest import BUCKET

HEADER = "user_id,name,email,monthly_income,credit_score,employment_status,age\n"


def _upload(conn, s3, body: str) -> str:
    job_id = str(uuid.uuid4())
    key = f"uploads/{job_id}.csv"
    conn.execute("INSERT INTO upload_jobs (id, filename, s3_key) VALUES (%s, 'f.csv', %s)", (job_id, key))
    conn.commit()
    s3.put_object(Bucket=BUCKET, Key=key, Body=body.encode())
    return key


def _row(i, email=None, score=700):
    return f"u{i},User {i},{email or f'u{i}@x.com'},50000,{score},Salaried,30\n"


def test_load_validates_rejects_and_notifies(env):
    conn, s3, sent = env
    key = _upload(conn, s3, HEADER + _row(1) + _row(2) + _row(2) + _row(3, score=999))
    result = ingestion.process_upload(conn, BUCKET, key)

    assert result.status == "COMPLETED_WITH_ERRORS"
    job = conn.execute("SELECT total_rows, valid_rows, rejected_rows, error_key, webhook_delivered_at "
                       "FROM upload_jobs WHERE id = %s", (result.job_id,)).fetchone()
    assert job[:3] == (4, 2, 2) and job[4] is not None
    assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 2
    report = s3.get_object(Bucket=BUCKET, Key=job[3])["Body"].read().decode()
    assert "duplicate user_id" in report and "credit_score" in report
    assert len(sent) == 1 and sent[0]["upload_job_id"] == result.job_id


def test_email_is_not_unique(env):
    conn, s3, _ = env
    key = _upload(conn, s3, HEADER + _row(1, "same@x.com") + _row(2, "same@x.com"))
    assert ingestion.process_upload(conn, BUCKET, key).status == "COMPLETED"
    assert conn.execute("SELECT count(*) FROM users WHERE email = 'same@x.com'").fetchone()[0] == 2


def test_identical_reupload_is_detected(env):
    conn, s3, sent = env
    body = HEADER + _row(1) + _row(2)
    first = ingestion.process_upload(conn, BUCKET, _upload(conn, s3, body))
    second = ingestion.process_upload(conn, BUCKET, _upload(conn, s3, body))
    assert (first.status, second.status) == ("COMPLETED", "DUPLICATE")
    assert len(sent) == 1  # no second matching run


def test_changed_reupload_updates_user_in_place(env):
    conn, s3, _ = env
    ingestion.process_upload(conn, BUCKET, _upload(conn, s3, HEADER + _row(1, score=700)))
    second = ingestion.process_upload(conn, BUCKET, _upload(conn, s3, HEADER + _row(1, score=750)))
    assert second.status == "COMPLETED"
    score, job = conn.execute("SELECT credit_score, upload_job_id FROM users WHERE user_id = 'u1'").fetchone()
    assert score == 750 and str(job) == second.job_id


def test_missing_columns_fails_job_permanently(env):
    conn, s3, sent = env
    key = _upload(conn, s3, "user_id,email\nu1,a@x.com\n")
    result = ingestion.process_upload(conn, BUCKET, key)
    assert result.status == "FAILED"
    msg = conn.execute("SELECT error_message FROM upload_jobs WHERE id = %s", (result.job_id,)).fetchone()[0]
    assert "missing required columns" in msg and not sent


def test_webhook_failure_is_retried_without_reloading(env, monkeypatch):
    conn, s3, sent = env
    key = _upload(conn, s3, HEADER + _row(1))

    def boom(payload):
        raise ConnectionError("n8n down")

    monkeypatch.setattr(ingestion.webhook, "send", boom)
    with pytest.raises(ConnectionError):
        ingestion.process_upload(conn, BUCKET, key)
    status, delivered = conn.execute("SELECT status, webhook_delivered_at FROM upload_jobs").fetchone()
    assert status == "COMPLETED" and delivered is None  # data is safe, only the webhook is pending

    monkeypatch.setattr(ingestion.webhook, "send", lambda payload: sent.append(payload))
    assert ingestion.process_upload(conn, BUCKET, key).status == "COMPLETED"  # SQS redelivery
    assert len(sent) == 1
    assert conn.execute("SELECT webhook_delivered_at FROM upload_jobs").fetchone()[0] is not None


def test_allow_duplicate_job_is_processed_and_original_stays_the_reference(env):
    conn, s3, sent = env
    body = HEADER + _row(1) + _row(2)
    first = ingestion.process_upload(conn, BUCKET, _upload(conn, s3, body))
    dup = ingestion.process_upload(conn, BUCKET, _upload(conn, s3, body))
    assert (first.status, dup.status) == ("COMPLETED", "DUPLICATE")

    key = _upload(conn, s3, body)
    conn.execute("UPDATE upload_jobs SET allow_duplicate = true WHERE s3_key = %s", (key,))
    conn.commit()
    forced = ingestion.process_upload(conn, BUCKET, key)
    assert forced.status == "COMPLETED" and len(sent) == 2  # matching runs again
    assert conn.execute("SELECT file_hash FROM upload_jobs WHERE id = %s", (forced.job_id,)).fetchone()[0] is None

    again = ingestion.process_upload(conn, BUCKET, _upload(conn, s3, body))
    assert again.status == "DUPLICATE"  # a normal re-upload is still caught, against the original
