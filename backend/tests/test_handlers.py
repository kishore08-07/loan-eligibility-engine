"""Lambda entrypoints: SQS partial-batch handling and scheduled housekeeping."""
import json

from app import db, ingestion
from app.handlers import ingest, maintenance
from tests.conftest import BUCKET
from tests.test_ingestion_integration import HEADER, _row, _upload


def _sqs_event(key, receive_count=1, message_id="m1"):
    body = json.dumps({"Records": [{"s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}]})
    return {"Records": [{"messageId": message_id, "body": body,
                         "attributes": {"ApproximateReceiveCount": str(receive_count)}}]}


def test_ingest_handler_acks_success(env):
    conn, s3, sent = env
    key = _upload(conn, s3, HEADER + _row(1))
    assert ingest.handler(_sqs_event(key)) == {"batchItemFailures": []}
    assert len(sent) == 1


def test_ingest_handler_reports_only_failed_message(env, monkeypatch):
    conn, s3, _ = env
    good = _upload(conn, s3, HEADER + _row(1))
    bad = _upload(conn, s3, HEADER + _row(2))
    real = ingestion.process_upload

    def flaky(c, bucket, key, final_attempt=False):
        if key == bad:
            raise ConnectionError("db blip")
        return real(c, bucket, key, final_attempt)

    monkeypatch.setattr(ingest, "process_upload", flaky)
    event = {"Records": _sqs_event(good, message_id="ok")["Records"] + _sqs_event(bad, message_id="bad")["Records"]}
    assert ingest.handler(event) == {"batchItemFailures": [{"itemIdentifier": "bad"}]}


def test_ingest_handler_ignores_s3_test_event_and_permanent_errors(env):
    event = {"Records": [{"messageId": "t", "body": json.dumps({"Event": "s3:TestEvent"}), "attributes": {}}]}
    assert ingest.handler(event) == {"batchItemFailures": []}
    assert ingest.handler(_sqs_event("uploads/not-a-job.csv")) == {"batchItemFailures": []}  # bad key: dropped


def test_final_attempt_webhook_failure_keeps_loaded_job_completed(env, monkeypatch):
    conn, s3, _ = env
    key = _upload(conn, s3, HEADER + _row(1))

    def boom(payload):
        raise ConnectionError("n8n down")

    monkeypatch.setattr(ingestion.webhook, "send", boom)
    result = ingest.handler(_sqs_event(key, receive_count=3))
    assert result["batchItemFailures"]  # still retried / DLQ'd
    # the handler resets the shared connection after a failure, so read through a fresh one
    assert db.get_conn().execute("SELECT status FROM upload_jobs").fetchone()[0] == "COMPLETED"  # data is safe


def test_final_attempt_load_failure_marks_job_failed(env, monkeypatch):
    conn, s3, _ = env
    key = _upload(conn, s3, HEADER + _row(1))
    monkeypatch.setattr(ingestion, "_hash_object", lambda *a: (_ for _ in ()).throw(ConnectionError("s3 down")))
    ingest.handler(_sqs_event(key, receive_count=3))
    status, msg = db.get_conn().execute("SELECT status, error_message FROM upload_jobs").fetchone()
    assert status == "FAILED" and "gave up after retries" in msg


def test_maintenance_expires_abandoned_and_stuck_jobs_only(env):
    conn, s3, _ = env
    for name, status, age in [("old-pending", "PENDING", 120), ("fresh-pending", "PENDING", 5),
                              ("stuck", "PROCESSING", 200), ("busy", "PROCESSING", 10),
                              ("done", "COMPLETED", 500)]:
        conn.execute("INSERT INTO upload_jobs (id, filename, s3_key, status, updated_at) "
                     "VALUES (gen_random_uuid(), %s, %s, %s, now() - make_interval(mins => %s))",
                     (name, f"uploads/{name}.csv", status, age))
    conn.commit()

    assert maintenance.handler() == {"pending_expired": 1, "processing_failed": 1}
    got = dict(conn.execute("SELECT filename, status FROM upload_jobs").fetchall())
    assert got == {"old-pending": "FAILED", "fresh-pending": "PENDING", "stuck": "FAILED",
                   "busy": "PROCESSING", "done": "COMPLETED"}
