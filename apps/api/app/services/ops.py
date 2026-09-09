from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    Chunk,
    Connection,
    Conversation,
    Document,
    EmbeddingMeta,
    AnswerFeedback,
    SyncJob,
)
from app.security import DocumentStatus, SyncJobStatus


def collect_metrics(db: Session, settings: Settings) -> dict[str, Any]:
    doc_rows = db.execute(
        select(Document.status, func.count())
        .where(Document.deleted_at.is_(None))
        .group_by(Document.status)
    ).all()
    job_rows = db.execute(select(SyncJob.status, func.count()).group_by(SyncJob.status)).all()

    def _status_map(rows: list) -> dict[str, int]:
        out: dict[str, int] = {}
        for status, count in rows:
            key = status.value if hasattr(status, "value") else str(status)
            out[key] = int(count)
        return out

    return {
        "documents_by_status": _status_map(doc_rows),
        "jobs_by_status": _status_map(job_rows),
        "chunks": int(db.scalar(select(func.count()).select_from(Chunk)) or 0),
        "conversations": int(db.scalar(select(func.count()).select_from(Conversation)) or 0),
        "feedback": int(db.scalar(select(func.count()).select_from(AnswerFeedback)) or 0),
        "connections": int(db.scalar(select(func.count()).select_from(Connection)) or 0),
        "queue_backend": settings.queue_backend,
        "search_backend": settings.search_backend,
    }


def orphan_report(db: Session, *, tenant_id: UUID | None = None, limit: int = 20) -> dict[str, Any]:
    doc_q = select(Document.id).where(
        Document.status == DocumentStatus.ready,
        Document.deleted_at.is_(None),
        ~Document.id.in_(select(Chunk.document_id).where(Chunk.document_id.is_not(None))),
    )
    if tenant_id:
        doc_q = doc_q.where(Document.tenant_id == tenant_id)
    ready_without_chunks = list(db.scalars(doc_q.limit(limit)).all())

    chunk_without_emb = int(
        db.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(
                ~Chunk.id.in_(select(EmbeddingMeta.chunk_id).where(EmbeddingMeta.chunk_id.is_not(None)))
            )
        )
        or 0
    )
    emb_without_chunk = int(
        db.scalar(
            select(func.count())
            .select_from(EmbeddingMeta)
            .where(~EmbeddingMeta.chunk_id.in_(select(Chunk.id)))
        )
        or 0
    )
    failed_q = select(func.count()).select_from(Document).where(
        Document.status == DocumentStatus.failed,
        Document.deleted_at.is_(None),
    )
    if tenant_id:
        failed_q = failed_q.where(Document.tenant_id == tenant_id)
    dead_q = select(func.count()).select_from(SyncJob).where(SyncJob.status == SyncJobStatus.dead)
    if tenant_id:
        dead_q = dead_q.where(SyncJob.tenant_id == tenant_id)

    return {
        "ready_docs_without_chunks": len(ready_without_chunks),
        "chunks_without_embeddings": chunk_without_emb,
        "embeddings_without_chunks": emb_without_chunk,
        "failed_documents": int(db.scalar(failed_q) or 0),
        "dead_jobs": int(db.scalar(dead_q) or 0),
        "sample_document_ids": ready_without_chunks,
    }
