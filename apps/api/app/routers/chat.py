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
from app.routers.documents import job_to_out
from app.schemas import SyncJobOut
from app.security import MemberRole
from app.services.chat import ChatService, new_oauth_state
from app.services.queue import get_ingest_queue
from app.services.storage import get_object_storage
from app.services.tokens import get_token_store

router = APIRouter()

CHAT_OAUTH_COOKIE = "vridhi_chat_oauth_state"


class ChatConnectionOut(BaseModel):
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
    selected_space_ids: list[str] = Field(default_factory=list)
    auto_sync_enabled: Optional[bool] = None
    auto_sync_paused_reason: Optional[str] = None


class ChatSpaceOut(BaseModel):
    id: str
    name: str


class ChatSpacesResponse(BaseModel):
    spaces: list[ChatSpaceOut]
    selected_space_ids: list[str] = Field(default_factory=list)


class SaveChatSpacesRequest(BaseModel):
    space_ids: list[str]


class AutoSyncRequest(BaseModel):
    enabled: bool


class ChatSyncRequest(BaseModel):
    space_ids: Optional[list[str]] = None
    incremental: bool = True


class ChatSyncHistoryResponse(BaseModel):
    items: list[SyncJobOut]


def get_chat_service(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ChatService:
    return ChatService(db, settings, get_token_store(), get_object_storage(), get_ingest_queue())


@router.get("/v1/connections/google_chat", response_model=ChatConnectionOut)
def get_chat_connection(
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> ChatConnectionOut:
    assert ctx.membership
    detail = chat.connection_detail(ctx.membership.tenant_id)
    last_sync = detail.get("last_sync_at")
    return ChatConnectionOut(
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
        selected_space_ids=list(detail.get("selected_space_ids") or []),
        auto_sync_enabled=detail.get("auto_sync_enabled"),
        auto_sync_paused_reason=detail.get("auto_sync_paused_reason"),
    )


@router.put("/v1/connections/google_chat/auto-sync", response_model=ChatConnectionOut)
def set_chat_auto_sync(
    body: AutoSyncRequest,
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> ChatConnectionOut:
    assert ctx.membership and ctx.user
    chat.set_auto_sync(tenant_id=ctx.membership.tenant_id, user_id=ctx.user.id, enabled=body.enabled)
    return get_chat_connection(ctx=ctx, chat=chat)


@router.get("/v1/connections/google_chat/oauth/start")
def chat_oauth_start(
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> RedirectResponse:
    assert ctx.membership and ctx.user
    state = new_oauth_state()
    url = chat.oauth_start_url(state=state)
    response = RedirectResponse(url=url, status_code=302)
    response.set_cookie(CHAT_OAUTH_COOKIE, state, httponly=True, samesite="lax", max_age=600, path="/")
    return response


@router.get("/v1/connections/google_chat/oauth/callback")
def chat_oauth_callback(
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    chat: Annotated[ChatService, Depends(get_chat_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    assert ctx.membership and ctx.user
    frontend = settings.frontend_url.rstrip("/")
    fail_url = f"{frontend}/app/connections?chat=error"
    if error or not code:
        redirect = RedirectResponse(url=fail_url, status_code=302)
        redirect.delete_cookie(CHAT_OAUTH_COOKIE, path="/")
        return redirect

    expected = request.cookies.get(CHAT_OAUTH_COOKIE)
    if not expected or not state or expected != state:
        raise AppError("CHAT_OAUTH_FAILED", "Chat OAuth state mismatch.", 401)

    try:
        chat.complete_oauth(tenant_id=ctx.membership.tenant_id, user_id=ctx.user.id, code=code)
    except AppError:
        redirect = RedirectResponse(url=fail_url, status_code=302)
        redirect.delete_cookie(CHAT_OAUTH_COOKIE, path="/")
        return redirect

    redirect = RedirectResponse(url=f"{frontend}/app/connections?chat=connected", status_code=302)
    redirect.delete_cookie(CHAT_OAUTH_COOKIE, path="/")
    return redirect


@router.delete("/v1/connections/google_chat")
def disconnect_chat(
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    assert ctx.membership and ctx.user
    chat.disconnect(tenant_id=ctx.membership.tenant_id, user_id=ctx.user.id)
    return {"ok": True}


@router.get("/v1/connections/google_chat/spaces", response_model=ChatSpacesResponse)
def list_chat_spaces(
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> ChatSpacesResponse:
    assert ctx.membership
    spaces = chat.list_spaces(tenant_id=ctx.membership.tenant_id)
    conn = chat.get_connection(ctx.membership.tenant_id)
    selected = list((conn.config or {}).get("selected_space_ids") or []) if conn else []
    return ChatSpacesResponse(spaces=[ChatSpaceOut(**s) for s in spaces], selected_space_ids=selected)


@router.put("/v1/connections/google_chat/spaces", response_model=ChatConnectionOut)
def save_chat_spaces(
    body: SaveChatSpacesRequest,
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> ChatConnectionOut:
    assert ctx.membership
    chat.save_selected_spaces(tenant_id=ctx.membership.tenant_id, space_ids=body.space_ids)
    return get_chat_connection(ctx=ctx, chat=chat)


@router.post("/v1/connections/google_chat/sync", response_model=SyncJobOut)
def sync_chat_now(
    body: ChatSyncRequest,
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> SyncJobOut:
    assert ctx.membership and ctx.user
    job = chat.start_sync(
        tenant_id=ctx.membership.tenant_id,
        user_id=ctx.user.id,
        space_ids=body.space_ids,
        incremental=body.incremental,
    )
    return job_to_out(job)


@router.get("/v1/connections/google_chat/syncs", response_model=ChatSyncHistoryResponse)
def chat_sync_history(
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    chat: Annotated[ChatService, Depends(get_chat_service)],
    limit: int = Query(default=20, ge=1, le=100),
) -> ChatSyncHistoryResponse:
    assert ctx.membership
    jobs = chat.list_sync_history(tenant_id=ctx.membership.tenant_id, limit=limit)
    return ChatSyncHistoryResponse(items=[job_to_out(j) for j in jobs])
