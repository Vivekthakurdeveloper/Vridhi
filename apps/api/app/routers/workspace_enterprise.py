from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import RequestContext, require_role, require_tenant
from app.security import MemberRole
from app.services.tokens import get_token_store
from app.services.workspace_enterprise import WorkspaceEnterpriseService

router = APIRouter()


class WorkspaceEnterpriseSubmitRequest(BaseModel):
    google_domain: str
    service_account_key: str  # raw JSON, pasted or uploaded-then-read-as-text


class WorkspaceEnterpriseStatusOut(BaseModel):
    connected: bool
    status: Optional[str] = None
    google_domain: Optional[str] = None
    service_account_email: Optional[str] = None
    verified_scopes: Optional[str] = None
    last_verified_at: Optional[str] = None
    last_error: Optional[str] = None


def get_workspace_enterprise_service(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> WorkspaceEnterpriseService:
    return WorkspaceEnterpriseService(
        db,
        get_token_store(),
        is_mock=settings.workspace_enterprise_is_mock,
        scopes=settings.workspace_enterprise_scope_list,
    )


def _to_out(detail: dict) -> WorkspaceEnterpriseStatusOut:
    last_verified = detail.get("last_verified_at")
    return WorkspaceEnterpriseStatusOut(
        connected=bool(detail.get("connected")),
        status=detail.get("status"),
        google_domain=detail.get("google_domain"),
        service_account_email=detail.get("service_account_email"),
        verified_scopes=detail.get("verified_scopes"),
        last_verified_at=last_verified.isoformat() if last_verified else None,
        last_error=detail.get("last_error"),
    )


@router.get("/v1/enterprise/google-workspace", response_model=WorkspaceEnterpriseStatusOut)
def get_workspace_enterprise_status(
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    svc: Annotated[WorkspaceEnterpriseService, Depends(get_workspace_enterprise_service)],
) -> WorkspaceEnterpriseStatusOut:
    assert ctx.membership
    return _to_out(svc.connection_detail(ctx.membership.tenant_id))


@router.post("/v1/enterprise/google-workspace", response_model=WorkspaceEnterpriseStatusOut)
def submit_workspace_enterprise(
    body: WorkspaceEnterpriseSubmitRequest,
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    svc: Annotated[WorkspaceEnterpriseService, Depends(get_workspace_enterprise_service)],
) -> WorkspaceEnterpriseStatusOut:
    assert ctx.membership and ctx.user
    svc.submit_and_verify(
        tenant_id=ctx.membership.tenant_id,
        user_id=ctx.user.id,
        google_domain=body.google_domain,
        raw_key=body.service_account_key,
        admin_email=ctx.user.email,
    )
    return _to_out(svc.connection_detail(ctx.membership.tenant_id))


@router.post("/v1/enterprise/google-workspace/verify", response_model=WorkspaceEnterpriseStatusOut)
def verify_workspace_enterprise(
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    svc: Annotated[WorkspaceEnterpriseService, Depends(get_workspace_enterprise_service)],
) -> WorkspaceEnterpriseStatusOut:
    assert ctx.membership and ctx.user
    svc.verify(tenant_id=ctx.membership.tenant_id, admin_email=ctx.user.email)
    return _to_out(svc.connection_detail(ctx.membership.tenant_id))


@router.delete("/v1/enterprise/google-workspace")
def disable_workspace_enterprise(
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    svc: Annotated[WorkspaceEnterpriseService, Depends(get_workspace_enterprise_service)],
) -> dict:
    assert ctx.membership
    svc.disable(tenant_id=ctx.membership.tenant_id)
    return {"ok": True}
