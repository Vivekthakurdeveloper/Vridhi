from __future__ import annotations

from typing import Annotated, Optional
from uuid import UUID

from fastapi import Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import RequestContext, require_tenant
from app.errors import AppError
from app.models import Document, DocumentVersion, SyncJob
from app.schemas import (
    DocumentOut,
    DocumentPreviewOut,
    DocumentsResponse,
    SyncJobOut,
    UploadResponse,
)
from app.security import DocumentVisibility
from app.services.documents import DocumentService
from app.services.queue import IngestQueue, get_ingest_queue
from app.services.storage import ObjectStorage, get_object_storage


def get_document_service(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    queue: Annotated[IngestQueue, Depends(get_ingest_queue)],
) -> DocumentService:
    return DocumentService(db, settings, storage, queue)


def _version_size(db: Session, doc: Document) -> Optional[int]:
    if not doc.current_version_id:
        return None
    version = db.get(DocumentVersion, doc.current_version_id)
    return version.byte_size if version else None


def _latest_job(db: Session, document_id: UUID) -> Optional[SyncJob]:
    from sqlalchemy import select

    return db.scalar(
        select(SyncJob)
        .where(SyncJob.document_id == document_id)
        .order_by(SyncJob.created_at.desc())
        .limit(1)
    )


def document_to_out(db: Session, doc: Document, job: Optional[SyncJob] = None) -> DocumentOut:
    job = job or _latest_job(db, doc.id)
    return DocumentOut(
        id=doc.id,
        title=doc.title,
        mime_type=doc.mime_type,
        source=doc.source,
        status=doc.status.value if hasattr(doc.status, "value") else str(doc.status),
        visibility=doc.visibility.value if hasattr(doc.visibility, "value") else str(doc.visibility),
        uploaded_by_user_id=doc.uploaded_by_user_id,
        job_id=job.id if job else None,
        job_status=job.status.value if job else None,
        error_message=doc.error_message,
        byte_size=_version_size(db, doc),
        granted_user_ids=[g.user_id for g in (doc.grants or [])],
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


def job_to_out(job: SyncJob) -> SyncJobOut:
    return SyncJobOut(
        id=job.id,
        connection_id=job.connection_id,
        document_id=job.document_id,
        version_id=job.version_id,
        job_type=job.job_type.value if hasattr(job.job_type, "value") else str(job.job_type),
        status=job.status.value if hasattr(job.status, "value") else str(job.status),
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        progress_total=getattr(job, "progress_total", 0) or 0,
        progress_done=getattr(job, "progress_done", 0) or 0,
        progress_failed=getattr(job, "progress_failed", 0) or 0,
        progress_skipped=getattr(job, "progress_skipped", 0) or 0,
        error_message=job.error_message,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


async def upload_document(
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    svc: Annotated[DocumentService, Depends(get_document_service)],
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
    visibility: str = Form(default="private"),
    selected_user_ids: str = Form(default=""),
) -> UploadResponse:
    assert ctx.membership and ctx.user
    try:
        vis = DocumentVisibility(visibility)
    except ValueError as exc:
        raise AppError(
            "INVALID_VISIBILITY",
            "Visibility must be private, org, or selected.",
            400,
        ) from exc

    user_ids: list[UUID] = []
    raw = selected_user_ids.strip()
    if raw:
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                user_ids.append(UUID(part))
            except ValueError as exc:
                raise AppError("INVALID_SELECTED_USERS", "Invalid user id in selected_user_ids.", 400) from exc

    data = await file.read()
    filename = file.filename or "upload.bin"
    doc, job = svc.upload(
        tenant_id=ctx.membership.tenant_id,
        user_id=ctx.user.id,
        filename=filename,
        content_type=file.content_type,
        data=data,
        visibility=vis,
        selected_user_ids=user_ids,
        request_id=ctx.request_id,
    )
    return UploadResponse(document=document_to_out(db, doc, job), job=job_to_out(job))


def list_documents_handler(
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    svc: Annotated[DocumentService, Depends(get_document_service)],
    db: Annotated[Session, Depends(get_db)],
    page: int = 1,
    limit: int = 20,
) -> DocumentsResponse:
    assert ctx.membership and ctx.user
    rows, total = svc.list_documents(
        tenant_id=ctx.membership.tenant_id,
        user_id=ctx.user.id,
        role=ctx.membership.role,
        page=page,
        limit=limit,
    )
    return DocumentsResponse(
        items=[document_to_out(db, row) for row in rows],
        total=total,
        page=page,
        limit=limit,
    )


def get_document_handler(
    document_id: UUID,
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    svc: Annotated[DocumentService, Depends(get_document_service)],
    db: Annotated[Session, Depends(get_db)],
) -> DocumentOut:
    assert ctx.membership and ctx.user
    doc = svc.get_document(
        document_id=document_id,
        tenant_id=ctx.membership.tenant_id,
        user_id=ctx.user.id,
        role=ctx.membership.role,
    )
    return document_to_out(db, doc)


def delete_document_handler(
    document_id: UUID,
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    svc: Annotated[DocumentService, Depends(get_document_service)],
) -> dict:
    assert ctx.membership and ctx.user
    svc.delete_document(
        document_id=document_id,
        tenant_id=ctx.membership.tenant_id,
        user_id=ctx.user.id,
        role=ctx.membership.role,
        request_id=ctx.request_id,
    )
    return {"ok": True}


def preview_document_handler(
    document_id: UUID,
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    svc: Annotated[DocumentService, Depends(get_document_service)],
) -> DocumentPreviewOut:
    assert ctx.membership and ctx.user
    data = svc.preview(
        document_id=document_id,
        tenant_id=ctx.membership.tenant_id,
        user_id=ctx.user.id,
        role=ctx.membership.role,
    )
    return DocumentPreviewOut(**data)


def get_job_handler(
    job_id: UUID,
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    svc: Annotated[DocumentService, Depends(get_document_service)],
) -> SyncJobOut:
    assert ctx.membership and ctx.user
    job = svc.get_job(
        job_id=job_id,
        tenant_id=ctx.membership.tenant_id,
        user_id=ctx.user.id,
        role=ctx.membership.role,
    )
    return job_to_out(job)


def get_document_job_handler(
    document_id: UUID,
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    svc: Annotated[DocumentService, Depends(get_document_service)],
) -> SyncJobOut:
    assert ctx.membership and ctx.user
    job = svc.latest_job_for_document(
        document_id=document_id,
        tenant_id=ctx.membership.tenant_id,
        user_id=ctx.user.id,
        role=ctx.membership.role,
    )
    if not job:
        raise AppError("JOB_NOT_FOUND", "No job found for this document.", 404)
    return job_to_out(job)
