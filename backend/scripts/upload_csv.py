"""CLI equivalent of the UI: upload a CSV through the presigned-URL flow and follow the job.

usage: python scripts/upload_csv.py ../users.csv [--api http://localhost:8000]
"""
import argparse
import json
import mimetypes
import pathlib
import time
import urllib.request
import uuid


def call(method, url, data=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def post_form(url, fields, filename, content):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                  f'Content-Type: text/csv\r\n\r\n').encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(url, data=b"".join(parts), method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()
    path = pathlib.Path(args.csv)

    created = call("POST", f"{args.api}/uploads", {"filename": path.name})
    job_id = created["job_id"]
    print("job", job_id)
    print("s3 upload status", post_form(created["upload"]["url"], created["upload"]["fields"],
                                        path.name, path.read_bytes()))
    deadline, last = time.time() + args.timeout, None
    while time.time() < deadline:
        job = call("GET", f"{args.api}/uploads/{job_id}")
        state = (job["status"], job["matching_status"], job["matches"], job["notified"])
        if state != last:
            print(f"status={job['status']} rows={job['valid_rows']}/{job['total_rows']} "
                  f"rejected={job['rejected_rows']} matching={job['matching_status']} "
                  f"matches={job['matches']} notified={job['notified']}")
            last = state
        if job["status"] in ("FAILED", "DUPLICATE") or (
                job["matching_status"] in ("DONE", "FAILED") and job["status"].startswith("COMPLETED")):
            print(json.dumps(job, indent=2))
            return
        time.sleep(2)
    print("timed out waiting for job")


if __name__ == "__main__":
    main()
