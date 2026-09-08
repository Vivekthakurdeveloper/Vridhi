from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO, Optional, Protocol

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.config import Settings

logger = logging.getLogger(__name__)


class ObjectStorage(Protocol):
    def ensure_bucket(self) -> None: ...
    def put_bytes(self, key: str, body: bytes, content_type: Optional[str] = None) -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def object_key(self, tenant_id: str, document_id: str, version_id: str, filename: str) -> str: ...


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

    def ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError:
            params: dict = {"Bucket": self.bucket}
            if self.settings.s3_region != "us-east-1":
                params["CreateBucketConfiguration"] = {
                    "LocationConstraint": self.settings.s3_region
                }
            self._client.create_bucket(**params)
            logger.info("s3.bucket_created", extra={"operation": "s3_ensure_bucket"})

    def put_bytes(self, key: str, body: bytes, content_type: Optional[str] = None) -> None:
        extra = {}
        if content_type:
            extra["ContentType"] = content_type
        self._client.put_object(Bucket=self.bucket, Key=key, Body=body, **extra)

    def put_fileobj(self, key: str, fileobj: BinaryIO, content_type: Optional[str] = None) -> None:
        extra = {}
        if content_type:
            extra["ContentType"] = content_type
        self._client.upload_fileobj(fileobj, self.bucket, key, ExtraArgs=extra or None)

    def get_bytes(self, key: str) -> bytes:
        obj = self._client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def object_key(self, tenant_id: str, document_id: str, version_id: str, filename: str) -> str:
        prefix = self.settings.s3_prefix.strip("/")
        safe_name = filename.replace("/", "_")
        return f"{prefix}/{tenant_id}/{document_id}/{version_id}/{safe_name}"


class FilesystemObjectStorage:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = Path(settings.storage_local_path)
        self.root.mkdir(parents=True, exist_ok=True)

    def ensure_bucket(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, key: str, body: bytes, content_type: Optional[str] = None) -> None:
        _ = content_type
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    def get_bytes(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def delete(self, key: str) -> None:
        path = self.root / key
        if path.exists():
            path.unlink()

    def object_key(self, tenant_id: str, document_id: str, version_id: str, filename: str) -> str:
        prefix = self.settings.s3_prefix.strip("/")
        safe_name = filename.replace("/", "_")
        return f"{prefix}/{tenant_id}/{document_id}/{version_id}/{safe_name}"


def build_object_storage(settings: Settings) -> ObjectStorage:
    backend = settings.storage_backend.lower().strip()
    if backend == "filesystem":
        return FilesystemObjectStorage(settings)
    if backend == "s3":
        return S3ObjectStorage(settings)
    raise RuntimeError(f"Unknown STORAGE_BACKEND: {backend}")


@lru_cache
def get_object_storage() -> ObjectStorage:
    from app.config import get_settings

    return build_object_storage(get_settings())
