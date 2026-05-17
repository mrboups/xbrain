"""MinIO/S3 client init — fail-soft if MinIO credentials not configured.

Phase 11 plan 11-10: used by the superadmin storage metric endpoint to
report bytes per team. The lazy lru_cache singleton keeps memory-api
startup fast and avoids a hard dependency on MinIO when the operator
has not yet wired the credentials.

Fail-soft contract: returns None if MINIO_URL or credentials are empty,
or if boto3 cannot be imported, or if client instantiation raises. The
caller MUST handle the None case — repos/brain_metrics.py:get_storage_per_team
returns `minio_bytes=None` in that case and the UI in 11-11 displays "N/A".
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_minio_client() -> Any | None:
    """Return an S3-compatible boto3 client or None if MinIO is not configured.

    The first call constructs the client; subsequent calls return the same
    instance. Pass `get_minio_client.cache_clear()` in tests that need to
    re-evaluate (e.g. after monkey-patching the settings).
    """
    if not settings.MINIO_URL or not settings.MINIO_ACCESS_KEY or not settings.MINIO_SECRET_KEY:
        logger.info("minio_client_disabled: MINIO_URL or credentials empty")
        return None
    try:
        import boto3
        from botocore.config import Config as BotoConfig
        return boto3.client(
            "s3",
            endpoint_url=settings.MINIO_URL,
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY,
            config=BotoConfig(signature_version="s3v4"),
        )
    except Exception as exc:
        logger.warning("minio_client_init_failed error=%s", exc)
        return None
