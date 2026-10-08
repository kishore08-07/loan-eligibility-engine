"""CSV ingestion: S3 object -> validated rows -> Postgres (COPY into staging, then upsert).

Idempotent by design: the whole load is one transaction, so a crash before commit leaves
nothing behind and an SQS redelivery simply redoes it. Re-uploading identical content is
detected by SHA-256 and marked DUPLICATE.
"""
import csv
import hashlib
import io
import re
import uuid
from dataclasses import dataclass

import psycopg
from psycopg import errors as pg_errors

from app import config, validation, webhook
from app.aws import s3
from app.log import get_logger, log

logger = get_logger("ingestion")

DONE_STATUSES = ("COMPLETED", "COMPLETED_WITH_ERRORS")
TERMINAL_STATUSES = DONE_STATUSES + ("FAILED", "DUPLICATE")
_KEY_RE = re.compile(r"^uploads/([0-9a-f-]{36})\.csv$")


class PermanentError(Exception):
    """Bad input: retrying cannot help. The job is marked FAILED and the message is acked."""


@dataclass
class Result:
    job_id: str
    status: str


def job_id_from_key(key: str) -> str:
    match = _KEY_RE.match(key)
    if not match:
        raise PermanentError(f"unexpected object key {key!r}")
    return str(uuid.UUID(match.group(1)))


def _lines(body) -> io.TextIOBase:
    return io.TextIOWrapper(body, encoding="utf-8-sig", errors="replace", newline="")


def _hash_object(bucket: str, key: str) -> str:
    digest = hashlib.sha256()
    for chunk in s3().get_object(Bucket=bucket, Key=key)["Body"].iter_chunks(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _set_job(conn, job_id: str, **fields) -> None:
    cols = ", ".join(f"{k} = %({k})s" for k in fields)
    conn.execute(f"UPDATE upload_jobs SET {cols}, updated_at = now() WHERE id = %(id)s",
                 {**fields, "id": job_id})


def _fail(conn, job_id: str, message: str) -> Result:
    conn.rollback()
    conn.execute("UPDATE upload_jobs SET status = 'FAILED', error_message = %s, "
                 "finished_at = now(), updated_at = now() WHERE id = %s", (message[:500], job_id))
    conn.commit()
    log(logger, "job failed", job_id=job_id, error=message)
    return Result(job_id, "FAILED")


def process_upload(conn: psycopg.Connection, bucket: str, key: str, final_attempt: bool = False) -> Result:
    """Process one uploaded object. Raises on transient errors so SQS retries the message."""
    job_id = job_id_from_key(key)
    row = conn.execute("SELECT status, webhook_delivered_at FROM upload_jobs WHERE id = %s", (job_id,)).fetchone()
    conn.rollback()
    if row is None:
        raise PermanentError(f"no upload job {job_id}")
    status, delivered_at = row

    if status in DONE_STATUSES:  # redelivery after success: only the webhook may be missing
        if delivered_at is None:
            _dispatch(conn, job_id)
        return Result(job_id, status)
    if status in TERMINAL_STATUSES:
        return Result(job_id, status)

    try:
        return _load(conn, job_id, bucket, key)
    except PermanentError as exc:
        return _fail(conn, job_id, str(exc))
    except Exception as exc:
        conn.rollback()
        if final_attempt:
            _fail(conn, job_id, f"gave up after retries: {exc}")
        raise


def _load(conn: psycopg.Connection, job_id: str, bucket: str, key: str) -> Result:
    _set_job(conn, job_id, status="PROCESSING")
    conn.commit()

    file_hash = _hash_object(bucket, key)
    original = conn.execute(
        "SELECT id FROM upload_jobs WHERE file_hash = %s AND status = ANY(%s) AND id <> %s",
        (file_hash, list(DONE_STATUSES), job_id)).fetchone()
    if original:
        _set_job(conn, job_id, status="DUPLICATE", duplicate_of=original[0], file_hash=None,
                 error_message="Identical file was already processed")
        conn.execute("UPDATE upload_jobs SET finished_at = now() WHERE id = %s", (job_id,))
        conn.commit()
        log(logger, "duplicate upload", job_id=job_id, duplicate_of=str(original[0]))
        return Result(job_id, "DUPLICATE")

    body = s3().get_object(Bucket=bucket, Key=key)["Body"]
    reader = csv.DictReader(_lines(body))
    missing = validation.missing_columns(reader.fieldnames)
    if missing:
        raise PermanentError(f"missing required columns: {', '.join(missing)}")

    total = valid = 0
    rejected: list[tuple[int, str, str]] = []  # (row_number, user_id, reason)
    seen: set[str] = set()

    conn.execute(
        "CREATE TEMP TABLE staging (user_id text, name text, email text, monthly_income numeric, "
        "credit_score int, employment_status text, age int) ON COMMIT DROP")
    with conn.cursor() as cur, cur.copy("COPY staging FROM STDIN") as copy:
        for row_number, raw in enumerate(reader, start=2):  # row 1 is the header
            total += 1
            try:
                user = validation.parse_row(raw)
                if user.user_id in seen:
                    raise validation.RowError("duplicate user_id in file (first occurrence kept)")
            except validation.RowError as exc:
                rejected.append((row_number, (raw.get("user_id") or "").strip(), str(exc)))
                continue
            seen.add(user.user_id)
            copy.write_row((user.user_id, user.name, user.email, user.monthly_income,
                            user.credit_score, user.employment_status, user.age))
            valid += 1

    if valid == 0:
        raise PermanentError("file contains no valid rows")

    conn.execute(
        """
        INSERT INTO users (user_id, name, email, monthly_income, credit_score,
                           employment_status, age, upload_job_id)
        SELECT user_id, name, email, monthly_income, credit_score, employment_status, age, %s
        FROM staging
        ON CONFLICT (user_id) DO UPDATE SET
            name = EXCLUDED.name, email = EXCLUDED.email,
            monthly_income = EXCLUDED.monthly_income, credit_score = EXCLUDED.credit_score,
            employment_status = EXCLUDED.employment_status, age = EXCLUDED.age,
            upload_job_id = EXCLUDED.upload_job_id, updated_at = now()
        """, (job_id,))

    error_key = _write_error_report(bucket, job_id, rejected) if rejected else None
    final_status = "COMPLETED_WITH_ERRORS" if rejected else "COMPLETED"
    try:
        _set_job(conn, job_id, status=final_status, file_hash=file_hash, total_rows=total,
                 valid_rows=valid, rejected_rows=len(rejected), error_key=error_key)
        conn.execute("UPDATE upload_jobs SET finished_at = now() WHERE id = %s", (job_id,))
        conn.commit()
    except pg_errors.UniqueViolation:
        # Two identical files finished concurrently; the other one won.
        conn.rollback()
        _set_job(conn, job_id, status="DUPLICATE", error_message="Identical file was processed concurrently")
        conn.commit()
        return Result(job_id, "DUPLICATE")

    log(logger, "job loaded", job_id=job_id, total=total, valid=valid, rejected=len(rejected))
    _dispatch(conn, job_id)
    return Result(job_id, final_status)


def _write_error_report(bucket: str, job_id: str, rejected: list[tuple[int, str, str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["row_number", "user_id", "reason"])
    writer.writerows(rejected)
    key = f"errors/{job_id}.csv"
    s3().put_object(Bucket=bucket, Key=key, Body=buf.getvalue().encode(), ContentType="text/csv")
    return key


def _dispatch(conn: psycopg.Connection, job_id: str) -> None:
    """Tell n8n Workflow B the users are ready. Raises on failure so the message is retried."""
    row = conn.execute("SELECT total_rows, valid_rows, rejected_rows FROM upload_jobs WHERE id = %s", (job_id,)).fetchone()
    conn.execute("UPDATE upload_jobs SET webhook_attempts = webhook_attempts + 1 WHERE id = %s", (job_id,))
    conn.commit()
    webhook.send({"upload_job_id": job_id, "total_rows": row[0], "valid_rows": row[1], "rejected_rows": row[2]})
    conn.execute("UPDATE upload_jobs SET webhook_delivered_at = now(), updated_at = now() WHERE id = %s", (job_id,))
    conn.commit()
    log(logger, "webhook delivered", job_id=job_id)
