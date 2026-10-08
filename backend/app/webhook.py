"""Signed webhook delivery to n8n Workflow B."""
import hashlib
import hmac
import json
import time
import urllib.request

from app import config


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def send(payload: dict) -> None:
    """POST the payload with an HMAC signature. Raises on any non-2xx / network error."""
    if not config.N8N_MATCH_WEBHOOK_URL or not config.WEBHOOK_SECRET:
        raise RuntimeError("N8N_MATCH_WEBHOOK_URL and WEBHOOK_SECRET must be set")
    body = json.dumps(payload).encode()
    ts = str(int(time.time()))
    req = urllib.request.Request(
        config.N8N_MATCH_WEBHOOK_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Timestamp": ts,
                 "X-Signature": "sha256=" + sign(config.WEBHOOK_SECRET, ts, body)})
    with urllib.request.urlopen(req, timeout=config.WEBHOOK_TIMEOUT_SECONDS) as resp:
        if not 200 <= resp.status < 300:
            raise RuntimeError(f"webhook returned {resp.status}")
