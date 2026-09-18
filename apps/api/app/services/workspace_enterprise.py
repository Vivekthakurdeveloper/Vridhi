"""Enterprise Google Workspace auth foundation (Phase F).

Domain-Wide Delegation lets a per-tenant service account impersonate any
employee in a Google Workspace domain. This module owns exactly one
capability: given a tenant and an employee's email, return a working
Google API access token for that employee. It does not decide what to do
with that token — later sub-projects (a sync engine, the Chat connector)
consume this module, they do not extend it.

Deliberately separate from `google_oauth.py`, which does human-consent
authorization-code flows. Domain-Wide Delegation is server-to-server: no
consent screen, no redirect URI, just a service-account key and Google's
own `google-auth` library.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.errors import AppError

logger = logging.getLogger(__name__)

REQUIRED_KEY_FIELDS = ("type", "project_id", "private_key", "client_email", "client_id")


def validate_service_account_key(raw: str) -> dict[str, Any]:
    """Parse and sanity-check a service-account JSON key.

    Raises AppError(INVALID_SERVICE_ACCOUNT_KEY) rather than a bare
    exception so the router can return a clean 400 with a message the
    admin can act on, instead of a stack trace.
    """
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise AppError(
            "INVALID_SERVICE_ACCOUNT_KEY",
            "This doesn't look like a valid service account key — make sure "
            "you copied the entire JSON file.",
            400,
        ) from exc

    if not isinstance(parsed, dict):
        raise AppError(
            "INVALID_SERVICE_ACCOUNT_KEY",
            "This doesn't look like a valid service account key — make sure "
            "you copied the entire JSON file.",
            400,
        )

    missing = [f for f in REQUIRED_KEY_FIELDS if not parsed.get(f)]
    if missing or parsed.get("type") != "service_account":
        raise AppError(
            "INVALID_SERVICE_ACCOUNT_KEY",
            "This doesn't look like a valid service account key — make sure "
            "you copied the entire JSON file.",
            400,
        )

    return parsed


def translate_workspace_enterprise_error(raw_message: str, *, client_id: str | None = None) -> str:
    """Map a known Google Domain-Wide Delegation failure signature to an
    actionable message. Falls back to a generic, safe message for anything
    not recognized — never invent a misleading explanation for an error we
    don't actually recognize, and never surface Google's raw error text to
    the client, since this endpoint is readable by any org member and the
    raw text can contain internal details. The raw text is still preserved
    separately (see `verify_directory_access`'s `raw_detail` attribute) for
    support debugging via `last_error`.
    """
    lowered = raw_message.lower()

    if "unauthorized_client" in lowered or "not authorized for any of the scopes" in lowered:
        cid = client_id or "<shown on the setup page>"
        return (
            "Domain-Wide Delegation isn't authorized for this service account yet. "
            "In Google Admin Console → Security → API Controls → "
            f"Domain-wide Delegation, authorize Client ID {cid} for scope "
            "admin.directory.user.readonly."
        )

    if "invalid_grant" in lowered and ("invalid email" in lowered or "user id" in lowered):
        return (
            "Couldn't act as that user — check this is an active user in "
            "your Google Workspace."
        )

    if "403" in lowered and ("forbidden" in lowered or "not authorized" in lowered):
        return (
            "This account doesn't have Super Admin privileges. Directory access "
            "requires impersonating a Super Admin — use a different admin "
            "account for verification."
        )

    return "Verification failed. Check the service account configuration and try again."


def mint_impersonated_token(
    key_dict: dict[str, Any], subject_email: str, scopes: list[str], *, mock: bool = False
) -> str:
    """Return an access token that lets the caller act as `subject_email`.

    In mock mode, short-circuits to a fixed fake token — no real Google
    call, no real key required. Mirrors the MOCK_ACCESS_TOKEN pattern
    already used by services/drive.py and services/gmail.py.
    """
    if mock:
        return "mock-workspace-enterprise-access-token"

    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_info(
        key_dict, scopes=scopes
    ).with_subject(subject_email)
    credentials.refresh(Request())
    return credentials.token


def verify_directory_access(
    key_dict: dict[str, Any], admin_email: str, domain: str, scopes: list[str], *, mock: bool = False
) -> None:
    """Prove Domain-Wide Delegation actually works by impersonating
    `admin_email` and listing 1 user in `domain` via the Admin Directory
    API, using exactly `scopes` to mint the test token — whatever the
    caller passes here is what actually gets exercised, and is therefore
    what's safe to record as `verified_scopes`.

    Raises AppError(WORKSPACE_ENTERPRISE_VERIFICATION_FAILED) with a
    translated message on any failure. Never raises Google's raw error
    directly to a caller outside this module — but the raw error text
    (Google's actual response body when available) is attached to the
    raised AppError as `.raw_detail` so a caller can persist it for
    support debugging without ever surfacing it to the API client.

    Retries exactly once on a transient network/DNS-level failure
    (httpx.RequestError) before giving up, per the spec's error table.
    An HTTP error response (e.g. 403) is not retried — it's not transient.
    """
    if mock:
        return  # Fixture mode: submitting a well-formed key always "works".

    import time

    import httpx

    def _attempt() -> None:
        token = mint_impersonated_token(key_dict, admin_email, scopes, mock=False)
        resp = httpx.get(
            "https://admin.googleapis.com/admin/directory/v1/users",
            params={"domain": domain, "maxResults": 1},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        resp.raise_for_status()

    try:
        try:
            _attempt()
        except httpx.RequestError:
            time.sleep(1)
            _attempt()
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see module docstring
        if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None and exc.response.text:
            raw = exc.response.text
        else:
            raw = str(exc)
        client_id = key_dict.get("client_id")
        friendly = translate_workspace_enterprise_error(raw, client_id=client_id)
        # Raw Google error text is logged server-side only — it can contain
        # internal project/config identifiers and must never reach a client.
        # (last_error, which IS client-readable by any org member, gets the
        # translated `friendly` message instead — see WorkspaceEnterpriseService._verify.)
        logger.warning(
            "workspace_enterprise.verification_failed",
            extra={"operation": "workspace_enterprise_verify", "raw_detail": raw},
        )
        err = AppError("WORKSPACE_ENTERPRISE_VERIFICATION_FAILED", friendly, 400)
        err.raw_detail = raw
        raise err from exc


from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User, WorkspaceEnterpriseConnection
from app.security import WorkspaceEnterpriseStatus, utcnow
from app.services.tokens import TokenStore, get_token_store


class WorkspaceEnterpriseService:
    def __init__(self, db: Session, tokens: TokenStore, *, is_mock: bool, scopes: list[str]):
        self.db = db
        self.tokens = tokens
        self.is_mock = is_mock
        self.scopes = scopes

    def get_connection(self, tenant_id: UUID) -> WorkspaceEnterpriseConnection | None:
        return self.db.scalar(
            select(WorkspaceEnterpriseConnection).where(
                WorkspaceEnterpriseConnection.tenant_id == tenant_id
            )
        )

    def connection_detail(self, tenant_id: UUID) -> dict[str, Any]:
        conn = self.get_connection(tenant_id)
        if not conn:
            return {"connected": False, "status": None}
        return {
            "connected": conn.status == WorkspaceEnterpriseStatus.verified,
            "status": conn.status.value,
            "google_domain": conn.google_domain,
            "service_account_email": conn.service_account_email,
            "verified_scopes": conn.verified_scopes,
            "last_verified_at": conn.last_verified_at,
            "last_error": conn.last_error,
        }

    def submit_and_verify(
        self, *, tenant_id: UUID, user_id: UUID, google_domain: str, raw_key: str, admin_email: str
    ) -> WorkspaceEnterpriseConnection:
        key_dict = validate_service_account_key(raw_key)

        conn = self.get_connection(tenant_id)
        if not conn:
            # No previously-working credential to protect: persist first
            # (as pending_verification), then verify, updating status
            # either way.
            conn = WorkspaceEnterpriseConnection(
                id=uuid4(),
                tenant_id=tenant_id,
                google_domain=google_domain,
                service_account_email=key_dict["client_email"],
                encrypted_key=self.tokens.encrypt(raw_key),
                created_by_user_id=user_id,
            )
            self.db.add(conn)
            conn.status = WorkspaceEnterpriseStatus.pending_verification
            conn.last_error = None
            self.db.commit()
            self.db.refresh(conn)

            self._verify(conn, key_dict, admin_email)
            return conn

        # Existing connection: there IS a previously-working credential.
        # Verify the NEW key/domain first, before touching the stored row,
        # so a failed re-submit can never destroy a working credential.
        try:
            self._verify(conn, key_dict, admin_email, domain=google_domain)
        except AppError:
            # _verify() already set status=error and last_error and
            # committed, without touching encrypted_key/google_domain/
            # service_account_email — the old, working values survive.
            raise

        # Verification succeeded: now it's safe to overwrite the stored key.
        conn.google_domain = google_domain
        conn.service_account_email = key_dict["client_email"]
        conn.encrypted_key = self.tokens.encrypt(raw_key)
        conn.created_by_user_id = user_id
        self.db.commit()
        self.db.refresh(conn)
        return conn

    def verify(self, *, tenant_id: UUID, admin_email: str) -> WorkspaceEnterpriseConnection:
        conn = self.get_connection(tenant_id)
        if not conn:
            raise AppError(
                "WORKSPACE_ENTERPRISE_NOT_CONFIGURED", "Submit a service account key first.", 400
            )
        if conn.status == WorkspaceEnterpriseStatus.disabled or not conn.encrypted_key:
            raise AppError(
                "WORKSPACE_ENTERPRISE_NOT_CONFIGURED",
                "This connection has been disabled or has no valid credentials. "
                "Submit a new service account key to reconnect.",
                400,
            )
        key_dict = json.loads(self.tokens.decrypt(conn.encrypted_key))
        self._verify(conn, key_dict, admin_email)
        return conn

    def _verify(
        self,
        conn: WorkspaceEnterpriseConnection,
        key_dict: dict[str, Any],
        admin_email: str,
        *,
        domain: str | None = None,
    ) -> None:
        """Run directory verification and persist the outcome onto `conn`.

        `domain` lets a caller verify against a domain that hasn't been
        written onto `conn` yet (the re-submit flow in submit_and_verify);
        it defaults to `conn.google_domain` for the already-persisted case
        (new connections, and the standalone re-verify endpoint). Either
        way, this method never assigns conn.google_domain/encrypted_key/
        service_account_email itself — only status/verified_scopes/
        last_verified_at/last_error — so callers stay in full control of
        when (or whether) the stored credential fields get overwritten.
        """
        target_domain = domain if domain is not None else conn.google_domain
        try:
            verify_directory_access(
                key_dict, admin_email, target_domain, self.scopes, mock=self.is_mock
            )
        except AppError as exc:
            conn.status = WorkspaceEnterpriseStatus.error
            # last_error is returned by GET /v1/enterprise/google-workspace,
            # which any org member can read (gated by require_tenant, not
            # admin) — so only the translated, safe, actionable message goes
            # here. The raw Google error text (exc.raw_detail) is logged
            # server-side only (see verify_directory_access), never
            # persisted or returned to a client.
            conn.last_error = exc.message
            self.db.commit()
            raise
        conn.status = WorkspaceEnterpriseStatus.verified
        conn.verified_scopes = " ".join(self.scopes)
        conn.last_verified_at = utcnow()
        conn.last_error = None
        self.db.commit()

    def disable(self, *, tenant_id: UUID) -> None:
        conn = self.get_connection(tenant_id)
        if not conn:
            return
        conn.status = WorkspaceEnterpriseStatus.disabled
        # Same pattern as Drive's disconnect(): remove the sensitive
        # credential entirely rather than leaving it at rest, so a
        # disabled connection can't be silently resurrected by calling
        # verify() again. encrypted_key is nullable=False, so clear it to
        # "" rather than None.
        conn.encrypted_key = ""
        conn.updated_at = utcnow()
        self.db.commit()


def get_admin_impersonated_token(db: Session, tenant_id: UUID, scopes: list[str]) -> str:
    """Return a working admin-level token for this tenant's Domain-Wide
    Delegation connection, impersonating the admin who originally
    verified it. This is the one place any consumer (Groups sync today,
    future Chat/enterprise-Gmail sync) gets an admin-level token -- do
    not duplicate impersonation logic elsewhere.
    """
    from app.config import get_settings

    conn = db.scalar(
        select(WorkspaceEnterpriseConnection).where(
            WorkspaceEnterpriseConnection.tenant_id == tenant_id,
            WorkspaceEnterpriseConnection.status == WorkspaceEnterpriseStatus.verified,
        )
    )
    if not conn:
        raise AppError(
            "WORKSPACE_ENTERPRISE_NOT_CONFIGURED",
            "This organization has no verified Google Workspace connection.",
            400,
        )
    admin_email = db.scalar(select(User.email).where(User.id == conn.created_by_user_id))
    if not admin_email:
        raise AppError(
            "WORKSPACE_ENTERPRISE_NOT_CONFIGURED",
            "The admin who set up this connection no longer exists.",
            400,
        )

    tokens = get_token_store()
    key_dict = json.loads(tokens.decrypt(conn.encrypted_key))
    return mint_impersonated_token(
        key_dict, admin_email, scopes, mock=get_settings().workspace_enterprise_is_mock
    )
