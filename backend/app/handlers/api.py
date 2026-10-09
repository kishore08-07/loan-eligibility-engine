"""HTTP API Lambda handlers (API Gateway HTTP API, payload format 2.0). One function per route."""
import datetime
import decimal
import hmac
import json
import re
import uuid

from psycopg.rows import dict_row

from app import config, db
from app.aws import s3, s3_public
from app.log import get_logger

logger = get_logger("handler.api")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _default(value):
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.datetime, datetime.date, uuid.UUID)):
        return str(value) if isinstance(value, uuid.UUID) else value.isoformat()
    raise TypeError(type(value))


def _resp(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body, default=_default)}


def _rows(sql: str, params=()) -> list[dict]:
    with db.get_conn().cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    db.get_conn().rollback()
    return rows


def _authorised(event) -> bool:
    """True when no API_KEY is configured, or the request carries it in the x-api-key header."""
    if not config.API_KEY:
        return True
    supplied = (event.get("headers") or {}).get("x-api-key", "")  # HTTP API lower-cases header names
    return hmac.compare_digest(supplied.encode(), config.API_KEY.encode())


def _guard(fn):
    """Check the API key, and turn unexpected errors into a 500 without leaking details."""
    def wrapper(event, context=None):
        if fn.__name__ != "health" and not _authorised(event):
            return _resp(401, {"error": "unauthorized"})
        try:
            return fn(event, context)
        except Exception:
            logger.exception("unhandled error")
            db.reset()
            return _resp(500, {"error": "internal error"})
    wrapper.__name__ = fn.__name__
    return wrapper


def _int_param(event, name, default, lo, hi) -> int:
    raw = (event.get("queryStringParameters") or {}).get(name)
    try:
        return min(max(int(raw), lo), hi) if raw is not None else default
    except ValueError:
        return default


def _job_id(event) -> str | None:
    value = (event.get("pathParameters") or {}).get("job_id", "")
    return value if _UUID_RE.match(value) else None


@_guard
def health(event, context=None):
    _rows("SELECT 1")
    return _resp(200, {"status": "ok"})


@_guard
def create_upload(event, context=None):
    """Create a job and return a presigned S3 POST so the browser uploads directly to S3."""
    try:
        filename = str(json.loads(event.get("body") or "{}").get("filename", "")).strip()
    except json.JSONDecodeError:
        return _resp(400, {"error": "body must be JSON"})
    if not filename.lower().endswith(".csv"):
        return _resp(400, {"error": "filename must end with .csv"})

    job_id = str(uuid.uuid4())
    key = f"uploads/{job_id}.csv"
    db.get_conn().execute("INSERT INTO upload_jobs (id, filename, s3_key) VALUES (%s, %s, %s)",
                          (job_id, filename[:255], key))
    db.get_conn().commit()
    post = s3_public().generate_presigned_post(
        Bucket=config.UPLOAD_BUCKET, Key=key,
        Fields={"Content-Type": "text/csv"},
        Conditions=[{"Content-Type": "text/csv"}, ["content-length-range", 1, config.MAX_UPLOAD_BYTES]],
        ExpiresIn=config.PRESIGN_EXPIRY_SECONDS)
    return _resp(201, {"job_id": job_id, "upload": post, "max_bytes": config.MAX_UPLOAD_BYTES})


@_guard
def get_upload(event, context=None):
    job_id = _job_id(event)
    if not job_id:
        return _resp(400, {"error": "invalid job id"})
    jobs = _rows("SELECT * FROM upload_jobs WHERE id = %s", (job_id,))
    if not jobs:
        return _resp(404, {"error": "not found"})
    job = jobs[0]
    counts = _rows("SELECT count(*) AS matches, count(notified_at) AS notified "
                   "FROM matches WHERE upload_job_id = %s", (job_id,))[0]
    funnel = _rows("SELECT * FROM funnel_metrics WHERE upload_job_id = %s", (job_id,))
    error_url = None
    if job["error_key"]:
        error_url = s3_public().generate_presigned_url(
            "get_object", Params={"Bucket": config.UPLOAD_BUCKET, "Key": job["error_key"]},
            ExpiresIn=config.PRESIGN_EXPIRY_SECONDS)
    return _resp(200, {**{k: job[k] for k in (
        "id", "filename", "status", "total_rows", "valid_rows", "rejected_rows", "error_message",
        "matching_status", "created_at", "finished_at")},
        "matches": counts["matches"], "notified": counts["notified"],
        "funnel": funnel[0] if funnel else None, "error_report_url": error_url})


@_guard
def reprocess_upload(event, context=None):
    """"Process anyway": run the full pipeline again for a file that was rejected as a DUPLICATE.

    Creates a new job and copies the stored object to its key; the S3 event then drives the normal
    ingest path (with the duplicate check switched off for that job).
    """
    job_id = _job_id(event)
    if not job_id:
        return _resp(400, {"error": "invalid job id"})
    jobs = _rows("SELECT filename, status, s3_key FROM upload_jobs WHERE id = %s", (job_id,))
    if not jobs:
        return _resp(404, {"error": "not found"})
    if jobs[0]["status"] != "DUPLICATE":
        return _resp(409, {"error": "only a DUPLICATE upload can be processed again"})

    new_id = str(uuid.uuid4())
    key = f"uploads/{new_id}.csv"
    conn = db.get_conn()
    conn.execute("INSERT INTO upload_jobs (id, filename, s3_key, allow_duplicate) VALUES (%s, %s, %s, true)",
                 (new_id, jobs[0]["filename"], key))
    conn.commit()
    try:
        s3().copy_object(Bucket=config.UPLOAD_BUCKET, Key=key,
                         CopySource={"Bucket": config.UPLOAD_BUCKET, "Key": jobs[0]["s3_key"]})
    except Exception:
        conn.execute("UPDATE upload_jobs SET status = 'FAILED', error_message = 'could not copy the original file', "
                     "finished_at = now(), updated_at = now() WHERE id = %s", (new_id,))
        conn.commit()
        raise
    return _resp(201, {"job_id": new_id, "reprocessing": job_id})


@_guard
def list_uploads(event, context=None):
    rows = _rows("SELECT id, filename, status, matching_status, total_rows, valid_rows, rejected_rows, created_at "
                 "FROM upload_jobs ORDER BY created_at DESC LIMIT 20")
    return _resp(200, {"items": rows})


@_guard
def list_matches(event, context=None):
    job_id = _job_id(event)
    if not job_id:
        return _resp(400, {"error": "invalid job id"})
    limit = _int_param(event, "limit", 50, 1, 200)
    offset = _int_param(event, "offset", 0, 0, 10_000_000)
    total = _rows("SELECT count(*) AS n FROM matches WHERE upload_job_id = %s", (job_id,))[0]["n"]
    items = _rows(
        """
        SELECT u.user_id, u.name, u.email, p.product_name, p.source, p.interest_rate,
               m.score, m.match_stage, m.notified_at
        FROM matches m
        JOIN users u ON u.user_id = m.user_id
        JOIN loan_products p ON p.id = m.product_id
        WHERE m.upload_job_id = %s
        ORDER BY u.user_id, m.score DESC, p.id
        LIMIT %s OFFSET %s
        """, (job_id, limit, offset))
    return _resp(200, {"total": total, "limit": limit, "offset": offset, "items": items})
