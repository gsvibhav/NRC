"""Builds the boto3 S3 client from validated Config.

Kept separate from S3Storage so tests can inject a fake/mocked client
directly into S3Storage without ever constructing a real boto3 client.
"""

from __future__ import annotations

import boto3

from ..config import Config


def build_s3_client(config: Config):
    return boto3.client(
        "s3",
        region_name=config.aws_region,
        aws_access_key_id=config.aws_access_key_id,
        aws_secret_access_key=config.aws_secret_access_key,
    )
