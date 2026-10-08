"""SQS-triggered Lambda: one message per uploaded file (S3 ObjectCreated notification)."""
import json
import urllib.parse

from app import config, db
from app.ingestion import PermanentError, process_upload
from app.log import get_logger, log

logger = get_logger("handler.ingest")


def handler(event, context=None):
    """Returns SQS partial-batch-failure response: only failed messages are retried."""
    failures = []
    for record in event.get("Records", []):
        receive_count = int(record.get("attributes", {}).get("ApproximateReceiveCount", "1"))
        try:
            for s3_record in json.loads(record["body"]).get("Records", []):  # s3:TestEvent has none
                bucket = s3_record["s3"]["bucket"]["name"]
                key = urllib.parse.unquote_plus(s3_record["s3"]["object"]["key"])
                result = process_upload(db.get_conn(), bucket, key,
                                        final_attempt=receive_count >= config.MAX_RECEIVE_COUNT)
                log(logger, "processed", key=key, job_id=result.job_id, status=result.status)
        except PermanentError as exc:
            log(logger, "dropping message", error=str(exc))
        except Exception:
            logger.exception("ingest failed; message will be retried (receive_count=%s)", receive_count)
            db.reset()
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
