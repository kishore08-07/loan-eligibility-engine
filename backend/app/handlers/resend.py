"""Re-send the 'users ready' webhook for a finished upload (n8n was down, DLQ'd message, re-run wanted).

  npx serverless invoke -f resendWebhook --data '{"job_id": "<uuid>"}'
"""
from app import db, webhook


def handler(event, context=None):
    job_id = (event or {}).get("job_id", "")
    conn = db.get_conn()
    row = conn.execute("SELECT total_rows, valid_rows, rejected_rows FROM upload_jobs "
                       "WHERE id::text = %s AND status LIKE 'COMPLETED%%'", (job_id,)).fetchone()
    conn.rollback()
    if not row:
        return {"error": "job not found or not completed"}
    webhook.send({"upload_job_id": job_id, "total_rows": row[0], "valid_rows": row[1], "rejected_rows": row[2]})
    conn.execute("UPDATE upload_jobs SET webhook_delivered_at = now() WHERE id::text = %s", (job_id,))
    conn.commit()
    return {"webhook": "delivered", "job_id": job_id}
