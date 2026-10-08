"""Create the local AWS resources in moto (bucket, queue + DLQ, S3->SQS notification, CORS)
and apply DB migrations + seed products. Safe to re-run."""
import json
import os

import _env

_env.load()

import boto3  # noqa: E402

from app import config, migrate  # noqa: E402
import seed_products  # noqa: E402

endpoint = config.AWS_ENDPOINT_URL
s3 = boto3.client("s3", endpoint_url=endpoint)
sqs = boto3.client("sqs", endpoint_url=endpoint)

bucket = config.UPLOAD_BUCKET
queue_name = os.environ.get("INGEST_QUEUE_NAME", "loan-engine-ingest")

dlq_url = sqs.create_queue(QueueName=f"{queue_name}-dlq")["QueueUrl"]
dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
queue_url = sqs.create_queue(QueueName=queue_name, Attributes={
    "VisibilityTimeout": "30",
    "RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": config.MAX_RECEIVE_COUNT}),
})["QueueUrl"]
queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]

s3.create_bucket(Bucket=bucket)
s3.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": [{
    "AllowedOrigins": ["*"], "AllowedMethods": ["POST", "GET"], "AllowedHeaders": ["*"]}]})
s3.put_bucket_notification_configuration(Bucket=bucket, NotificationConfiguration={"QueueConfigurations": [{
    "QueueArn": queue_arn, "Events": ["s3:ObjectCreated:*"],
    "Filter": {"Key": {"FilterRules": [{"Name": "prefix", "Value": "uploads/"}]}}}]})
print("aws resources ready:", bucket, queue_url)

print("migrations applied:", migrate.run() or "none (up to date)")
print("seed products:", seed_products.seed())
