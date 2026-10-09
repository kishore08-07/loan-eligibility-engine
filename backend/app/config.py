"""Environment-driven settings. Local and AWS differ only in env values."""
import os
from urllib.parse import quote


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def database_url() -> str:
    url = _env("DATABASE_URL")
    if url:
        return url
    host, name = _env("DB_HOST"), _env("DB_NAME", "loans")
    user, password = _env("DB_USER"), _env("DB_PASSWORD")
    if not (host and user and password):
        raise RuntimeError("Set DATABASE_URL or DB_HOST/DB_USER/DB_PASSWORD")
    # RDS (Postgres 15+) enforces TLS; "require" encrypts, set DB_SSLMODE=verify-full + CA bundle to also verify.
    return (f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{_env('DB_PORT', '5432')}/{name}"
            f"?sslmode={_env('DB_SSLMODE', 'require')}")


# Internal endpoint (Lambda/worker -> S3). Unset on AWS (default endpoint).
AWS_ENDPOINT_URL = _env("AWS_ENDPOINT_URL")
# Endpoint embedded in presigned URLs; must be reachable by the browser.
S3_PUBLIC_ENDPOINT_URL = _env("S3_PUBLIC_ENDPOINT_URL", AWS_ENDPOINT_URL)
UPLOAD_BUCKET = _env("UPLOAD_BUCKET", "loan-engine-uploads")
MAX_UPLOAD_BYTES = int(_env("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
PRESIGN_EXPIRY_SECONDS = int(_env("PRESIGN_EXPIRY_SECONDS", "900"))

# Webhook that starts n8n Workflow B.
N8N_MATCH_WEBHOOK_URL = _env("N8N_MATCH_WEBHOOK_URL")
WEBHOOK_SECRET = _env("WEBHOOK_SECRET")
WEBHOOK_TIMEOUT_SECONDS = int(_env("WEBHOOK_TIMEOUT_SECONDS", "10"))

# Optional shared secret for the HTTP API (x-api-key header). Unset = open API (local dev).
API_KEY = _env("API_KEY")

# Housekeeping (handlers/maintenance.py): a PENDING job whose file never arrived is expired after this long
# (> the presigned-POST lifetime). A PROCESSING job is only failed after SQS has had its chance to retry:
# MAX_RECEIVE_COUNT x queue visibility timeout (30 min) plus a margin.
PENDING_EXPIRY_MINUTES = int(_env("PENDING_EXPIRY_MINUTES", "60"))
PROCESSING_EXPIRY_MINUTES = int(_env("PROCESSING_EXPIRY_MINUTES", "100"))

# After this many SQS deliveries the job is marked FAILED (matches the DLQ maxReceiveCount).
MAX_RECEIVE_COUNT = int(_env("MAX_RECEIVE_COUNT", "3"))
