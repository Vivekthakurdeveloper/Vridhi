from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import AppError
from app.models import (
    AuditEvent,
    EmailVerificationToken,
    Invite,
    OAuthAccount,
    Organization,
    OrganizationMember,
    PasswordResetToken,
    User,
)
from app.models import (
    Session as DbSession,
)
from app.security import (
    InviteStatus,
    MemberRole,
    MemberStatus,
    OAuthProvider,
    UserStatus,
    generate_token,
    hash_password,
    hash_token,
    slugify,
    utcnow,
    verify_password,
)
from app.services.email import EmailMessage, EmailService


@dataclass
class MembershipContext:
    tenant_id: uuid.UUID
    role: MemberRole
    status: MemberStatus
    organization_name: str
    organization_slug: str


@dataclass
class SessionContext:
    session: DbSession
    user: User
    membership: MembershipContext | None


class AuthService:
    def __init__(self, db: Session, settings: Settings, email: EmailService) -> None:
        self.db = db
        self.settings = settings
        self.email = email

    def write_audit(
        self,
        *,
        action: str,
        request_id: str | None,
        tenant_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        metadata: dict | None = None,
    ) -> None:
        self.db.add(
            AuditEvent(
                action=action,
                tenant_id=tenant_id,
                user_id=user_id,
                request_id=request_id,
                metadata_=metadata or {},
            )
        )

    def _unique_slug(self, name: str) -> str:
        base = slugify(name)
        candidate = base
        i = 0
        while True:
            exists = self.db.scalar(select(Organization.id).where(Organization.slug == candidate))
            if not exists:
                return candidate
            i += 1
            candidate = f"{base}-{i}"

    def create_session(
        self,
        *,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID | None,
        user_agent: str | None,
        ip_address: str | None,
    ) -> tuple[DbSession, str]:
        token = generate_token(32)
        row = DbSession(
            user_id=user_id,
            tenant_id=tenant_id,
            token_hash=hash_token(token),
            expires_at=utcnow() + timedelta(days=self.settings.session_ttl_days),
            user_agent=user_agent,
            ip_address=ip_address,
        )
        self.db.add(row)
        self.db.flush()
        return row, token

    def get_session_by_token(self, token: str) -> SessionContext | None:
        token_hash = hash_token(token)
        session = self.db.scalar(
            select(DbSession).where(
                and_(
                    DbSession.token_hash == token_hash,
                    DbSession.revoked_at.is_(None),
                    DbSession.expires_at > utcnow(),
                )
            )
        )
        if not session:
            return None
        user = self.db.get(User, session.user_id)
        if not user or user.status != UserStatus.active:
            return None
        membership = None
        if session.tenant_id:
            member = self.db.scalar(
                select(OrganizationMember).where(
                    and_(
                        OrganizationMember.tenant_id == session.tenant_id,
                        OrganizationMember.user_id == user.id,
                        OrganizationMember.status == MemberStatus.active,
                    )
                )
            )
            if member:
                org = self.db.get(Organization, member.tenant_id)
                if org:
                    membership = MembershipContext(
                        tenant_id=member.tenant_id,
                        role=member.role,
                        status=member.status,
                        organization_name=org.name,
                        organization_slug=org.slug,
                    )
        return SessionContext(session=session, user=user, membership=membership)

    def revoke_session(self, session_id: uuid.UUID) -> None:
        session = self.db.get(DbSession, session_id)
        if session and session.revoked_at is None:
            session.revoked_at = utcnow()

    def _issue_email_verification(self, user: User) -> str:
        token = generate_token(32)
        self.db.add(
            EmailVerificationToken(
                user_id=user.id,
                token_hash=hash_token(token),
                expires_at=utcnow() + timedelta(hours=self.settings.email_verify_ttl_hours),
            )
        )
        verify_url = f"{self.settings.frontend_url}/verify-email?token={token}"
        self.email.send(
            EmailMessage(
                to=user.email,
                subject="Verify your Vridhi email",
                text=(
                    f"Hi {user.name},\n\nVerify your email: {verify_url}\n\n"
                    f"This link expires in {self.settings.email_verify_ttl_hours} hours."
                ),
            )
        )
        return token

    def register(
        self,
        *,
        name: str,
        email: str,
        password: str,
        organization_name: str | None,
        request_id: str | None,
        user_agent: str | None,
        ip_address: str | None,
    ) -> tuple[User, Organization | None, OrganizationMember | None, str]:
        email_norm = email.lower().strip()
        existing = self.db.scalar(select(User).where(User.email == email_norm))
        if existing:
            raise AppError("EMAIL_IN_USE", "An account with this email already exists.", 409)

        user = User(
            email=email_norm,
            password_hash=hash_password(password),
            name=name.strip(),
        )
        self.db.add(user)
        self.db.flush()

        org = None
        membership = None
        if organization_name and organization_name.strip():
            org = Organization(name=organization_name.strip(), slug=self._unique_slug(organization_name))
            self.db.add(org)
            self.db.flush()
            membership = OrganizationMember(
                tenant_id=org.id,
                user_id=user.id,
                role=MemberRole.owner,
            )
            self.db.add(membership)

        verify_token = self._issue_email_verification(user)
        session, token = self.create_session(
            user_id=user.id,
            tenant_id=org.id if org else None,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        self.write_audit(
            action="auth.register",
            request_id=request_id,
            tenant_id=org.id if org else None,
            user_id=user.id,
            metadata={"session_id": str(session.id)},
        )
        self.db.commit()
        self.db.refresh(user)
        if org:
            self.db.refresh(org)
        if membership:
            self.db.refresh(membership)
        setattr(user, "_raw_verify_token", verify_token)
        return user, org, membership, token

    def login(
        self,
        *,
        email: str,
        password: str,
        request_id: str | None,
        user_agent: str | None,
        ip_address: str | None,
    ) -> tuple[User, MembershipContext | None, str]:
        email_norm = email.lower().strip()
        user = self.db.scalar(select(User).where(User.email == email_norm))
        if not user or not verify_password(password, user.password_hash):
            raise AppError("INVALID_CREDENTIALS", "Invalid email or password.", 401)
        if user.status != UserStatus.active:
            raise AppError("USER_DEACTIVATED", "This account has been deactivated.", 403)

        membership_row = self.db.scalar(
            select(OrganizationMember)
            .where(
                and_(
                    OrganizationMember.user_id == user.id,
                    OrganizationMember.status == MemberStatus.active,
                )
            )
            .order_by(OrganizationMember.created_at.asc())
        )
        tenant_id = membership_row.tenant_id if membership_row else None
        session, token = self.create_session(
            user_id=user.id,
            tenant_id=tenant_id,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        membership_ctx = None
        if membership_row:
            org = self.db.get(Organization, membership_row.tenant_id)
            if org:
                membership_ctx = MembershipContext(
                    tenant_id=membership_row.tenant_id,
                    role=membership_row.role,
                    status=membership_row.status,
                    organization_name=org.name,
                    organization_slug=org.slug,
                )
        self.write_audit(
            action="auth.login",
            request_id=request_id,
            tenant_id=tenant_id,
            user_id=user.id,
            metadata={"session_id": str(session.id)},
        )
        self.db.commit()
        return user, membership_ctx, token

    def logout(self, *, session_id: uuid.UUID, user_id: uuid.UUID, tenant_id: uuid.UUID | None, request_id: str | None) -> None:
        self.revoke_session(session_id)
        self.write_audit(
            action="auth.logout",
            request_id=request_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        self.db.commit()

    def forgot_password(self, *, email: str, request_id: str | None) -> None:
        email_norm = email.lower().strip()
        user = self.db.scalar(select(User).where(User.email == email_norm))
        # Always succeed to avoid account enumeration.
        if user and user.status == UserStatus.active:
            token = generate_token(32)
            self.db.add(
                PasswordResetToken(
                    user_id=user.id,
                    token_hash=hash_token(token),
                    expires_at=utcnow() + timedelta(hours=self.settings.password_reset_ttl_hours),
                )
            )
            reset_url = f"{self.settings.frontend_url}/reset-password?token={token}"
            self.email.send(
                EmailMessage(
                    to=user.email,
                    subject="Reset your Vridhi password",
                    text=(
                        f"Hi {user.name},\n\nReset your password: {reset_url}\n\n"
                        f"This link expires in {self.settings.password_reset_ttl_hours} hour(s)."
                    ),
                )
            )
            self.write_audit(
                action="auth.forgot_password",
                request_id=request_id,
                user_id=user.id,
            )
            self.db.commit()

    def reset_password(self, *, token: str, password: str, request_id: str | None) -> None:
        row = self.db.scalar(
            select(PasswordResetToken).where(
                and_(
                    PasswordResetToken.token_hash == hash_token(token),
                    PasswordResetToken.used_at.is_(None),
                    PasswordResetToken.expires_at > utcnow(),
                )
            )
        )
        if not row:
            raise AppError("INVALID_TOKEN", "This reset link is invalid or expired.", 400)
        user = self.db.get(User, row.user_id)
        if not user:
            raise AppError("INVALID_TOKEN", "This reset link is invalid or expired.", 400)
        user.password_hash = hash_password(password)
        row.used_at = utcnow()
        # Revoke all sessions for safety.
        sessions = self.db.scalars(
            select(DbSession).where(
                and_(DbSession.user_id == user.id, DbSession.revoked_at.is_(None))
            )
        ).all()
        for s in sessions:
            s.revoked_at = utcnow()
        self.write_audit(action="auth.reset_password", request_id=request_id, user_id=user.id)
        self.db.commit()

    def verify_email(self, *, token: str, request_id: str | None) -> None:
        row = self.db.scalar(
            select(EmailVerificationToken).where(
                and_(
                    EmailVerificationToken.token_hash == hash_token(token),
                    EmailVerificationToken.used_at.is_(None),
                    EmailVerificationToken.expires_at > utcnow(),
                )
            )
        )
        if not row:
            raise AppError("INVALID_TOKEN", "This verification link is invalid or expired.", 400)
        user = self.db.get(User, row.user_id)
        if not user:
            raise AppError("INVALID_TOKEN", "This verification link is invalid or expired.", 400)
        user.email_verified_at = utcnow()
        row.used_at = utcnow()
        self.write_audit(action="auth.verify_email", request_id=request_id, user_id=user.id)
        self.db.commit()

    def resend_email_verification(self, *, user_id: uuid.UUID, request_id: str | None) -> str:
        user = self.db.get(User, user_id)
        if not user:
            raise AppError("UNAUTHORIZED", "Authentication required.", 401)
        if user.email_verified_at is not None:
            raise AppError("ALREADY_VERIFIED", "Email is already verified.", 400)
        token = self._issue_email_verification(user)
        self.write_audit(action="auth.resend_verification", request_id=request_id, user_id=user.id)
        self.db.commit()
        return token

    def google_auth_url(self, state: str) -> str:
        if not self.settings.google_oauth_configured:
            raise AppError(
                "GOOGLE_OAUTH_NOT_CONFIGURED",
                "Google login is not configured for this environment.",
                501,
            )
        params = {
            "client_id": self.settings.google_client_id,
            "redirect_uri": self.settings.google_redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "access_type": "online",
            "include_granted_scopes": "true",
            "state": state,
            "prompt": "select_account",
        }
        return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    def google_callback(
        self,
        *,
        code: str,
        request_id: str | None,
        user_agent: str | None,
        ip_address: str | None,
    ) -> tuple[User, MembershipContext | None, str]:
        if not self.settings.google_oauth_configured:
            raise AppError(
                "GOOGLE_OAUTH_NOT_CONFIGURED",
                "Google login is not configured for this environment.",
                501,
            )
        with httpx.Client(timeout=20.0) as client:
            token_resp = client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": self.settings.google_client_id,
                    "client_secret": self.settings.google_client_secret,
                    "redirect_uri": self.settings.google_redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            if token_resp.status_code >= 400:
                raise AppError("GOOGLE_AUTH_FAILED", "We could not authenticate with Google.", 401)
            access_token = token_resp.json().get("access_token")
            if not access_token:
                raise AppError("GOOGLE_AUTH_FAILED", "We could not authenticate with Google.", 401)
            userinfo = client.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if userinfo.status_code >= 400:
                raise AppError("GOOGLE_AUTH_FAILED", "We could not authenticate with Google.", 401)
            info = userinfo.json()

        provider_user_id = str(info.get("sub") or "")
        email = str(info.get("email") or "").lower().strip()
        name = str(info.get("name") or email or "Google User")
        email_verified = bool(info.get("email_verified"))
        if not provider_user_id or not email:
            raise AppError("GOOGLE_AUTH_FAILED", "We could not authenticate with Google.", 401)

        oauth = self.db.scalar(
            select(OAuthAccount).where(
                and_(
                    OAuthAccount.provider == OAuthProvider.google,
                    OAuthAccount.provider_user_id == provider_user_id,
                )
            )
        )
        if oauth:
            user = self.db.get(User, oauth.user_id)
            if not user:
                raise AppError("GOOGLE_AUTH_FAILED", "We could not authenticate with Google.", 401)
        else:
            user = self.db.scalar(select(User).where(User.email == email))
            if not user:
                user = User(
                    email=email,
                    name=name,
                    password_hash=None,
                    email_verified_at=utcnow() if email_verified else None,
                )
                self.db.add(user)
                self.db.flush()
            elif email_verified and user.email_verified_at is None:
                user.email_verified_at = utcnow()
            self.db.add(
                OAuthAccount(
                    user_id=user.id,
                    provider=OAuthProvider.google,
                    provider_user_id=provider_user_id,
                )
            )

        if user.status != UserStatus.active:
            raise AppError("USER_DEACTIVATED", "This account has been deactivated.", 403)

        membership_row = self.db.scalar(
            select(OrganizationMember)
            .where(
                and_(
                    OrganizationMember.user_id == user.id,
                    OrganizationMember.status == MemberStatus.active,
                )
            )
            .order_by(OrganizationMember.created_at.asc())
        )
        tenant_id = membership_row.tenant_id if membership_row else None
        _, token = self.create_session(
            user_id=user.id,
            tenant_id=tenant_id,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        membership_ctx = None
        if membership_row:
            org = self.db.get(Organization, membership_row.tenant_id)
            if org:
                membership_ctx = MembershipContext(
                    tenant_id=membership_row.tenant_id,
                    role=membership_row.role,
                    status=membership_row.status,
                    organization_name=org.name,
                    organization_slug=org.slug,
                )
        self.write_audit(
            action="auth.google_login",
            request_id=request_id,
            tenant_id=tenant_id,
            user_id=user.id,
        )
        self.db.commit()
        self.db.refresh(user)
        return user, membership_ctx, token


class OrgService:
    def __init__(self, db: Session, settings: Settings, email: EmailService, auth: AuthService) -> None:
        self.db = db
        self.settings = settings
        self.email = email
        self.auth = auth

    def create_organization(
        self,
        *,
        user: User,
        name: str,
        session: DbSession,
        request_id: str | None,
    ) -> Organization:
        existing = self.db.scalar(
            select(OrganizationMember).where(
                and_(
                    OrganizationMember.user_id == user.id,
                    OrganizationMember.status == MemberStatus.active,
                )
            )
        )
        if existing:
            raise AppError(
                "ORG_ALREADY_EXISTS",
                "You already belong to an organization in Phase 1.",
                409,
            )
        org = Organization(name=name.strip(), slug=self.auth._unique_slug(name))
        self.db.add(org)
        self.db.flush()
        self.db.add(
            OrganizationMember(
                tenant_id=org.id,
                user_id=user.id,
                role=MemberRole.owner,
            )
        )
        session.tenant_id = org.id
        self.auth.write_audit(
            action="organization.created",
            request_id=request_id,
            tenant_id=org.id,
            user_id=user.id,
        )
        self.db.commit()
        self.db.refresh(org)
        return org

    def get_my_organization(self, tenant_id: uuid.UUID) -> Organization:
        org = self.db.get(Organization, tenant_id)
        if not org:
            raise AppError("ORG_NOT_FOUND", "Organization not found.", 404)
        return org

    def patch_organization(
        self, *, tenant_id: uuid.UUID, name: str | None, user_id: uuid.UUID, request_id: str | None
    ) -> Organization:
        org = self.get_my_organization(tenant_id)
        if name:
            org.name = name.strip()
        self.auth.write_audit(
            action="organization.updated",
            request_id=request_id,
            tenant_id=tenant_id,
            user_id=user_id,
            metadata={"name": org.name},
        )
        self.db.commit()
        self.db.refresh(org)
        return org

    def list_users(self, tenant_id: uuid.UUID) -> list[tuple[OrganizationMember, User]]:
        rows = self.db.execute(
            select(OrganizationMember, User)
            .join(User, User.id == OrganizationMember.user_id)
            .where(OrganizationMember.tenant_id == tenant_id)
            .order_by(OrganizationMember.created_at.asc())
        ).all()
        return [(m, u) for m, u in rows]

    def invite_user(
        self,
        *,
        tenant_id: uuid.UUID,
        invited_by: User,
        email: str,
        role: MemberRole,
        request_id: str | None,
    ) -> Invite:
        if role == MemberRole.owner:
            raise AppError("INVALID_ROLE", "Owner role cannot be invited. Transfer ownership later.", 400)
        email_norm = email.lower().strip()
        existing_member = self.db.execute(
            select(OrganizationMember)
            .join(User, User.id == OrganizationMember.user_id)
            .where(
                and_(
                    OrganizationMember.tenant_id == tenant_id,
                    User.email == email_norm,
                    OrganizationMember.status == MemberStatus.active,
                )
            )
        ).first()
        if existing_member:
            raise AppError("ALREADY_MEMBER", "This user is already a member of the organization.", 409)

        token = generate_token(32)
        invite = Invite(
            tenant_id=tenant_id,
            email=email_norm,
            role=role,
            token_hash=hash_token(token),
            invited_by_user_id=invited_by.id,
            expires_at=utcnow() + timedelta(days=self.settings.invite_ttl_days),
        )
        self.db.add(invite)
        org = self.get_my_organization(tenant_id)
        accept_url = f"{self.settings.frontend_url}/invite/accept?token={token}"
        self.email.send(
            EmailMessage(
                to=email_norm,
                subject=f"Join {org.name} on Vridhi",
                text=(
                    f"You have been invited to join {org.name} on Vridhi as {role.value}.\n\n"
                    f"Accept invitation: {accept_url}\n\n"
                    f"This link expires in {self.settings.invite_ttl_days} days."
                ),
            )
        )
        self.auth.write_audit(
            action="user.invited",
            request_id=request_id,
            tenant_id=tenant_id,
            user_id=invited_by.id,
            metadata={"email": email_norm, "role": role.value},
        )
        self.db.commit()
        self.db.refresh(invite)
        # Attach raw token only for response in log email; API does not return token.
        invite._raw_token = token  # type: ignore[attr-defined]
        return invite

    def resend_invite(
        self, *, tenant_id: uuid.UUID, invite_id: uuid.UUID, actor_id: uuid.UUID, request_id: str | None
    ) -> Invite:
        invite = self.db.get(Invite, invite_id)
        if not invite or invite.tenant_id != tenant_id:
            raise AppError("INVITE_NOT_FOUND", "Invite not found.", 404)
        if invite.status != InviteStatus.pending:
            raise AppError("INVITE_NOT_PENDING", "Only pending invites can be resent.", 400)
        token = generate_token(32)
        invite.token_hash = hash_token(token)
        invite.expires_at = utcnow() + timedelta(days=self.settings.invite_ttl_days)
        org = self.get_my_organization(tenant_id)
        accept_url = f"{self.settings.frontend_url}/invite/accept?token={token}"
        self.email.send(
            EmailMessage(
                to=invite.email,
                subject=f"Join {org.name} on Vridhi",
                text=f"Your invitation was resent.\n\nAccept: {accept_url}",
            )
        )
        self.auth.write_audit(
            action="user.invite_resent",
            request_id=request_id,
            tenant_id=tenant_id,
            user_id=actor_id,
            metadata={"invite_id": str(invite.id)},
        )
        self.db.commit()
        self.db.refresh(invite)
        invite._raw_token = token  # type: ignore[attr-defined]
        return invite

    def list_invites(self, tenant_id: uuid.UUID) -> list[Invite]:
        return list(
            self.db.scalars(
                select(Invite)
                .where(
                    and_(
                        Invite.tenant_id == tenant_id,
                        Invite.status == InviteStatus.pending,
                    )
                )
                .order_by(Invite.created_at.desc())
            ).all()
        )

    def revoke_invite(
        self, *, tenant_id: uuid.UUID, invite_id: uuid.UUID, actor_id: uuid.UUID, request_id: str | None
    ) -> None:
        invite = self.db.get(Invite, invite_id)
        if not invite or invite.tenant_id != tenant_id:
            raise AppError("INVITE_NOT_FOUND", "Invite not found.", 404)
        invite.status = InviteStatus.revoked
        invite.revoked_at = utcnow()
        self.auth.write_audit(
            action="user.invite_revoked",
            request_id=request_id,
            tenant_id=tenant_id,
            user_id=actor_id,
            metadata={"invite_id": str(invite.id)},
        )
        self.db.commit()

    def accept_invite(
        self,
        *,
        token: str,
        name: str | None,
        password: str | None,
        request_id: str | None,
        user_agent: str | None,
        ip_address: str | None,
    ) -> tuple[User, MembershipContext, str]:
        invite = self.db.scalar(
            select(Invite).where(
                and_(
                    Invite.token_hash == hash_token(token),
                    Invite.status == InviteStatus.pending,
                    Invite.expires_at > utcnow(),
                )
            )
        )
        if not invite:
            raise AppError("INVALID_TOKEN", "This invite is invalid or expired.", 400)

        user = self.db.scalar(select(User).where(User.email == invite.email))
        if not user:
            if not name or not password:
                raise AppError(
                    "ACCOUNT_REQUIRED",
                    "Create an account with name and password to accept this invite.",
                    400,
                )
            user = User(
                email=invite.email,
                name=name.strip(),
                password_hash=hash_password(password),
                email_verified_at=utcnow(),
            )
            self.db.add(user)
            self.db.flush()
        elif password and not user.password_hash:
            user.password_hash = hash_password(password)

        existing = self.db.scalar(
            select(OrganizationMember).where(
                and_(
                    OrganizationMember.tenant_id == invite.tenant_id,
                    OrganizationMember.user_id == user.id,
                )
            )
        )
        if existing:
            existing.status = MemberStatus.active
            existing.role = invite.role
            membership = existing
        else:
            membership = OrganizationMember(
                tenant_id=invite.tenant_id,
                user_id=user.id,
                role=invite.role,
            )
            self.db.add(membership)

        invite.status = InviteStatus.accepted
        invite.accepted_at = utcnow()
        org = self.get_my_organization(invite.tenant_id)
        _, session_token = self.auth.create_session(
            user_id=user.id,
            tenant_id=invite.tenant_id,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        self.auth.write_audit(
            action="user.invite_accepted",
            request_id=request_id,
            tenant_id=invite.tenant_id,
            user_id=user.id,
        )
        self.db.commit()
        self.db.refresh(user)
        ctx = MembershipContext(
            tenant_id=membership.tenant_id,
            role=membership.role,
            status=membership.status,
            organization_name=org.name,
            organization_slug=org.slug,
        )
        return user, ctx, session_token

    def patch_member(
        self,
        *,
        tenant_id: uuid.UUID,
        member_user_id: uuid.UUID,
        actor: OrganizationMember,
        role: MemberRole | None,
        status: MemberStatus | None,
        request_id: str | None,
    ) -> OrganizationMember:
        member = self.db.scalar(
            select(OrganizationMember).where(
                and_(
                    OrganizationMember.tenant_id == tenant_id,
                    OrganizationMember.user_id == member_user_id,
                )
            )
        )
        if not member:
            raise AppError("USER_NOT_FOUND", "User not found in this organization.", 404)
        if member.role == MemberRole.owner and actor.user_id != member.user_id:
            if role or (status and status != MemberStatus.active):
                raise AppError("FORBIDDEN", "Owner membership cannot be changed this way.", 403)
        if role:
            if role == MemberRole.owner:
                raise AppError("INVALID_ROLE", "Cannot assign owner via patch in Phase 1.", 400)
            member.role = role
        if status:
            member.status = status
        self.auth.write_audit(
            action="user.updated",
            request_id=request_id,
            tenant_id=tenant_id,
            user_id=actor.user_id,
            metadata={"target_user_id": str(member_user_id), "role": member.role.value, "status": member.status.value},
        )
        self.db.commit()
        self.db.refresh(member)
        return member

    def deactivate_member(
        self,
        *,
        tenant_id: uuid.UUID,
        member_user_id: uuid.UUID,
        actor: OrganizationMember,
        request_id: str | None,
    ) -> None:
        if actor.user_id == member_user_id:
            raise AppError("FORBIDDEN", "You cannot deactivate yourself.", 403)
        member = self.db.scalar(
            select(OrganizationMember).where(
                and_(
                    OrganizationMember.tenant_id == tenant_id,
                    OrganizationMember.user_id == member_user_id,
                )
            )
        )
        if not member:
            raise AppError("USER_NOT_FOUND", "User not found in this organization.", 404)
        if member.role == MemberRole.owner:
            raise AppError("FORBIDDEN", "Cannot deactivate the owner.", 403)
        member.status = MemberStatus.deactivated
        self.auth.write_audit(
            action="user.removed",
            request_id=request_id,
            tenant_id=tenant_id,
            user_id=actor.user_id,
            metadata={"target_user_id": str(member_user_id)},
        )
        self.db.commit()
