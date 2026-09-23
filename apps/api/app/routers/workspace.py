from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import RequestContext, require_role, require_tenant
from app.errors import AppError
from app.models import AuditEvent, OrganizationMember
from app.routers.documents import (
    delete_document_handler,
    get_document_handler,
    get_document_job_handler,
    get_job_handler,
    list_documents_handler,
    preview_document_handler,
    upload_document,
    get_document_service,
)
from app.schemas import (
    AuditEventOut,
    AuditResponse,
    ConnectorOut,
    ConnectorsResponse,
    DashboardActivityOut,
    DashboardOut,
    DocumentOut,
    DocumentPreviewOut,
    DocumentsResponse,
    FeaturesOut,
    SyncJobOut,
    UploadResponse,
    UsageOut,
)
from app.security import MemberRole, MemberStatus
from app.services.chat import ChatService
from app.services.documents import DocumentService
from app.services.drive import DriveService
from app.services.gmail import GmailService
from app.services.queue import get_ingest_queue
from app.services.storage import get_object_storage
from app.services.tokens import get_token_store

router = APIRouter()


def _drive_service(db: Session, settings: Settings) -> DriveService:
    return DriveService(
        db,
        settings,
        get_token_store(),
        get_object_storage(),
        get_ingest_queue(),
    )


def _gmail_service(db: Session, settings: Settings) -> GmailService:
    return GmailService(
        db,
        settings,
        get_token_store(),
        get_object_storage(),
        get_ingest_queue(),
    )


def _chat_service(db: Session, settings: Settings) -> ChatService:
    return ChatService(
        db,
        settings,
        get_token_store(),
        get_object_storage(),
        get_ingest_queue(),
    )


def _connector_catalog(
    settings: Settings,
    drive_detail: dict | None = None,
    gmail_detail: dict | None = None,
    chat_detail: dict | None = None,
) -> list[dict]:
    file_ready = settings.file_upload_ready
    drive_ready = settings.google_drive_ready
    drive_status = "not_implemented"
    drive_enabled = False
    drive_extra: dict = {}
    if settings.google_drive_enabled:
        drive_enabled = drive_ready
        if not drive_ready:
            drive_status = "not_configured"
        elif drive_detail and drive_detail.get("connected"):
            drive_status = str(drive_detail.get("status") or "connected")
            drive_extra = {
                "last_sync_at": drive_detail.get("last_sync_at"),
                "document_count": drive_detail.get("document_count"),
                "failed_document_count": drive_detail.get("failed_document_count"),
                "health": drive_detail.get("health"),
                "account_email": drive_detail.get("account_email"),
                "mode": drive_detail.get("mode"),
                "auto_sync_enabled": drive_detail.get("auto_sync_enabled"),
                "auto_sync_paused_reason": drive_detail.get("auto_sync_paused_reason"),
            }
        else:
            drive_status = "available"
            drive_extra = {"mode": settings.google_drive_mode}

    gmail_ready = settings.gmail_ready
    gmail_status = "not_implemented"
    gmail_enabled = False
    gmail_extra: dict = {}
    if settings.gmail_enabled:
        gmail_enabled = gmail_ready
        if not gmail_ready:
            gmail_status = "not_configured"
        elif gmail_detail and gmail_detail.get("connected"):
            gmail_status = str(gmail_detail.get("status") or "connected")
            gmail_extra = {
                "last_sync_at": gmail_detail.get("last_sync_at"),
                "document_count": gmail_detail.get("document_count"),
                "failed_document_count": gmail_detail.get("failed_document_count"),
                "health": gmail_detail.get("health"),
                "account_email": gmail_detail.get("account_email"),
                "mode": gmail_detail.get("mode"),
                "auto_sync_enabled": gmail_detail.get("auto_sync_enabled"),
                "auto_sync_paused_reason": gmail_detail.get("auto_sync_paused_reason"),
            }
        else:
            gmail_status = "available"
            gmail_extra = {"mode": settings.gmail_mode}

    chat_ready = settings.google_chat_ready
    chat_status = "not_implemented"
    chat_enabled = False
    chat_extra: dict = {}
    if settings.google_chat_enabled:
        chat_enabled = chat_ready
        if not chat_ready:
            chat_status = "not_configured"
        elif chat_detail and chat_detail.get("connected"):
            chat_status = str(chat_detail.get("status") or "connected")
            chat_extra = {
                "last_sync_at": chat_detail.get("last_sync_at"),
                "document_count": chat_detail.get("document_count"),
                "failed_document_count": chat_detail.get("failed_document_count"),
                "health": chat_detail.get("health"),
                "account_email": chat_detail.get("account_email"),
                "mode": chat_detail.get("mode"),
                "auto_sync_enabled": chat_detail.get("auto_sync_enabled"),
                "auto_sync_paused_reason": chat_detail.get("auto_sync_paused_reason"),
            }
        else:
            chat_status = "available"
            chat_extra = {"mode": settings.google_chat_mode}

    return [
        {
            "id": "google_drive",
            "name": "Google Drive",
            "description": "Search and ask questions about files in your Drive.",
            "status": drive_status,
            "enabled": drive_enabled,
            **drive_extra,
        },
        {
            "id": "gmail",
            "name": "Gmail",
            "description": "Index email attachments for company knowledge.",
            "status": gmail_status,
            "enabled": gmail_enabled,
            **gmail_extra,
        },
        {
            "id": "google_chat",
            "name": "Google Chat",
            "description": "Search and ask questions about conversations in your Chat spaces.",
            "status": chat_status,
            "enabled": chat_enabled,
            **chat_extra,
        },
        {
            "id": "file_upload",
            "name": "File Upload",
            "description": "Upload PDF, DOCX, XLSX, PPTX, CSV, and TXT files.",
            "status": (
                "available"
                if file_ready
                else ("not_configured" if settings.file_upload_enabled else "not_implemented")
            ),
            "enabled": file_ready,
        },
    ]


@router.get("/v1/features", response_model=FeaturesOut)
def get_features(
    settings: Annotated[Settings, Depends(get_settings)],
) -> FeaturesOut:
    return FeaturesOut(
        google_login_enabled=settings.google_oauth_configured,
        google_drive_enabled=settings.google_drive_ready,
        gmail_enabled=settings.gmail_ready,
        file_upload_enabled=settings.file_upload_ready,
        ai_query_enabled=settings.ai_query_ready,
        search_enabled=settings.search_ready,
        audit_enabled=True,
        usage_enabled=settings.usage_enabled,
        billing_enabled=settings.billing_enabled,
        sso_enabled=settings.sso_enabled,
        upload_max_bytes=settings.upload_max_bytes,
        upload_allowed_extensions=sorted(settings.allowed_upload_extensions),
        chat_stream_enabled=settings.chat_stream_enabled,
        llm_provider=settings.llm_provider,
    )


@router.get("/v1/connectors", response_model=ConnectorsResponse)
def list_connectors(
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> ConnectorsResponse:
    assert ctx.membership
    detail = None
    if settings.google_drive_ready:
        detail = _drive_service(db, settings).connection_detail(ctx.membership.tenant_id)
    gmail_detail = None
    if settings.gmail_ready:
        gmail_detail = _gmail_service(db, settings).connection_detail(ctx.membership.tenant_id)
    chat_detail = None
    if settings.google_chat_ready:
        chat_detail = _chat_service(db, settings).connection_detail(ctx.membership.tenant_id)
    return ConnectorsResponse(
        connectors=[
            ConnectorOut(**item)
            for item in _connector_catalog(settings, detail, gmail_detail, chat_detail)
        ]
    )


@router.get("/v1/dashboard", response_model=DashboardOut)
def get_dashboard(
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    svc: Annotated[DocumentService, Depends(get_document_service)],
) -> DashboardOut:
    assert ctx.membership
    tenant_id = ctx.membership.tenant_id

    users = (
        db.scalar(
            select(func.count())
            .select_from(OrganizationMember)
            .where(
                and_(
                    OrganizationMember.tenant_id == tenant_id,
                    OrganizationMember.status == MemberStatus.active,
                )
            )
        )
        or 0
    )

    activity_rows = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.tenant_id == tenant_id)
        .order_by(AuditEvent.created_at.desc())
        .limit(10)
    ).all()

    detail = None
    if settings.google_drive_ready:
        detail = _drive_service(db, settings).connection_detail(tenant_id)
    gmail_detail = None
    if settings.gmail_ready:
        gmail_detail = _gmail_service(db, settings).connection_detail(tenant_id)
    chat_detail = None
    if settings.google_chat_ready:
        chat_detail = _chat_service(db, settings).connection_detail(tenant_id)
    catalog = _connector_catalog(settings, detail, gmail_detail, chat_detail)
    connected = sum(
        1
        for c in catalog
        if c["status"] in {"connected", "syncing", "sync_failed"}
        or (c["id"] == "file_upload" and c["enabled"])
    )
    available = sum(1 for c in catalog if c["enabled"])
    sync_times = [
        d.get("last_sync_at")
        for d in (detail, gmail_detail, chat_detail)
        if d and d.get("last_sync_at")
    ]
    last_sync = max(sync_times) if sync_times else None

    return DashboardOut(
        documents=svc.count_ready_documents(tenant_id),
        chunks=svc.count_chunks(tenant_id),
        connections_connected=connected,
        connections_available=available,
        questions=0,
        users=int(users),
        last_sync_at=last_sync,
        recent_activity=[
            DashboardActivityOut(
                id=row.id,
                action=row.action,
                created_at=row.created_at,
                user_id=row.user_id,
                metadata=row.metadata_ or {},
            )
            for row in activity_rows
        ],
    )


@router.get("/v1/documents", response_model=DocumentsResponse)
def list_documents(
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    svc: Annotated[DocumentService, Depends(get_document_service)],
    db: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> DocumentsResponse:
    return list_documents_handler(ctx=ctx, svc=svc, db=db, page=page, limit=limit)


@router.post("/v1/documents/upload", response_model=UploadResponse)
async def upload_documents(
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    svc: Annotated[DocumentService, Depends(get_document_service)],
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
    visibility: str = Form(default="private"),
    selected_user_ids: str = Form(default=""),
) -> UploadResponse:
    return await upload_document(
        ctx=ctx,
        svc=svc,
        db=db,
        file=file,
        visibility=visibility,
        selected_user_ids=selected_user_ids,
    )


@router.get("/v1/documents/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: UUID,
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    svc: Annotated[DocumentService, Depends(get_document_service)],
    db: Annotated[Session, Depends(get_db)],
) -> DocumentOut:
    return get_document_handler(document_id=document_id, ctx=ctx, svc=svc, db=db)


@router.delete("/v1/documents/{document_id}")
def delete_document(
    document_id: UUID,
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    svc: Annotated[DocumentService, Depends(get_document_service)],
) -> dict:
    return delete_document_handler(document_id=document_id, ctx=ctx, svc=svc)


@router.get("/v1/documents/{document_id}/preview", response_model=DocumentPreviewOut)
def preview_document(
    document_id: UUID,
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    svc: Annotated[DocumentService, Depends(get_document_service)],
) -> DocumentPreviewOut:
    return preview_document_handler(document_id=document_id, ctx=ctx, svc=svc)


@router.get("/v1/documents/{document_id}/job", response_model=SyncJobOut)
def document_job(
    document_id: UUID,
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    svc: Annotated[DocumentService, Depends(get_document_service)],
) -> SyncJobOut:
    return get_document_job_handler(document_id=document_id, ctx=ctx, svc=svc)


@router.get("/v1/jobs/{job_id}", response_model=SyncJobOut)
def get_job(
    job_id: UUID,
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    svc: Annotated[DocumentService, Depends(get_document_service)],
) -> SyncJobOut:
    return get_job_handler(job_id=job_id, ctx=ctx, svc=svc)


@router.get("/v1/audit", response_model=AuditResponse)
def list_audit(
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    db: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> AuditResponse:
    assert ctx.membership
    tenant_id = ctx.membership.tenant_id
    total = (
        db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.tenant_id == tenant_id))
        or 0
    )
    rows = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.tenant_id == tenant_id)
        .order_by(AuditEvent.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    ).all()
    return AuditResponse(
        items=[
            AuditEventOut(
                id=row.id,
                action=row.action,
                user_id=row.user_id,
                request_id=row.request_id,
                metadata=row.metadata_ or {},
                created_at=row.created_at,
            )
            for row in rows
        ],
        total=int(total),
        page=page,
        limit=limit,
    )


@router.get("/v1/usage", response_model=UsageOut)
def get_usage(
    ctx: Annotated[RequestContext, Depends(require_tenant)],
) -> UsageOut:
    _ = ctx
    return UsageOut(
        available=False,
        message="Usage data isn't available yet.",
    )


@router.post("/v1/connections/{connector_id}/connect")
def connect_connector(
    connector_id: str,
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    _ = ctx
    known = {c["id"] for c in _connector_catalog(settings)}
    if connector_id not in known:
        raise AppError("CONNECTOR_NOT_FOUND", "Unknown connector.", 404)
    if connector_id == "file_upload":
        if not settings.file_upload_ready:
            raise AppError(
                "CONNECTOR_NOT_CONFIGURED",
                "File upload isn't configured yet.",
                503,
            )
        return {"ok": True, "connector_id": connector_id}
    if connector_id == "google_drive":
        if not settings.google_drive_ready:
            raise AppError(
                "CONNECTOR_NOT_CONFIGURED",
                "Google Drive isn't configured yet.",
                503,
            )
        return {
            "ok": True,
            "connector_id": connector_id,
            "oauth_start_path": "/v1/connections/google_drive/oauth/start",
        }
    if connector_id == "gmail":
        if not settings.gmail_ready:
            raise AppError(
                "CONNECTOR_NOT_CONFIGURED",
                "Gmail isn't configured yet.",
                503,
            )
        return {
            "ok": True,
            "connector_id": connector_id,
            "oauth_start_path": "/v1/connections/gmail/oauth/start",
        }
    if connector_id == "google_chat":
        if not settings.google_chat_ready:
            raise AppError(
                "CONNECTOR_NOT_CONFIGURED",
                "Google Chat isn't configured yet.",
                503,
            )
        return {
            "ok": True,
            "connector_id": connector_id,
            "oauth_start_path": "/v1/connections/google_chat/oauth/start",
        }
    raise AppError(
        "CONNECTOR_NOT_AVAILABLE",
        "This connection isn't available yet.",
        501,
    )
