from __future__ import annotations

import json
import logging
import time
from uuid import UUID

import boto3
from botocore.exceptions import ClientError
from sqlalchemy import select

from app.models import SyncJob
from app.security import SyncJobStatus, SyncJobType
from worker.config import Settings, get_settings
from worker.db import SessionLocal
from worker.drive_sync import process_drive_sync_job
from worker.ingest import process_ingest_job
from worker.pipeline.index import build_search_index
from worker.storage import build_object_storage

logger = logging.getLogger(__name__)


def _dispatch_job(db, settings: Settings, storage, search, *, job_id: UUID, job_type: str | None) -> None:
    resolved = job_type
    if not resolved:
        job = db.get(SyncJob, job_id)
        resolved = job.job_type.value if job and hasattr(job.job_type, "value") else (str(job.job_type) if job else "ingest")

    if resolved == SyncJobType.drive_sync.value or resolved == "drive_sync":
        process_drive_sync_job(db, job_id=job_id)
        return

    process_ingest_job(db, settings, storage, search, job_id=job_id)


class IngestConsumer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.storage = build_object_storage(settings)
        self.search = build_search_index(
            backend=settings.search_backend,
            url=settings.opensearch_url,
            index=settings.opensearch_index,
            username=settings.opensearch_username,
            password=settings.opensearch_password,
            use_ssl=settings.opensearch_use_ssl,
            verify_certs=settings.opensearch_verify_certs,
            dimensions=settings.embedding_dimensions,
        )
        self.queue_backend = settings.queue_backend.lower().strip()
        self._queue_url = settings.sqs_queue_url
        self.sqs = None
        if self.queue_backend == "sqs":
            self.sqs = boto3.client(
                "sqs",
                endpoint_url=settings.sqs_endpoint_url or None,
                aws_access_key_id=settings.sqs_access_key_id,
                aws_secret_access_key=settings.sqs_secret_access_key,
                region_name=settings.sqs_region,
            )

    def queue_url(self) -> str:
        assert self.sqs is not None
        if self._queue_url:
            return self._queue_url
        resp = self.sqs.get_queue_url(QueueName=self.settings.sqs_queue_name)
        self._queue_url = resp["QueueUrl"]
        return self._queue_url

    def run_forever(self) -> None:
        logger.info(
            "worker.started",
            extra={"operation": "worker_start", "request_id": self.queue_backend},
        )
        while True:
            try:
                if self.queue_backend == "db":
                    self.poll_db_once()
                else:
                    self.poll_sqs_once()
            except Exception:
                logger.exception("worker.poll_error", extra={"operation": "poll"})
                time.sleep(self.settings.worker_poll_interval_seconds)

    def poll_db_once(self) -> None:
        with SessionLocal() as db:
            jobs = db.scalars(
                select(SyncJob)
                .where(SyncJob.status == SyncJobStatus.queued)
                .order_by(SyncJob.created_at.asc())
                .limit(self.settings.sqs_max_messages)
            ).all()
            queued = [(job.id, job.job_type.value if hasattr(job.job_type, "value") else str(job.job_type)) for job in jobs]
        if not queued:
            time.sleep(self.settings.worker_poll_interval_seconds)
            return
        for job_id, job_type in queued:
            try:
                with SessionLocal() as db:
                    _dispatch_job(
                        db,
                        self.settings,
                        self.storage,
                        self.search,
                        job_id=job_id,
                        job_type=job_type,
                    )
            except Exception:
                logger.exception("worker.message_failed", extra={"operation": job_type})

    def poll_sqs_once(self) -> None:
        assert self.sqs is not None
        try:
            resp = self.sqs.receive_message(
                QueueUrl=self.queue_url(),
                MaxNumberOfMessages=self.settings.sqs_max_messages,
                WaitTimeSeconds=self.settings.sqs_wait_time_seconds,
                VisibilityTimeout=self.settings.sqs_visibility_timeout,
            )
        except ClientError:
            logger.exception("sqs.receive_failed", extra={"operation": "poll"})
            time.sleep(self.settings.worker_poll_interval_seconds)
            return

        messages = resp.get("Messages") or []
        if not messages:
            return

        for message in messages:
            receipt = message["ReceiptHandle"]
            try:
                body = json.loads(message["Body"])
                job_id = UUID(body["job_id"])
                job_type = body.get("job_type")
                with SessionLocal() as db:
                    _dispatch_job(
                        db,
                        self.settings,
                        self.storage,
                        self.search,
                        job_id=job_id,
                        job_type=job_type,
                    )
                self.sqs.delete_message(QueueUrl=self.queue_url(), ReceiptHandle=receipt)
            except Exception:
                logger.exception("worker.message_failed", extra={"operation": "dispatch"})


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    IngestConsumer(settings).run_forever()


if __name__ == "__main__":
    main()
