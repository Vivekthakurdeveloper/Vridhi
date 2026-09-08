from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any, Optional, Protocol
from uuid import UUID

import boto3
from botocore.exceptions import ClientError

from app.config import Settings

logger = logging.getLogger(__name__)


class IngestQueue(Protocol):
    def enqueue_ingest(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
        version_id: UUID,
    ) -> str: ...

    def enqueue_job(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        job_type: str,
        document_id: Optional[UUID] = None,
        version_id: Optional[UUID] = None,
    ) -> str: ...


class SqsIngestQueue:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = boto3.client(
            "sqs",
            endpoint_url=settings.sqs_endpoint_url or None,
            aws_access_key_id=settings.sqs_access_key_id,
            aws_secret_access_key=settings.sqs_secret_access_key,
            region_name=settings.sqs_region,
        )
        self._queue_url: Optional[str] = settings.sqs_queue_url

    def resolve_queue_url(self) -> str:
        if self._queue_url:
            return self._queue_url
        try:
            resp = self._client.get_queue_url(QueueName=self.settings.sqs_queue_name)
            self._queue_url = resp["QueueUrl"]
            return self._queue_url
        except ClientError as exc:
            raise RuntimeError(
                f"SQS queue '{self.settings.sqs_queue_name}' not found. "
                "Start LocalStack and run infra/localstack/init-aws.sh."
            ) from exc

    def enqueue_job(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        job_type: str,
        document_id: Optional[UUID] = None,
        version_id: Optional[UUID] = None,
    ) -> str:
        body: dict[str, Any] = {
            "job_id": str(job_id),
            "tenant_id": str(tenant_id),
            "job_type": job_type,
            "document_id": str(document_id) if document_id else None,
            "version_id": str(version_id) if version_id else None,
        }
        resp = self._client.send_message(
            QueueUrl=self.resolve_queue_url(),
            MessageBody=json.dumps(body),
        )
        message_id = resp["MessageId"]
        logger.info(
            "sqs.enqueued",
            extra={
                "operation": f"enqueue_{job_type}",
                "tenant_id": str(tenant_id),
                "request_id": str(job_id),
            },
        )
        return message_id

    def enqueue_ingest(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
        version_id: UUID,
    ) -> str:
        return self.enqueue_job(
            job_id=job_id,
            tenant_id=tenant_id,
            job_type="ingest",
            document_id=document_id,
            version_id=version_id,
        )


class DbIngestQueue:
    """Marks the job as queued in Postgres; worker polls sync_jobs."""

    def enqueue_job(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        job_type: str,
        document_id: Optional[UUID] = None,
        version_id: Optional[UUID] = None,
    ) -> str:
        _ = (tenant_id, document_id, version_id, job_type)
        message_id = f"db:{job_id}"
        logger.info(
            "db.enqueued",
            extra={"operation": f"enqueue_{job_type}", "request_id": str(job_id)},
        )
        return message_id

    def enqueue_ingest(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
        version_id: UUID,
    ) -> str:
        return self.enqueue_job(
            job_id=job_id,
            tenant_id=tenant_id,
            job_type="ingest",
            document_id=document_id,
            version_id=version_id,
        )


def build_ingest_queue(settings: Settings) -> IngestQueue:
    backend = settings.queue_backend.lower().strip()
    if backend == "db":
        return DbIngestQueue()
    if backend == "sqs":
        return SqsIngestQueue(settings)
    raise RuntimeError(f"Unknown QUEUE_BACKEND: {backend}")


@lru_cache
def get_ingest_queue() -> IngestQueue:
    from app.config import get_settings

    return build_ingest_queue(get_settings())
