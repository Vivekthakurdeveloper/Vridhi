# Enterprise Google Workspace Auth Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin authorize Highwatch/Vridhi, once, to impersonate any employee in their Google Workspace domain via a per-customer service account and Domain-Wide Delegation, and give the rest of the app one reliable function to get a working Google API token for any employee.

**Architecture:** A fully separate module (`services/workspace_enterprise.py` + `routers/workspace_enterprise.py` + one new table) that does not touch Drive/Gmail's existing per-admin OAuth code at all. Uses Google's own `google-auth` library for server-to-server impersonation (`service_account.Credentials(...).with_subject(email)`), not the hand-rolled human-consent `google_oauth.py`. Verification is proven by actually calling Google's Admin Directory API to list one user, impersonating the submitting admin — not just accepting whatever key was uploaded.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, `google-auth` (new dependency), the existing Fernet-based `TokenStore`, httpx for the REST call (matching how Drive/Gmail already call Google APIs directly rather than via `google-api-python-client`).

**Spec:** `docs/superpowers/specs/2026-09-15-enterprise-google-workspace-auth-foundation-design.md`

## Global Constraints

- Never write the service-account key or any minted access token to logs — only non-secret fields (domain, service account email, status) may appear in log lines.
- Never surface a raw Google error to the admin — always translate through the error table in the spec; store the raw error in `last_error` only.
- Do not modify `services/drive.py`, `services/gmail.py`, or `services/google_oauth.py` — this sub-project is additive only.
- One row per tenant (`tenant_id` unique on `workspace_enterprise_connections`), unlike `Connection` which is unique on `(tenant_id, connector_type)`.
- All new admin-mutating endpoints require `require_role(MemberRole.admin)`; the status `GET` only requires `require_tenant`.
- This repo's existing testing convention: pure logic (no DB/network) gets pytest unit tests (see `tests/test_security.py`); DB- and network-touching integration behavior is verified with a bash smoke script against the running docker stack (see `scripts/smoke-phase-e.sh`), not pytest DB fixtures — this repo has no test-DB fixture infrastructure. Follow that split exactly; do not invent a new pytest DB fixture pattern.

---

### Task 1: Status enum + database migration + SQLAlchemy model

**Files:**
- Modify: `apps/api/app/security/__init__.py` (add enum)
- Modify: `apps/api/app/models/__init__.py` (add enum wrapper + model)
- Create: `apps/api/alembic/versions/0006_workspace_enterprise.py`

**Interfaces:**
- Produces: `WorkspaceEnterpriseStatus` enum (`pending_verification`, `verified`, `error`, `disabled`) in `app.security`; `WorkspaceEnterpriseConnection` model in `app.models` with fields `id, tenant_id, google_domain, service_account_email, encrypted_key, status, verified_scopes, last_verified_at, last_error, created_by_user_id, created_at, updated_at`.

- [ ] **Step 1: Add the status enum**

In `apps/api/app/security/__init__.py`, add near `ConnectionStatus` (around line 56):

```python
class WorkspaceEnterpriseStatus(str, enum.Enum):
    pending_verification = "pending_verification"
    verified = "verified"
    error = "error"
    disabled = "disabled"
```

- [ ] **Step 2: Add the model**

In `apps/api/app/models/__init__.py`, add the import to the existing `from app.security import (...)` block:

```python
    WorkspaceEnterpriseStatus,
```

Add the enum wrapper next to the other `*_enum` definitions (near line 62):

```python
workspace_enterprise_status_enum = Enum(
    WorkspaceEnterpriseStatus,
    name="workspace_enterprise_status",
    values_callable=lambda x: [e.value for e in x],
)
```

Add the model (append after `ConnectionCredential`, around line 341):

```python
class WorkspaceEnterpriseConnection(Base):
    """Per-tenant Domain-Wide Delegation credential. One row per org — this
    is an org-wide auth capability, not a per-connector connection, so it
    intentionally does not reuse the Connection/ConnectionCredential shape."""

    __tablename__ = "workspace_enterprise_connections"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="workspace_enterprise_connections_tenant_uidx"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    google_domain: Mapped[str] = mapped_column(Text, nullable=False)
    service_account_email: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[WorkspaceEnterpriseStatus] = mapped_column(
        workspace_enterprise_status_enum,
        nullable=False,
        default=WorkspaceEnterpriseStatus.pending_verification,
    )
    verified_scopes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
```

- [ ] **Step 3: Write the migration**

Create `apps/api/alembic/versions/0006_workspace_enterprise.py`:

```python
"""phase F enterprise google workspace auth foundation (domain-wide delegation)

Revision ID: 0006_workspace_enterprise
Revises: 0005_phase_e_gmail
Create Date: 2026-09-15
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_workspace_enterprise"
down_revision: Union[str, None] = "0005_phase_e_gmail"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE workspace_enterprise_status AS ENUM "
        "('pending_verification', 'verified', 'error', 'disabled')"
    )
    op.create_table(
        "workspace_enterprise_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("google_domain", sa.Text(), nullable=False),
        sa.Column("service_account_email", sa.Text(), nullable=False),
        sa.Column("encrypted_key", sa.Text(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="workspace_enterprise_status", create_type=False),
            nullable=False,
            server_default="pending_verification",
        ),
        sa.Column("verified_scopes", sa.Text(), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", name="workspace_enterprise_connections_tenant_uidx"),
    )


def downgrade() -> None:
    op.drop_table("workspace_enterprise_connections")
    op.execute("DROP TYPE workspace_enterprise_status")
```

- [ ] **Step 4: Run the migration against the running dev stack**

Run: `docker compose up -d` (recreates `api`, which runs `alembic upgrade head` on startup)
Expected: `docker logs vridhi-api-1 --tail 20` shows `Running upgrade 0005_phase_e_gmail -> 0006_workspace_enterprise` with no error, and container becomes healthy.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/security/__init__.py apps/api/app/models/__init__.py apps/api/alembic/versions/0006_workspace_enterprise.py
git commit -m "feat: add workspace_enterprise_connections table and model"
```

---

### Task 2: Config settings + dependency

**Files:**
- Modify: `apps/api/requirements.txt`
- Modify: `apps/api/app/config.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: `settings.workspace_enterprise_mode` (`"mock"` | `"live"`, default `"mock"`), `settings.workspace_enterprise_admin_scopes` (`list[str]` property), `settings.workspace_enterprise_ready` (`bool` property, mirrors `gmail_ready`).

- [ ] **Step 1: Add the dependency**

In `apps/api/requirements.txt`, add alphabetically:

```
google-auth>=2.35.0
```

- [ ] **Step 2: Add settings**

In `apps/api/app/config.py`, add near the Gmail settings block (after the `gmail_allowed_mime` field):

```python
    # --- Phase F: Enterprise Google Workspace auth foundation ---
    # mock = fixture domain/directory listing, no real Google calls;
    # live = real Domain-Wide Delegation via a per-tenant service account.
    workspace_enterprise_mode: str = Field(default="mock", alias="WORKSPACE_ENTERPRISE_MODE")
    workspace_enterprise_scopes: str = Field(
        default="https://www.googleapis.com/auth/admin.directory.user.readonly",
        alias="WORKSPACE_ENTERPRISE_SCOPES",
    )
```

Add the property near `gmail_ready`/`gmail_scope_list`:

```python
    @property
    def workspace_enterprise_is_mock(self) -> bool:
        return self.workspace_enterprise_mode.lower().strip() == "mock"

    @property
    def workspace_enterprise_scope_list(self) -> list[str]:
        return [s for s in self.workspace_enterprise_scopes.split() if s.strip()]
```

- [ ] **Step 3: Document in `.env.example`**

Append after the Gmail block in `.env.example`:

```
# ---------------------------------------------------------------------------
# Phase F — Enterprise Google Workspace auth foundation (Domain-Wide Delegation)
# mock = fixture domain, no real Google calls; live = real per-tenant service account
# ---------------------------------------------------------------------------
WORKSPACE_ENTERPRISE_MODE=mock
WORKSPACE_ENTERPRISE_SCOPES=https://www.googleapis.com/auth/admin.directory.user.readonly
```

- [ ] **Step 4: Verify the app still boots and the new dependency installs**

Run: `docker compose up -d --build` (must be `--build`, not a plain `up -d` — a
plain `up -d` reuses the existing image and will NOT install the new
`google-auth` dependency just added to requirements.txt; that gap would
otherwise go undetected until someone flips to live mode much later)
Expected: `vridhi-api-1` stays healthy (no Pydantic settings validation error), and:
```bash
docker exec vridhi-api-1 python -c "import google.auth; print('ok')"
```
prints `ok`.

- [ ] **Step 5: Commit**

```bash
git add apps/api/requirements.txt apps/api/app/config.py .env.example
git commit -m "feat: add workspace enterprise config settings and google-auth dependency"
```

---

### Task 3: Key validation + error translation (pure logic, TDD)

**Files:**
- Create: `apps/api/app/services/workspace_enterprise.py`
- Test: `apps/api/tests/test_workspace_enterprise.py`

**Interfaces:**
- Produces: `validate_service_account_key(raw: str) -> dict` (raises `AppError` with code `INVALID_SERVICE_ACCOUNT_KEY` on bad input); `translate_workspace_enterprise_error(raw_message: str) -> str`.
- Consumes: `app.errors.AppError` (existing).

- [ ] **Step 1: Write the failing tests**

Create `apps/api/tests/test_workspace_enterprise.py`:

```python
import json

import pytest

from app.errors import AppError
from app.services.workspace_enterprise import (
    translate_workspace_enterprise_error,
    validate_service_account_key,
)


def test_validate_service_account_key_accepts_well_formed_key():
    raw = json.dumps(
        {
            "type": "service_account",
            "project_id": "acme-proj",
            "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
            "client_email": "highwatch-sync@acme-proj.iam.gserviceaccount.com",
            "client_id": "123456789",
        }
    )
    parsed = validate_service_account_key(raw)
    assert parsed["client_email"] == "highwatch-sync@acme-proj.iam.gserviceaccount.com"


def test_validate_service_account_key_rejects_invalid_json():
    with pytest.raises(AppError) as exc:
        validate_service_account_key("not json at all")
    assert exc.value.code == "INVALID_SERVICE_ACCOUNT_KEY"


def test_validate_service_account_key_rejects_missing_fields():
    raw = json.dumps({"type": "service_account", "client_email": "x@y.com"})
    with pytest.raises(AppError) as exc:
        validate_service_account_key(raw)
    assert exc.value.code == "INVALID_SERVICE_ACCOUNT_KEY"


def test_validate_service_account_key_rejects_wrong_type():
    raw = json.dumps(
        {
            "type": "authorized_user",
            "project_id": "acme-proj",
            "private_key": "x",
            "client_email": "x@y.com",
            "client_id": "1",
        }
    )
    with pytest.raises(AppError) as exc:
        validate_service_account_key(raw)
    assert exc.value.code == "INVALID_SERVICE_ACCOUNT_KEY"


def test_translate_unauthorized_client():
    msg = translate_workspace_enterprise_error(
        "unauthorized_client: Client is unauthorized to retrieve access tokens "
        "using this method, or client not authorized for any of the scopes requested.",
        client_id="123456789",
    )
    assert "Domain-Wide Delegation isn't authorized" in msg
    assert "123456789" in msg


def test_translate_invalid_grant():
    msg = translate_workspace_enterprise_error("invalid_grant: Invalid email or User ID.")
    assert "active user" in msg


def test_translate_super_admin_403():
    msg = translate_workspace_enterprise_error("403 Forbidden: Not Authorized to access this resource/api")
    assert "Super Admin" in msg


def test_translate_unknown_error_falls_back_to_raw():
    msg = translate_workspace_enterprise_error("some completely novel google error")
    assert msg == "some completely novel google error"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && pytest tests/test_workspace_enterprise.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.workspace_enterprise'`

- [ ] **Step 3: Write the minimal implementation**

Create `apps/api/app/services/workspace_enterprise.py`:

```python
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
    actionable message. Falls back to the raw message for anything not
    recognized — never invent a misleading explanation for an error we
    don't actually recognize.
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

    return raw_message
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && pytest tests/test_workspace_enterprise.py -v`
Expected: all 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/workspace_enterprise.py apps/api/tests/test_workspace_enterprise.py
git commit -m "feat: add service account key validation and error translation"
```

---

### Task 4: Impersonation + verification logic

**Files:**
- Modify: `apps/api/app/services/workspace_enterprise.py`

**Interfaces:**
- Consumes: `validate_service_account_key`, `translate_workspace_enterprise_error` (Task 3); `app.services.tokens.TokenStore` (existing); `app.models.WorkspaceEnterpriseConnection`, `app.security.WorkspaceEnterpriseStatus`, `app.security.utcnow` (existing).
- Produces: `class WorkspaceEnterpriseService` with `get_connection(tenant_id)`, `connection_detail(tenant_id) -> dict`, `submit_and_verify(*, tenant_id, user_id, google_domain, raw_key) -> WorkspaceEnterpriseConnection`, `verify(*, tenant_id) -> WorkspaceEnterpriseConnection`, `disable(*, tenant_id) -> None`. Also module-level `mint_impersonated_token(key_dict, subject_email, scopes, *, mock=False) -> str` and `verify_directory_access(key_dict, admin_email, domain, *, mock=False) -> None` (raises on failure).

This task is DB- and network-touching, so per the Global Constraints it is verified by the smoke script (Task 8), not a pytest DB fixture — matching how `services/gmail.py` and `services/drive.py` are verified in this codebase. The mock-mode branch is simple enough to sanity-check by reading it; real-mode correctness is what the smoke script (mock mode) and eventual real-Workspace testing (flagged as an open follow-up in the spec) prove.

- [ ] **Step 1: Add the token-minting and verification functions**

Append to `apps/api/app/services/workspace_enterprise.py`:

```python
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
    key_dict: dict[str, Any], admin_email: str, domain: str, *, mock: bool = False
) -> None:
    """Prove Domain-Wide Delegation actually works by impersonating
    `admin_email` and listing 1 user in `domain` via the Admin Directory
    API. Raises AppError(WORKSPACE_ENTERPRISE_VERIFICATION_FAILED) with a
    translated message on any failure. Never raises Google's raw error
    directly to a caller outside this module.
    """
    if mock:
        return  # Fixture mode: submitting a well-formed key always "works".

    import httpx

    scopes = ["https://www.googleapis.com/auth/admin.directory.user.readonly"]
    try:
        token = mint_impersonated_token(key_dict, admin_email, scopes, mock=False)
        resp = httpx.get(
            "https://admin.googleapis.com/admin/directory/v1/users",
            params={"domain": domain, "maxResults": 1},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see module docstring
        raw = str(exc)
        client_id = key_dict.get("client_id")
        friendly = translate_workspace_enterprise_error(raw, client_id=client_id)
        logger.warning(
            "workspace_enterprise.verification_failed",
            extra={"operation": "workspace_enterprise_verify"},
        )
        raise AppError("WORKSPACE_ENTERPRISE_VERIFICATION_FAILED", friendly, 400) from exc
```

- [ ] **Step 2: Add the DB-backed service class**

Append:

```python
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import WorkspaceEnterpriseConnection
from app.security import WorkspaceEnterpriseStatus, utcnow
from app.services.tokens import TokenStore


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
            conn = WorkspaceEnterpriseConnection(
                id=uuid4(),
                tenant_id=tenant_id,
                google_domain=google_domain,
                service_account_email=key_dict["client_email"],
                encrypted_key=self.tokens.encrypt(raw_key),
                created_by_user_id=user_id,
            )
            self.db.add(conn)
        else:
            conn.google_domain = google_domain
            conn.service_account_email = key_dict["client_email"]
            conn.encrypted_key = self.tokens.encrypt(raw_key)
            conn.created_by_user_id = user_id
        conn.status = WorkspaceEnterpriseStatus.pending_verification
        conn.last_error = None
        self.db.commit()
        self.db.refresh(conn)

        self._verify(conn, key_dict, admin_email)
        return conn

    def verify(self, *, tenant_id: UUID, admin_email: str) -> WorkspaceEnterpriseConnection:
        conn = self.get_connection(tenant_id)
        if not conn:
            raise AppError(
                "WORKSPACE_ENTERPRISE_NOT_CONFIGURED", "Submit a service account key first.", 400
            )
        key_dict = json.loads(self.tokens.decrypt(conn.encrypted_key))
        self._verify(conn, key_dict, admin_email)
        return conn

    def _verify(
        self, conn: WorkspaceEnterpriseConnection, key_dict: dict[str, Any], admin_email: str
    ) -> None:
        try:
            verify_directory_access(key_dict, admin_email, conn.google_domain, mock=self.is_mock)
        except AppError as exc:
            conn.status = WorkspaceEnterpriseStatus.error
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
        conn.updated_at = utcnow()
        self.db.commit()
```

- [ ] **Step 3: Sanity-check mock mode with a one-off script**

Run (from repo root, api container):
```bash
docker exec vridhi-api-1 python -c "
from app.services.workspace_enterprise import mint_impersonated_token
print(mint_impersonated_token({}, 'admin@acme.com', ['scope'], mock=True))
"
```
Expected: prints `mock-workspace-enterprise-access-token` with no error — confirms the mock branch never touches `google-auth` or the key contents.

- [ ] **Step 4: Run the existing unit tests to confirm nothing broke**

Run: `cd apps/api && pytest tests/ -v`
Expected: all tests still PASS (the 8 from Task 3 plus the 5 pre-existing).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/workspace_enterprise.py
git commit -m "feat: add domain-wide delegation impersonation and verification"
```

---

### Task 5: API endpoints

**Files:**
- Create: `apps/api/app/routers/workspace_enterprise.py`
- Modify: `apps/api/app/main.py`

**Interfaces:**
- Consumes: `WorkspaceEnterpriseService` (Task 4), `app.deps.require_tenant`/`require_role`, `app.security.MemberRole`, `get_token_store` (existing).
- Produces: `router` (FastAPI `APIRouter`) mounted in `create_app()`.

- [ ] **Step 1: Write the router**

Create `apps/api/app/routers/workspace_enterprise.py`:

```python
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
```

- [ ] **Step 2: Register the router**

In `apps/api/app/main.py`, add the import next to `from app.routers.gmail import router as gmail_router`:

```python
from app.routers.workspace_enterprise import router as workspace_enterprise_router
```

Add the include next to `app.include_router(gmail_router)`:

```python
    app.include_router(workspace_enterprise_router)
```

- [ ] **Step 3: Verify it's registered**

Run: `docker compose up -d` then `curl http://localhost:8000/docs` (or check OpenAPI JSON) and confirm `/v1/enterprise/google-workspace` appears.
Expected: the path is listed; `docker logs vridhi-api-1 --tail 10` shows no startup error.

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/routers/workspace_enterprise.py apps/api/app/main.py
git commit -m "feat: add enterprise google workspace auth endpoints"
```

---

### Task 6: Smoke test (mock mode, end-to-end)

**Files:**
- Create: `scripts/smoke-phase-f.sh`
- Modify: `README.md` (add to the Smoke tests list)

**Interfaces:**
- Consumes: the four endpoints from Task 5, running against the docker stack in `WORKSPACE_ENTERPRISE_MODE=mock`.

- [ ] **Step 1: Write the smoke script**

Create `scripts/smoke-phase-f.sh`:

```bash
#!/usr/bin/env bash
# Phase F smoke: submit a fixture service-account key (mock mode) -> verify -> status -> disable
set -euo pipefail

API="${API_URL:-http://localhost:8000}"
COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

email="phasef-$(date +%s)@example.com"
password="Password123!"

echo "== register =="
curl -sf -c "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d "{\"name\":\"Phase F\",\"email\":\"$email\",\"password\":\"$password\",\"organization_name\":\"Phase F Co\"}" \
  "$API/v1/auth/register" >/dev/null

echo "== status before submit =="
before=$(curl -sf -b "$COOKIE_JAR" "$API/v1/enterprise/google-workspace")
echo "$before" | grep -q '"connected":false' || {
  echo "FAIL: expected not connected before submit"
  echo "$before"
  exit 1
}

echo "== submit (mock) =="
# Built entirely in Python (not interpolated through bash) so the PEM
# newlines round-trip correctly: json.dumps(fixture_key) here escapes the
# real \n characters into the two-char "\n" sequence JSON requires, and
# the outer json.dumps then correctly re-escapes that already-JSON string
# as the value of service_account_key. Interpolating a bash variable
# containing literal \n sequences through a Python triple-quoted string
# would instead turn them into raw newline bytes inside the JSON payload,
# which json.loads() on the API side would reject as an invalid control
# character.
body=$(python3 -c "
import json
fixture_key = {
    'type': 'service_account',
    'project_id': 'acme-proj',
    'private_key': '-----BEGIN PRIVATE KEY-----\nfixture\n-----END PRIVATE KEY-----\n',
    'client_email': 'highwatch-sync@acme-proj.iam.gserviceaccount.com',
    'client_id': '111111111111111111111',
}
print(json.dumps({'google_domain': 'acme.com', 'service_account_key': json.dumps(fixture_key)}))
")
submit=$(curl -sf -b "$COOKIE_JAR" -H 'Content-Type: application/json' -d "$body" \
  "$API/v1/enterprise/google-workspace")
echo "$submit" | grep -q '"status":"verified"' || {
  echo "FAIL: expected verified status after mock-mode submit"
  echo "$submit"
  exit 1
}
echo "$submit" | grep -q '"service_account_email":"highwatch-sync@acme-proj.iam.gserviceaccount.com"' || {
  echo "FAIL: service_account_email not stored/returned correctly"
  echo "$submit"
  exit 1
}

echo "== re-verify =="
curl -sf -b "$COOKIE_JAR" -X POST "$API/v1/enterprise/google-workspace/verify" \
  | grep -q '"status":"verified"' || {
  echo "FAIL: re-verify did not return verified"
  exit 1
}

echo "== disable =="
curl -sf -b "$COOKIE_JAR" -X DELETE "$API/v1/enterprise/google-workspace" >/dev/null
after=$(curl -sf -b "$COOKIE_JAR" "$API/v1/enterprise/google-workspace")
echo "$after" | grep -q '"status":"disabled"' || {
  echo "FAIL: expected disabled after DELETE"
  echo "$after"
  exit 1
}

echo "PHASE F SMOKE PASSED"
```

- [ ] **Step 2: Make it executable and run it**

Run:
```bash
chmod +x scripts/smoke-phase-f.sh
docker compose up -d
API_URL=http://localhost:8000 ./scripts/smoke-phase-f.sh
```
Expected: `PHASE F SMOKE PASSED` printed at the end, no `FAIL:` lines.

- [ ] **Step 3: Add it to the README's smoke test list**

In `README.md`, under the `## Smoke tests` section, add:

```
API_URL=http://localhost:8000 ./scripts/smoke-phase-f.sh
```

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke-phase-f.sh README.md
git commit -m "test: add phase F enterprise workspace smoke test"
```

---

### Task 7: Minimal frontend — submit key, view status, disable

**Files:**
- Modify: `frontend/src/api/workspace.ts`
- Modify: `frontend/src/pages/WorkspacePages.tsx`
- Modify: `frontend/src/types/index.ts`

**Interfaces:**
- Consumes: the four endpoints from Task 5.
- Produces: `workspaceEnterpriseApi` client object; a new "Enterprise (Google Workspace)" card in the Connections page.

- [ ] **Step 1: Add the type**

In `frontend/src/types/index.ts`, add:

```typescript
export interface WorkspaceEnterpriseStatus {
  connected: boolean
  status: string | null
  google_domain: string | null
  service_account_email: string | null
  verified_scopes: string | null
  last_verified_at: string | null
  last_error: string | null
}
```

- [ ] **Step 2: Add the API client**

In `frontend/src/api/workspace.ts`, add near `gmailApi`:

```typescript
export const workspaceEnterpriseApi = {
  get() {
    return apiRequest<WorkspaceEnterpriseStatus>("/v1/enterprise/google-workspace")
  },
  submit(googleDomain: string, serviceAccountKey: string) {
    return apiRequest<WorkspaceEnterpriseStatus>("/v1/enterprise/google-workspace", {
      method: "POST",
      body: { google_domain: googleDomain, service_account_key: serviceAccountKey },
    })
  },
  verify() {
    return apiRequest<WorkspaceEnterpriseStatus>("/v1/enterprise/google-workspace/verify", {
      method: "POST",
    })
  },
  disable() {
    return apiRequest<{ ok: boolean }>("/v1/enterprise/google-workspace", { method: "DELETE" })
  },
}
```

Add `WorkspaceEnterpriseStatus` to the `import type { ... } from "@/types"` block at the top of the file.

- [ ] **Step 3: Add UI state and handlers to the Connections page**

In `frontend/src/pages/WorkspacePages.tsx`:

Add to the import from `@/api/workspace`: `workspaceEnterpriseApi,`
Add to the import from `@/types`: `WorkspaceEnterpriseStatus,`

Add state near the other connector state (after the `gmailConnected` block):

```typescript
  const [entDetail, setEntDetail] = useState<WorkspaceEnterpriseStatus | null>(null)
  const [entBusy, setEntBusy] = useState(false)
  const [entDomain, setEntDomain] = useState("")
  const [entKey, setEntKey] = useState("")
```

Add to `load()`'s body (after the existing drive-panel branch), so status is fetched whenever the Connections page loads and the viewer can manage connectors:

```typescript
      if (canManage) {
        try {
          setEntDetail(await workspaceEnterpriseApi.get())
        } catch {
          setEntDetail(null)
        }
      }
```

Add handlers near `onDisconnectGmail`:

```typescript
  async function onSubmitWorkspaceEnterprise(e: FormEvent) {
    e.preventDefault()
    setEntBusy(true)
    setActionError(null)
    try {
      const detail = await workspaceEnterpriseApi.submit(entDomain, entKey)
      setEntDetail(detail)
      setActionMsg(
        detail.status === "verified"
          ? "Google Workspace verified."
          : "Submitted, but verification did not pass — see the error below.",
      )
    } catch (err) {
      setActionError(userFacingError(err))
    } finally {
      setEntBusy(false)
    }
  }

  async function onDisableWorkspaceEnterprise() {
    if (!window.confirm("Disable the Google Workspace enterprise connection?")) return
    setEntBusy(true)
    setActionError(null)
    try {
      await workspaceEnterpriseApi.disable()
      setEntDetail(await workspaceEnterpriseApi.get())
      setActionMsg("Google Workspace connection disabled.")
    } catch (err) {
      setActionError(userFacingError(err))
    } finally {
      setEntBusy(false)
    }
  }
```

- [ ] **Step 4: Render the card**

Add, after the closing `</div>` of the `connector-grid` section (right after the existing `{driveConnected && canManage && driveDetail ? (...) : null}` Drive panel block), a new section visible only to admins:

```tsx
      {canManage ? (
        <section className="drive-panel">
          <div className="page-head">
            <div>
              <span className="eyebrow">ENTERPRISE</span>
              <h2>Google Workspace (Domain-Wide Delegation)</h2>
              <p>
                For organization-wide access: create a service account in your own
                Google Cloud project, authorize it for Domain-Wide Delegation in your
                Admin Console (scope: admin.directory.user.readonly), then paste the
                key below.
              </p>
            </div>
          </div>

          {entDetail?.status ? (
            <div className="sync-progress">
              <strong>Status</strong>
              <span className={`status-pill status-${entDetail.status}`}>{entDetail.status}</span>
              {entDetail.google_domain ? <p>Domain: {entDetail.google_domain}</p> : null}
              {entDetail.service_account_email ? (
                <p>Service account: {entDetail.service_account_email}</p>
              ) : null}
              {entDetail.last_error ? <p className="muted">{entDetail.last_error}</p> : null}
            </div>
          ) : null}

          <form onSubmit={(e) => void onSubmitWorkspaceEnterprise(e)}>
            <label>
              Google Workspace domain
              <input
                type="text"
                value={entDomain}
                onChange={(e) => setEntDomain(e.target.value)}
                placeholder="acme.com"
                required
              />
            </label>
            <label>
              Service account JSON key
              <textarea
                value={entKey}
                onChange={(e) => setEntKey(e.target.value)}
                rows={6}
                placeholder="Paste the full JSON key here"
                required
              />
            </label>
            <button type="submit" className="primary-button" disabled={entBusy}>
              Submit &amp; verify
            </button>
          </form>

          {entDetail?.connected ? (
            <button
              type="button"
              className="text-button"
              disabled={entBusy}
              onClick={() => void onDisableWorkspaceEnterprise()}
            >
              Disable
            </button>
          ) : null}
        </section>
      ) : null}
```

- [ ] **Step 5: Verify it compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: no type errors.

- [ ] **Step 6: Manually verify in the browser**

With the stack running and `WORKSPACE_ENTERPRISE_MODE=mock` (the default), open the Connections page as an admin, paste any well-formed fixture JSON key (matching `REQUIRED_KEY_FIELDS`) with a domain, submit, and confirm the status pill shows `verified` and the service account email displayed matches what was pasted. Click Disable and confirm it returns to no status shown.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/workspace.ts frontend/src/pages/WorkspacePages.tsx frontend/src/types/index.ts
git commit -m "feat: add enterprise google workspace UI (submit, verify, disable)"
```

---

### Task 8: Documentation

**Files:**
- Modify: `README.md`
- Modify: `ARCHITECTURE_NOTES.md` (optional but recommended, given this file is the canonical "what's real vs stubbed" reference used throughout this project)

**Interfaces:** none (docs only).

- [ ] **Step 1: Add a README section**

In `README.md`, add a new section before `## Phase D — Google Drive` (chronologically this is a new, later phase, but grouping it near the other Google integrations keeps related docs together — place it directly after the Key APIs table instead, as a new `## Phase F — Enterprise Google Workspace (Domain-Wide Delegation)` section near the end, following the existing Phase D/E section style):

```markdown
## Phase F — Enterprise Google Workspace (Domain-Wide Delegation)

Foundation for enterprise-wide access: an admin authorizes a per-tenant
Google service account for Domain-Wide Delegation, letting Highwatch
impersonate any employee in the Workspace domain. This is the auth layer
only — it does not yet sync anyone's mail/Drive (a later phase will).

- **Coexists with, does not replace,** the per-admin OAuth Drive/Gmail
  connectors above — this is a separate, additive mechanism for
  enterprise customers.
- **Submit + verify:** `POST /v1/enterprise/google-workspace` (submits a
  service-account key and immediately verifies it by impersonating the
  submitting admin and listing 1 user via the Admin Directory API)
- **Re-verify:** `POST /v1/enterprise/google-workspace/verify`
- **Status:** `GET /v1/enterprise/google-workspace`
- **Disable:** `DELETE /v1/enterprise/google-workspace`
- **Local:** `WORKSPACE_ENTERPRISE_MODE=mock` (no real Google Workspace domain required)

```bash
docker compose up --build
API_URL=http://localhost:8000 ./scripts/smoke-phase-f.sh
```

### Real setup (manual, cannot be automated)

1. In your own Google Cloud project, create a service account.
2. In Google Admin Console → Security → API Controls →
   Domain-wide Delegation, authorize that service account's Client ID for
   scope `admin.directory.user.readonly`.
3. Download the service account's JSON key and paste it into the
   Connections page along with your Workspace domain.

Then set `WORKSPACE_ENTERPRISE_MODE=live`.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document phase F enterprise google workspace auth foundation"
```

---

## Self-Review Notes

- **Spec coverage:** Architecture (Task 1, 4, 5), Data model (Task 1), Setup & Verification Flow (Task 4 `submit_and_verify`/`_verify`, Task 7 UI), API Endpoints (Task 5), Error Handling (Task 3), Security & Storage (Task 1 encryption reuse, Task 5 admin-only guards, audit logging — see gap below), Testing Strategy (Task 3 unit tests, Task 6 smoke script) — all covered.
- **Gap found and flagged rather than silently added:** the spec's Security section calls for an `AuditEvent` write on every submit/verify/disable. This plan's Task 4/5 do not yet include it — add a follow-up task before shipping to production: write `AuditEvent(action="workspace_enterprise.submit"/"verify"/"disable", tenant_id=..., user_id=..., metadata_={...})` (see `AuthService.write_audit` in `services/auth.py` for the exact call shape) inside each `WorkspaceEnterpriseService` method. Kept out of this plan's tasks because it's a small, mechanical addition better done as a fast-follow once the core flow is confirmed working end-to-end, not because it's optional.
- **Type consistency:** `WorkspaceEnterpriseStatus` (Pydantic response model, Task 5) vs `WorkspaceEnterpriseStatus` (SQLAlchemy enum, Task 1) share a name but live in different modules (`routers.workspace_enterprise` vs `app.security`) — matches how this codebase already names things (e.g. `ConnectionStatus` the enum vs per-router `*Out` response models are always distinctly suffixed). Double-checked: the Pydantic class is `WorkspaceEnterpriseStatusOut`, not `WorkspaceEnterpriseStatus` — no collision.
- **No placeholders found** on re-scan of all task code blocks.
