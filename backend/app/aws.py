"""boto3 clients. Endpoint overrides make the same code run against moto locally."""
import os

import boto3
from botocore.config import Config

from app import config

_s3 = None
_s3_public = None


def s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", endpoint_url=config.AWS_ENDPOINT_URL,
                           config=Config(signature_version="s3v4"))
    return _s3


def s3_public():
    """Client used only to sign URLs handed to the browser."""
    global _s3_public
    if _s3_public is None:
        endpoint = config.S3_PUBLIC_ENDPOINT_URL
        region = os.environ.get("AWS_REGION")
        if not endpoint and region:
            # The global endpoint answers 307 for buckets outside us-east-1, which breaks browser uploads.
            endpoint = f"https://s3.{region}.amazonaws.com"
        _s3_public = boto3.client("s3", endpoint_url=endpoint, region_name=region,
                                  config=Config(signature_version="s3v4"))
    return _s3_public
