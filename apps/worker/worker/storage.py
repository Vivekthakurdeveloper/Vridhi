from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Protocol

import boto3
from botocore.client import Config

from worker.config import Settings

logger = logging.getLogger(__name__)


class ObjectStorage(Protocol):
    def get_bytes(self, key: str) -> bytes: ...


class S3ObjectStorage:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
            config=Config(s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"}),
        )

    def get_bytes(self, key: str) -> bytes:
        obj = self._client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()


class FilesystemObjectStorage:
    def __init__(self, settings: Settings):
        self.root = Path(settings.storage_local_path)

    def get_bytes(self, key: str) -> bytes:
        return (self.root / key).read_bytes()


def build_object_storage(settings: Settings) -> ObjectStorage:
    backend = settings.storage_backend.lower().strip()
    if backend == "filesystem":
        return FilesystemObjectStorage(settings)
    if backend == "s3":
        return S3ObjectStorage(settings)
    raise RuntimeError(f"Unknown STORAGE_BACKEND: {backend}")
