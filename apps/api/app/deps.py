from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Cookie, Depends, Request
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.errors import AppError
from app.models import OrganizationMember, User
from app.security import MemberRole, role_at_least
from app.services.auth import AuthService, MembershipContext, OrgService
from app.services.email import EmailService


@dataclass
class RequestContext:
    request_id: str
    user: User | None = None
    membership: MembershipContext | None = None
    session_id: UUID | None = None
    member_row: OrganizationMember | None = None


def get_email_service(settings: Annotated[Settings, Depends(get_settings)]) -> EmailService:
    return EmailService(settings)


def get_auth_service(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    email: Annotated[EmailService, Depends(get_email_service)],
) -> AuthService:
    return AuthService(db, settings, email)


def get_org_service(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    email: Annotated[EmailService, Depends(get_email_service)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> OrgService:
    return OrgService(db, settings, email, auth)


def get_request_context(request: Request) -> RequestContext:
    return request.state.ctx


def load_session(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
    session_token: Annotated[str | None, Cookie(alias="vridhi_session")] = None,
) -> RequestContext:
    ctx: RequestContext = request.state.ctx
    cookie_name = settings.session_cookie_name
    token = request.cookies.get(cookie_name) or session_token
    if not token:
        return ctx
    session = auth.get_session_by_token(token)
    if not session:
        return ctx
    ctx.user = session.user
    ctx.membership = session.membership
    ctx.session_id = session.session.id
    return ctx


def require_auth(ctx: Annotated[RequestContext, Depends(load_session)]) -> RequestContext:
    if not ctx.user or not ctx.session_id:
        raise AppError("UNAUTHENTICATED", "Authentication required.", 401)
    return ctx


def require_tenant(ctx: Annotated[RequestContext, Depends(require_auth)]) -> RequestContext:
    if not ctx.membership:
        raise AppError("TENANT_REQUIRED", "Join or create an organization first.", 400)
    return ctx


def require_role(minimum: MemberRole):
    def _guard(ctx: Annotated[RequestContext, Depends(require_tenant)]) -> RequestContext:
        assert ctx.membership is not None
        if not role_at_least(ctx.membership.role, minimum):
            raise AppError("FORBIDDEN", "You do not have permission to perform this action.", 403)
        return ctx

    return _guard
