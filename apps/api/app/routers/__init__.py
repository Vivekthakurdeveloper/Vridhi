from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import and_, select

from app.config import Settings, get_settings
from app.deps import (
    RequestContext,
    get_auth_service,
    get_org_service,
    require_auth,
    require_role,
    require_tenant,
)
from app.errors import AppError
from app.models import Invite, OrganizationMember, User
from app.models import Session as DbSession
from app.schemas import (
    AcceptInviteRequest,
    AuthSessionOut,
    CreateOrganizationRequest,
    ForgotPasswordRequest,
    InviteOut,
    InviteUserRequest,
    LoginRequest,
    MemberOut,
    MembershipOut,
    OrganizationOut,
    PatchOrganizationRequest,
    PatchUserRequest,
    RegisterRequest,
    ResetPasswordRequest,
    UserOut,
    VerifyEmailRequest,
)
from app.security import MemberRole, generate_token
from app.services.auth import AuthService, MembershipContext, OrgService

router = APIRouter()


def _user_out(user) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        email_verified_at=user.email_verified_at,
        status=user.status.value if hasattr(user.status, "value") else str(user.status),
    )


def _membership_out(m: MembershipContext | None) -> MembershipOut | None:
    if not m:
        return None
    return MembershipOut(
        tenant_id=m.tenant_id,
        role=m.role,
        status=m.status,
        organization_name=m.organization_name,
        organization_slug=m.organization_slug,
    )


def _invite_out(invite: Invite, settings: Settings) -> InviteOut:
    debug_token = None
    raw = getattr(invite, "_raw_token", None)
    if (
        raw
        and settings.app_env == "development"
        and settings.email_provider == "log"
    ):
        debug_token = raw
    return InviteOut(
        id=invite.id,
        email=invite.email,
        role=invite.role,
        status=invite.status.value if hasattr(invite.status, "value") else str(invite.status),
        expires_at=invite.expires_at,
        created_at=invite.created_at,
        debug_token=debug_token,
    )


def _set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_days * 24 * 60 * 60,
        path="/",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(key=settings.session_cookie_name, path="/")


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    ua = request.headers.get("user-agent")
    ip = request.client.host if request.client else None
    return ua, ip


@router.post("/v1/auth/register", response_model=AuthSessionOut)
def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthSessionOut:
    ua, ip = _client_meta(request)
    user, org, membership, token = auth.register(
        name=body.name,
        email=str(body.email),
        password=body.password,
        organization_name=body.organization_name,
        request_id=request.state.ctx.request_id,
        user_agent=ua,
        ip_address=ip,
    )
    _set_session_cookie(response, settings, token)
    membership_out = None
    if org and membership:
        membership_out = MembershipOut(
            tenant_id=org.id,
            role=membership.role,
            status=membership.status,
            organization_name=org.name,
            organization_slug=org.slug,
        )
    return AuthSessionOut(user=_user_out(user), membership=membership_out)


@router.post("/v1/auth/login", response_model=AuthSessionOut)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthSessionOut:
    ua, ip = _client_meta(request)
    user, membership, token = auth.login(
        email=str(body.email),
        password=body.password,
        request_id=request.state.ctx.request_id,
        user_agent=ua,
        ip_address=ip,
    )
    _set_session_cookie(response, settings, token)
    return AuthSessionOut(user=_user_out(user), membership=_membership_out(membership))


@router.post("/v1/auth/logout")
def logout(
    request: Request,
    response: Response,
    ctx: Annotated[RequestContext, Depends(require_auth)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    assert ctx.user and ctx.session_id
    auth.logout(
        session_id=ctx.session_id,
        user_id=ctx.user.id,
        tenant_id=ctx.membership.tenant_id if ctx.membership else None,
        request_id=ctx.request_id,
    )
    _clear_session_cookie(response, settings)
    return {"ok": True}


@router.post("/v1/auth/forgot-password")
def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> dict:
    auth.forgot_password(email=str(body.email), request_id=request.state.ctx.request_id)
    return {"ok": True}


@router.post("/v1/auth/reset-password")
def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> dict:
    auth.reset_password(token=body.token, password=body.password, request_id=request.state.ctx.request_id)
    return {"ok": True}


@router.post("/v1/auth/verify-email")
def verify_email(
    body: VerifyEmailRequest,
    request: Request,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> dict:
    auth.verify_email(token=body.token, request_id=request.state.ctx.request_id)
    return {"ok": True}


@router.get("/v1/auth/me", response_model=AuthSessionOut)
def me(ctx: Annotated[RequestContext, Depends(require_auth)]) -> AuthSessionOut:
    assert ctx.user
    return AuthSessionOut(user=_user_out(ctx.user), membership=_membership_out(ctx.membership))


@router.get("/v1/auth/google")
def google_start(
    request: Request,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> RedirectResponse:
    state = generate_token(16)
    request.state.oauth_state = state
    url = auth.google_auth_url(state)
    response = RedirectResponse(url=url, status_code=302)
    response.set_cookie("vridhi_oauth_state", state, httponly=True, samesite="lax", max_age=600, path="/")
    return response


@router.get("/v1/auth/google/callback")
def google_callback(
    request: Request,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    if error or not code:
        raise AppError("GOOGLE_AUTH_FAILED", "We could not authenticate with Google.", 401)
    expected = request.cookies.get("vridhi_oauth_state")
    if not expected or not state or expected != state:
        raise AppError("GOOGLE_AUTH_FAILED", "We could not authenticate with Google.", 401)
    ua, ip = _client_meta(request)
    _, _, token = auth.google_callback(
        code=code,
        request_id=request.state.ctx.request_id,
        user_agent=ua,
        ip_address=ip,
    )
    redirect = RedirectResponse(url=f"{settings.frontend_url}/onboarding", status_code=302)
    _set_session_cookie(redirect, settings, token)
    redirect.delete_cookie("vridhi_oauth_state", path="/")
    return redirect


@router.post("/v1/organizations", response_model=OrganizationOut)
def create_organization(
    body: CreateOrganizationRequest,
    ctx: Annotated[RequestContext, Depends(require_auth)],
    orgs: Annotated[OrgService, Depends(get_org_service)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> OrganizationOut:
    assert ctx.user and ctx.session_id
    session = auth.db.get(DbSession, ctx.session_id)
    if not session:
        raise AppError("UNAUTHENTICATED", "Authentication required.", 401)
    org = orgs.create_organization(
        user=ctx.user,
        name=body.name,
        session=session,
        request_id=ctx.request_id,
    )
    return OrganizationOut.model_validate(org)


@router.get("/v1/organizations/me", response_model=OrganizationOut)
def get_my_organization(
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    orgs: Annotated[OrgService, Depends(get_org_service)],
) -> OrganizationOut:
    assert ctx.membership
    org = orgs.get_my_organization(ctx.membership.tenant_id)
    return OrganizationOut.model_validate(org)


@router.patch("/v1/organizations/{organization_id}", response_model=OrganizationOut)
def patch_organization(
    organization_id: UUID,
    body: PatchOrganizationRequest,
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.owner))],
    orgs: Annotated[OrgService, Depends(get_org_service)],
) -> OrganizationOut:
    assert ctx.user and ctx.membership
    if organization_id != ctx.membership.tenant_id:
        raise AppError("FORBIDDEN", "You do not have access to this organization.", 403)
    org = orgs.patch_organization(
        tenant_id=ctx.membership.tenant_id,
        name=body.name,
        user_id=ctx.user.id,
        request_id=ctx.request_id,
    )
    return OrganizationOut.model_validate(org)


@router.get("/v1/users", response_model=list[MemberOut])
def list_users(
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    orgs: Annotated[OrgService, Depends(get_org_service)],
) -> list[MemberOut]:
    assert ctx.membership
    rows = orgs.list_users(ctx.membership.tenant_id)
    return [
        MemberOut(
            id=m.id,
            user_id=u.id,
            email=u.email,
            name=u.name,
            role=m.role,
            status=m.status,
            created_at=m.created_at,
        )
        for m, u in rows
    ]


@router.post("/v1/users/invite", response_model=InviteOut)
def invite_user(
    body: InviteUserRequest,
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    orgs: Annotated[OrgService, Depends(get_org_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> InviteOut:
    assert ctx.user and ctx.membership
    invite = orgs.invite_user(
        tenant_id=ctx.membership.tenant_id,
        invited_by=ctx.user,
        email=str(body.email),
        role=body.role,
        request_id=ctx.request_id,
    )
    return _invite_out(invite, settings)


@router.get("/v1/users/invites", response_model=list[InviteOut])
def list_invites(
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    orgs: Annotated[OrgService, Depends(get_org_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[InviteOut]:
    assert ctx.membership
    invites = orgs.list_invites(ctx.membership.tenant_id)
    return [_invite_out(invite, settings) for invite in invites]


@router.post("/v1/users/invites/{invite_id}/resend", response_model=InviteOut)
def resend_invite(
    invite_id: UUID,
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    orgs: Annotated[OrgService, Depends(get_org_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> InviteOut:
    assert ctx.user and ctx.membership
    invite = orgs.resend_invite(
        tenant_id=ctx.membership.tenant_id,
        invite_id=invite_id,
        actor_id=ctx.user.id,
        request_id=ctx.request_id,
    )
    return _invite_out(invite, settings)


@router.delete("/v1/users/invites/{invite_id}")
def revoke_invite(
    invite_id: UUID,
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    orgs: Annotated[OrgService, Depends(get_org_service)],
) -> dict:
    assert ctx.user and ctx.membership
    orgs.revoke_invite(
        tenant_id=ctx.membership.tenant_id,
        invite_id=invite_id,
        actor_id=ctx.user.id,
        request_id=ctx.request_id,
    )
    return {"ok": True}


@router.post("/v1/users/invite/accept", response_model=AuthSessionOut)
def accept_invite(
    body: AcceptInviteRequest,
    request: Request,
    response: Response,
    orgs: Annotated[OrgService, Depends(get_org_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthSessionOut:
    ua, ip = _client_meta(request)
    user, membership, token = orgs.accept_invite(
        token=body.token,
        name=body.name,
        password=body.password,
        request_id=request.state.ctx.request_id,
        user_agent=ua,
        ip_address=ip,
    )
    _set_session_cookie(response, settings, token)
    return AuthSessionOut(user=_user_out(user), membership=_membership_out(membership))


@router.patch("/v1/users/{user_id}", response_model=MemberOut)
def patch_user(
    user_id: UUID,
    body: PatchUserRequest,
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    orgs: Annotated[OrgService, Depends(get_org_service)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> MemberOut:
    assert ctx.user and ctx.membership
    actor = auth.db.scalar(
        select(OrganizationMember).where(
            and_(
                OrganizationMember.tenant_id == ctx.membership.tenant_id,
                OrganizationMember.user_id == ctx.user.id,
            )
        )
    )
    if not actor:
        raise AppError("FORBIDDEN", "You do not have permission to perform this action.", 403)
    member = orgs.patch_member(
        tenant_id=ctx.membership.tenant_id,
        member_user_id=user_id,
        actor=actor,
        role=body.role,
        status=body.status,
        request_id=ctx.request_id,
    )
    user = auth.db.get(User, member.user_id)
    assert user
    return MemberOut(
        id=member.id,
        user_id=user.id,
        email=user.email,
        name=user.name,
        role=member.role,
        status=member.status,
        created_at=member.created_at,
    )


@router.delete("/v1/users/{user_id}")
def deactivate_user(
    user_id: UUID,
    ctx: Annotated[RequestContext, Depends(require_role(MemberRole.admin))],
    orgs: Annotated[OrgService, Depends(get_org_service)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> dict:
    assert ctx.user and ctx.membership
    actor = auth.db.scalar(
        select(OrganizationMember).where(
            and_(
                OrganizationMember.tenant_id == ctx.membership.tenant_id,
                OrganizationMember.user_id == ctx.user.id,
            )
        )
    )
    if not actor:
        raise AppError("FORBIDDEN", "You do not have permission to perform this action.", 403)
    orgs.deactivate_member(
        tenant_id=ctx.membership.tenant_id,
        member_user_id=user_id,
        actor=actor,
        request_id=ctx.request_id,
    )
    return {"ok": True}
