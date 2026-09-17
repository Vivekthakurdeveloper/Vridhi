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
from app.security import MemberRole
from app.services.gmail import GmailService, new_oauth_state
from app.services.queue import get_ingest_queue
from app.services.storage import get_object_storage
from app.services.tokens import get_token_store

router = APIRouter()

# Distinct from DRIVE_OAUTH_COOKIE so connecting one connector cannot clobber
# an in-flight OAuth handshake for the other.
GMAIL_OAUTH_COOKIE = "vridhi_gmail_oauth_state"


class GmailConnectionOut(BaseModel):
    connected: bool
    status: str
    health: Optional[str] = None
    account_email: Optional[str] = None
    last_sync_at: Optional[str] = None
    last_error: Optional[str] = None
    document_count: int = 0
    failed_document_count: int = 0
    mode: str
    connection_id: Optional[UUID] = None


class GmailSyncRequest(BaseModel):
    query: Optional[str] = None
    incremental: bool = True


class GmailSyncHistoryResponse(BaseModel):
    items: list[SyncJobOut]


class GmailFailedDocumentsResponse(BaseModel):
    items: list[DocumentOut] = Field(default_factory=list)


def get_gmail_service(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> GmailService:
    return GmailService(
        db,
        settings,
        get_token_store(),
        get_object_storage(),
        get_ingest_queue(),
    )


@router.get("/v1/connections/gmail", response_model=GmailConnectionOut)
def get_gmail_connection(
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    gmail: Annotated[GmailService, Depends(get_gmail_service)],
) -> GmailConnectionOut:
    assert ctx.membership
    detail = gmail.connection_detail(ctx.membership.tenant_id)
    last_sync = detail.get("last_sync_at")
    return GmailConnectionOut(
        connected=bool(detail.get("connected")),
        status=str(detail.get("status")),
        health=detail.get("health"),
        account_email=detail.get("account_email"),
        last_sync_at=last_sync.isoformat() if last_sync else None,
        last_error=detail.get("last_error"),
        document_count=int(detail.get("document_count") or 0),
        failed_document_count=int(detail.get("failed_document_count") or 0),
        mode=str(detail.get("mode") or "mock"),
        connection_id=detail.get("connection_id"),
    )


@router.get("/v1/connections/gmail/oauth/start")
def gmail_oauth_start(
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    gmail: Annotated[GmailService, Depends(get_gmail_service)],
) -> RedirectResponse:
    assert ctx.membership and ctx.user
    state = new_oauth_state()
    url = gmail.oauth_start_url(state=state)
    response = RedirectResponse(url=url, status_code=302)
    # SameSite=Lax is load-bearing: the callback below is guarded by
    # require_role(admin), so the session cookie must survive Google's
    # top-level redirect back to us.
    response.set_cookie(
        GMAIL_OAUTH_COOKIE,
        state,
        httponly=True,
        samesite="lax",
        max_age=600,
        path="/",
    )
    return response


@router.get("/v1/connections/gmail/oauth/callback")
def gmail_oauth_callback(
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    gmail: Annotated[GmailService, Depends(get_gmail_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    assert ctx.membership and ctx.user
    frontend = settings.frontend_url.rstrip("/")
    fail_url = f"{frontend}/app/connections?gmail=error"
    if error or not code:
        redirect = RedirectResponse(url=fail_url, status_code=302)
        redirect.delete_cookie(GMAIL_OAUTH_COOKIE, path="/")
        return redirect

    expected = request.cookies.get(GMAIL_OAUTH_COOKIE)
    if not expected or not state or expected != state:
        raise AppError("GMAIL_OAUTH_FAILED", "Gmail OAuth state mismatch.", 401)

    try:
        gmail.complete_oauth(
            tenant_id=ctx.membership.tenant_id,
            user_id=ctx.user.id,
            code=code,
        )
    except AppError:
        redirect = RedirectResponse(url=fail_url, status_code=302)
        redirect.delete_cookie(GMAIL_OAUTH_COOKIE, path="/")
        return redirect

    redirect = RedirectResponse(
        url=f"{frontend}/app/connections?gmail=connected",
        status_code=302,
    )
    redirect.delete_cookie(GMAIL_OAUTH_COOKIE, path="/")
    return redirect


@router.delete("/v1/connections/gmail")
def disconnect_gmail(
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    gmail: Annotated[GmailService, Depends(get_gmail_service)],
) -> dict:
    assert ctx.membership and ctx.user
    gmail.disconnect(tenant_id=ctx.membership.tenant_id, user_id=ctx.user.id)
    return {"ok": True}


@router.post("/v1/connections/gmail/sync", response_model=SyncJobOut)
def sync_gmail_now(
    body: GmailSyncRequest,
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    gmail: Annotated[GmailService, Depends(get_gmail_service)],
) -> SyncJobOut:
    assert ctx.membership and ctx.user
    job = gmail.start_sync(
        tenant_id=ctx.membership.tenant_id,
        user_id=ctx.user.id,
        query=body.query,
        incremental=body.incremental,
    )
    return job_to_out(job)


@router.get("/v1/connections/gmail/syncs", response_model=GmailSyncHistoryResponse)
def gmail_sync_history(
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    gmail: Annotated[GmailService, Depends(get_gmail_service)],
    limit: int = Query(default=20, ge=1, le=100),
) -> GmailSyncHistoryResponse:
    assert ctx.membership
    jobs = gmail.list_sync_history(tenant_id=ctx.membership.tenant_id, limit=limit)
    return GmailSyncHistoryResponse(items=[job_to_out(j) for j in jobs])


@router.get(
    "/v1/connections/gmail/failed-documents",
    response_model=GmailFailedDocumentsResponse,
)
def gmail_failed_documents(
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    gmail: Annotated[GmailService, Depends(get_gmail_service)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=100),
) -> GmailFailedDocumentsResponse:
    assert ctx.membership
    docs = gmail.list_failed_documents(tenant_id=ctx.membership.tenant_id, limit=limit)
    return GmailFailedDocumentsResponse(items=[document_to_out(db, d) for d in docs])
