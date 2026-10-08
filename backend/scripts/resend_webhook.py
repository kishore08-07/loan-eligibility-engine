"""Re-send the 'users ready' webhook for a finished upload (e.g. after the message hit the DLQ).

usage: python scripts/resend_webhook.py <job_id>
"""
import sys

import _env

_env.load()

from app import db, webhook  # noqa: E402

job_id = sys.argv[1]
row = db.get_conn().execute(
    "SELECT total_rows, valid_rows, rejected_rows FROM upload_jobs WHERE id = %s AND status LIKE 'COMPLETED%%'",
    (job_id,)).fetchone()
if not row:
    sys.exit("job not found or not completed")
webhook.send({"upload_job_id": job_id, "total_rows": row[0], "valid_rows": row[1], "rejected_rows": row[2]})
db.get_conn().execute("UPDATE upload_jobs SET webhook_delivered_at = now() WHERE id = %s", (job_id,))
db.get_conn().commit()
print("webhook delivered")
