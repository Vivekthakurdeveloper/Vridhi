from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.models import Chunk, Document, DocumentGrant, DocumentVersion, EmbeddingMeta, SyncJob
from app.security import DocumentStatus, SyncJobStatus
from worker.config import Settings
from worker.pipeline.index import SearchIndex
from worker.pipeline.process import (
    chunk_text,
    estimate_tokens,
    hash_embed,
    openai_embed,
    parse_bytes,
)
from worker.storage import ObjectStorage

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def process_ingest_job(
    db: Session,
    settings: Settings,
    storage: ObjectStorage,
    search: SearchIndex,
    *,
    job_id: UUID,
) -> None:
    job = db.get(SyncJob, job_id)
    if not job:
        logger.warning("job.missing", extra={"operation": "ingest", "request_id": str(job_id)})
        return

    if job.status == SyncJobStatus.succeeded:
        return

    job.attempt += 1
    job.status = SyncJobStatus.running
    job.started_at = utcnow()
    job.error_message = None
    db.commit()

    try:
        if not job.document_id or not job.version_id:
            raise RuntimeError("Job missing document_id or version_id")

        doc = db.scalar(
            select(Document)
            .where(Document.id == job.document_id)
            .options(selectinload(Document.grants))
        )
        version = db.get(DocumentVersion, job.version_id)
        if not doc or not version:
            raise RuntimeError("Document or version not found")

        doc.status = DocumentStatus.processing
        db.commit()

        raw = storage.get_bytes(version.storage_key)
        text = parse_bytes(version.original_filename, version.mime_type, raw)
        pieces = chunk_text(
            text,
            chunk_size=settings.chunk_size,
            overlap=settings.chunk_overlap,
            min_chars=settings.chunk_min_chars,
        )
        if not pieces:
            raise RuntimeError("No extractable text found in document")

        # Replace prior chunks/embeddings for this version.
        old_chunks = db.scalars(select(Chunk.id).where(Chunk.version_id == version.id)).all()
        if old_chunks:
            db.execute(delete(EmbeddingMeta).where(EmbeddingMeta.chunk_id.in_(old_chunks)))
            db.execute(delete(Chunk).where(Chunk.version_id == version.id))
            db.flush()

        search.ensure_index()
        search.delete_by_document(str(doc.tenant_id), str(doc.id))

        grant_ids = [str(g.user_id) for g in (doc.grants or [])]
        grant_group_ids = [str(g.group_id) for g in (doc.group_grants or [])]
        chunk_rows: list[Chunk] = []
        for idx, content in enumerate(pieces):
            row = Chunk(
                id=uuid4(),
                tenant_id=doc.tenant_id,
                document_id=doc.id,
                version_id=version.id,
                chunk_index=idx,
                content=content,
                token_count=estimate_tokens(content),
                metadata_={"filename": version.original_filename},
            )
            db.add(row)
            chunk_rows.append(row)
        db.flush()

        vectors = _embed_batch(settings, [c.content for c in chunk_rows])
        for row, vector in zip(chunk_rows, vectors):
            os_id = f"{doc.tenant_id}:{row.id}"
            search.index_chunk(
                os_id,
                {
                    "tenant_id": str(doc.tenant_id),
                    "document_id": str(doc.id),
                    "version_id": str(version.id),
                    "chunk_id": str(row.id),
                    "chunk_index": row.chunk_index,
                    "title": doc.title,
                    "content": row.content,
                    "visibility": doc.visibility.value,
                    "uploaded_by_user_id": str(doc.uploaded_by_user_id),
                    "granted_user_ids": grant_ids,
                    "granted_group_ids": grant_group_ids,
                    "source_url": getattr(doc, "source_url", None),
                    "embedding": vector,
                },
            )
            db.add(
                EmbeddingMeta(
                    id=uuid4(),
                    tenant_id=doc.tenant_id,
                    chunk_id=row.id,
                    document_id=doc.id,
                    version_id=version.id,
                    model=settings.embedding_model,
                    dimensions=settings.embedding_dimensions,
                    opensearch_id=os_id,
                )
            )

        doc.status = DocumentStatus.ready
        doc.error_message = None
        job.status = SyncJobStatus.succeeded
        job.finished_at = utcnow()
        db.commit()
        logger.info(
            "ingest.succeeded",
            extra={
                "operation": "ingest",
                "tenant_id": str(doc.tenant_id),
                "request_id": str(job.id),
            },
        )
    except Exception as exc:
        db.rollback()
        job = db.get(SyncJob, job_id)
        doc = db.get(Document, job.document_id) if job and job.document_id else None
        if job:
            job.error_message = str(exc)[:2000]
            job.finished_at = utcnow()
            if job.attempt >= job.max_attempts:
                job.status = SyncJobStatus.dead
            else:
                job.status = SyncJobStatus.failed
        if doc:
            doc.status = DocumentStatus.failed
            doc.error_message = str(exc)[:2000]
        db.commit()
        logger.exception(
            "ingest.failed",
            extra={"operation": "ingest", "request_id": str(job_id)},
        )
        raise


def _embed_batch(settings: Settings, texts: list[str]) -> list[list[float]]:
    provider = settings.embedding_provider.lower().strip()
    batch_size = max(1, settings.embedding_batch_size)
    out: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        if provider == "openai":
            out.extend(
                openai_embed(
                    batch,
                    api_key=settings.embedding_api_key,
                    api_base=settings.embedding_api_base,
                    model=settings.embedding_model,
                    dimensions=settings.embedding_dimensions,
                )
            )
        elif provider == "hash":
            out.extend(hash_embed(batch, settings.embedding_dimensions))
        else:
            raise RuntimeError(f"Unknown EMBEDDING_PROVIDER: {provider}")
    return out
