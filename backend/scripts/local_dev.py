"""Local stand-in for API Gateway + Lambda's SQS event source mapping.

* HTTP server on :8000 translates requests into API Gateway HTTP API (v2) events and calls
  the real handlers in app/handlers/api.py.
* A poller thread reads the ingest SQS queue and calls app/handlers/ingest.py with a real SQS event.
"""
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import _env

_env.load()

import boto3  # noqa: E402

from app import config  # noqa: E402
from app.handlers import api, ingest  # noqa: E402

ROUTES = [
    ("GET", r"^/health$", api.health, []),
    ("POST", r"^/uploads$", api.create_upload, []),
    ("GET", r"^/uploads$", api.list_uploads, []),
    ("GET", r"^/uploads/(?P<job_id>[^/]+)$", api.get_upload, ["job_id"]),
    ("GET", r"^/uploads/(?P<job_id>[^/]+)/matches$", api.list_matches, ["job_id"]),
    ("POST", r"^/uploads/(?P<job_id>[^/]+)/reprocess$", api.reprocess_upload, ["job_id"]),
]


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")

    def _dispatch(self, method):
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else None
        for route_method, pattern, fn, _ in ROUTES:
            match = re.match(pattern, url.path)
            if route_method == method and match:
                event = {"rawPath": url.path, "body": body,
                         "headers": {k.lower(): v for k, v in self.headers.items()},
                         "pathParameters": match.groupdict(),
                         "queryStringParameters": {k: v[0] for k, v in parse_qs(url.query).items()},
                         "requestContext": {"http": {"method": method}}}
                result = fn(event, None)
                payload = result["body"].encode()
                self.send_response(result["statusCode"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self._cors()
                self.end_headers()
                self.wfile.write(payload)
                return
        self.send_response(404)
        self._cors()
        self.end_headers()

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def log_message(self, fmt, *args):
        print("api:", fmt % args, flush=True)


def poll_queue():
    sqs = boto3.client("sqs", endpoint_url=config.AWS_ENDPOINT_URL)
    queue_url = sqs.get_queue_url(QueueName=os.environ.get("INGEST_QUEUE_NAME", "loan-engine-ingest"))["QueueUrl"]
    print("worker: polling", queue_url, flush=True)
    while True:
        try:
            resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=5,
                                       AttributeNames=["ApproximateReceiveCount"])
            for msg in resp.get("Messages", []):
                record = {"messageId": msg["MessageId"], "body": msg["Body"],
                          "attributes": msg.get("Attributes", {})}
                result = ingest.handler({"Records": [record]})
                if not result["batchItemFailures"]:
                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
        except Exception as exc:  # keep the worker alive
            print("worker error:", exc, flush=True)


if __name__ == "__main__":
    threading.Thread(target=poll_queue, daemon=True).start()
    port = int(os.environ.get("API_PORT", "8000"))
    print(f"api: listening on http://localhost:{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
