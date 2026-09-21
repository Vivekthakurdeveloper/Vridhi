from __future__ import annotations

from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import RequestContext, require_role, require_tenant
from app.errors import AppError
from app.routers.documents import document_to_out, job_to_out
from app.schemas import DocumentOut, SyncJobOut
from app.security import DocumentVisibility, MemberRole
from app.services.drive import DriveService, new_oauth_state
from app.services.queue import get_ingest_queue
from app.services.storage import get_object_storage
from app.services.tokens import get_token_store

router = APIRouter()

DRIVE_OAUTH_COOKIE = "vridhi_drive_oauth_state"


class DriveConnectionOut(BaseModel):
    connected: bool
    status: str
    health: Optional[str] = None
    account_email: Optional[str] = None
    last_sync_at: Optional[str] = None
    last_error: Optional[str] = None
    document_count: int = 0
    failed_document_count: int = 0
    selected_folder_ids: list[str] = Field(default_factory=list)
    mode: str
    connection_id: Optional[UUID] = None
    auto_sync_enabled: Optional[bool] = None
    auto_sync_paused_reason: Optional[str] = None


class DriveFolderOut(BaseModel):
    id: str
    name: str
    path: str


class DriveFoldersResponse(BaseModel):
    folders: list[DriveFolderOut]
    selected_folder_ids: list[str] = Field(default_factory=list)


class SelectFoldersRequest(BaseModel):
    folder_ids: list[str] = Field(default_factory=list)


class DriveSyncRequest(BaseModel):
    folder_ids: Optional[list[str]] = None
    visibility: str = "org"
    selected_user_ids: list[UUID] = Field(default_factory=list)
    incremental: bool = True


class DriveSyncHistoryResponse(BaseModel):
    items: list[SyncJobOut]


class DriveFailedDocumentsResponse(BaseModel):
    items: list[DocumentOut]


def get_drive_service(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DriveService:
    return DriveService(
        db,
        settings,
        get_token_store(),
        get_object_storage(),
        get_ingest_queue(),
    )


@router.get("/v1/connections/google_drive", response_model=DriveConnectionOut)
def get_drive_connection(
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    drive: Annotated[DriveService, Depends(get_drive_service)],
) -> DriveConnectionOut:
    assert ctx.membership
    detail = drive.connection_detail(ctx.membership.tenant_id)
    last_sync = detail.get("last_sync_at")
    return DriveConnectionOut(
        connected=bool(detail.get("connected")),
        status=str(detail.get("status")),
        health=detail.get("health"),
        account_email=detail.get("account_email"),
        last_sync_at=last_sync.isoformat() if last_sync else None,
        last_error=detail.get("last_error"),
        document_count=int(detail.get("document_count") or 0),
        failed_document_count=int(detail.get("failed_document_count") or 0),
        selected_folder_ids=list(detail.get("selected_folder_ids") or []),
        mode=str(detail.get("mode") or "mock"),
        connection_id=detail.get("connection_id"),
        auto_sync_enabled=detail.get("auto_sync_enabled"),
        auto_sync_paused_reason=detail.get("auto_sync_paused_reason"),
    )


class AutoSyncRequest(BaseModel):
    enabled: bool


@router.put("/v1/connections/google_drive/auto-sync", response_model=DriveConnectionOut)
def set_drive_auto_sync(
    body: AutoSyncRequest,
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    drive: Annotated[DriveService, Depends(get_drive_service)],
) -> DriveConnectionOut:
    assert ctx.membership and ctx.user
    drive.set_auto_sync(
        tenant_id=ctx.membership.tenant_id, user_id=ctx.user.id, enabled=body.enabled
    )
    return get_drive_connection(ctx=ctx, drive=drive)


@router.get("/v1/connections/google_drive/oauth/start")
def drive_oauth_start(
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    drive: Annotated[DriveService, Depends(get_drive_service)],
) -> RedirectResponse:
    assert ctx.membership and ctx.user
    state = new_oauth_state()
    url = drive.oauth_start_url(
        tenant_id=ctx.membership.tenant_id,
        user_id=ctx.user.id,
        state=state,
    )
    response = RedirectResponse(url=url, status_code=302)
    response.set_cookie(
        DRIVE_OAUTH_COOKIE,
        state,
        httponly=True,
        samesite="lax",
        max_age=600,
        path="/",
    )
    return response


@router.get("/v1/connections/google_drive/oauth/callback")
def drive_oauth_callback(
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    drive: Annotated[DriveService, Depends(get_drive_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    assert ctx.membership and ctx.user
    frontend = settings.frontend_url.rstrip("/")
    fail_url = f"{frontend}/app/connections?drive=error"
    if error or not code:
        redirect = RedirectResponse(url=fail_url, status_code=302)
        redirect.delete_cookie(DRIVE_OAUTH_COOKIE, path="/")
        return redirect

    expected = request.cookies.get(DRIVE_OAUTH_COOKIE)
    if not expected or not state or expected != state:
        raise AppError("DRIVE_OAUTH_FAILED", "Google Drive OAuth state mismatch.", 401)

    try:
        drive.complete_oauth(
            tenant_id=ctx.membership.tenant_id,
            user_id=ctx.user.id,
            code=code,
        )
    except AppError:
        redirect = RedirectResponse(url=fail_url, status_code=302)
        redirect.delete_cookie(DRIVE_OAUTH_COOKIE, path="/")
        return redirect

    redirect = RedirectResponse(
        url=f"{frontend}/app/connections?drive=connected",
        status_code=302,
    )
    redirect.delete_cookie(DRIVE_OAUTH_COOKIE, path="/")
    return redirect


@router.delete("/v1/connections/google_drive")
def disconnect_drive(
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    drive: Annotated[DriveService, Depends(get_drive_service)],
) -> dict:
    assert ctx.membership and ctx.user
    drive.disconnect(tenant_id=ctx.membership.tenant_id, user_id=ctx.user.id)
    return {"ok": True}


@router.get("/v1/connections/google_drive/folders", response_model=DriveFoldersResponse)
def list_drive_folders(
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    drive: Annotated[DriveService, Depends(get_drive_service)],
) -> DriveFoldersResponse:
    assert ctx.membership
    detail = drive.connection_detail(ctx.membership.tenant_id)
    folders = drive.list_folders(tenant_id=ctx.membership.tenant_id)
    return DriveFoldersResponse(
        folders=[DriveFolderOut(id=f.id, name=f.name, path=f.path) for f in folders],
        selected_folder_ids=list(detail.get("selected_folder_ids") or []),
    )


@router.put("/v1/connections/google_drive/folders", response_model=DriveConnectionOut)
def save_drive_folders(
    body: SelectFoldersRequest,
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    drive: Annotated[DriveService, Depends(get_drive_service)],
) -> DriveConnectionOut:
    assert ctx.membership
    drive.save_selected_folders(
        tenant_id=ctx.membership.tenant_id,
        folder_ids=body.folder_ids,
    )
    return get_drive_connection(ctx=ctx, drive=drive)


@router.post("/v1/connections/google_drive/sync", response_model=SyncJobOut)
def sync_drive_now(
    body: DriveSyncRequest,
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    drive: Annotated[DriveService, Depends(get_drive_service)],
) -> SyncJobOut:
    assert ctx.membership and ctx.user
    try:
        visibility = DocumentVisibility(body.visibility)
    except ValueError as exc:
        raise AppError(
            "INVALID_VISIBILITY",
            "Visibility must be private, org, or selected.",
            400,
        ) from exc
    job = drive.start_sync(
        tenant_id=ctx.membership.tenant_id,
        user_id=ctx.user.id,
        folder_ids=body.folder_ids,
        visibility=visibility,
        selected_user_ids=body.selected_user_ids or None,
        incremental=body.incremental,
    )
    return job_to_out(job)


@router.get("/v1/connections/google_drive/syncs", response_model=DriveSyncHistoryResponse)
def drive_sync_history(
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    drive: Annotated[DriveService, Depends(get_drive_service)],
    limit: int = Query(default=20, ge=1, le=100),
) -> DriveSyncHistoryResponse:
    assert ctx.membership
    jobs = drive.list_sync_history(tenant_id=ctx.membership.tenant_id, limit=limit)
    return DriveSyncHistoryResponse(items=[job_to_out(j) for j in jobs])


@router.get(
    "/v1/connections/google_drive/failed-documents",
    response_model=DriveFailedDocumentsResponse,
)
def drive_failed_documents(
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    drive: Annotated[DriveService, Depends(get_drive_service)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=100),
) -> DriveFailedDocumentsResponse:
    assert ctx.membership
    docs = drive.list_failed_documents(tenant_id=ctx.membership.tenant_id, limit=limit)
    return DriveFailedDocumentsResponse(items=[document_to_out(db, d) for d in docs])
