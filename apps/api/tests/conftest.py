"""Shared pytest fixtures for router-level (FastAPI TestClient) tests.

There is no pre-existing conftest.py in this repo and no prior router-level
integration test to mirror (grepped for `TestClient` usage across
apps/api/tests -- only test_health.py uses it, and only for /healthz and a
validation-error shape, both unauthenticated). These fixtures build real,
authenticated TestClient instances the same way a real client would: register
a fresh user + organization via POST /v1/auth/register, which creates that
user as the org's `owner` (see app/services/auth.py's `register()`), and rely
on the session cookie TestClient persists automatically across requests on
the same client instance.

`owner` outranks `admin` (see app/security.py's role ranking), so a single
registration flow satisfies both `require_tenant` (member-or-above) and
`require_role(MemberRole.admin)` endpoints. `authed_client` and
`authed_admin_client` are kept as two distinct fixtures/organizations (mostly
so tests can be run independently without cross-test state bleed), but both
are, today, owner-authenticated clients.

Teardown: `/v1/auth/register` commits its own session/transaction inside the
app (app/db.py's `get_db` opens/closes one `SessionLocal()` per request), so
there is no single outer transaction these fixtures could wrap and roll back
across the whole test -- each request the TestClient makes commits for real
against the same Postgres the app runs on. To avoid leaving user/org rows
behind after every test run, `_register_client` returns the created user id
and tenant id (read off `AuthSessionOut` in the response body) alongside the
client, and each fixture deletes those rows in its own teardown, after the
test body has run, using a fresh `SessionLocal()`. Every FK from
tenant-scoped tables (organization_members, connections, documents, etc.) to
`organizations.id`, and from user-scoped tables (sessions, oauth_accounts,
etc.) to `users.id`, is declared `ondelete="CASCADE"` (see
app/models/__init__.py), so deleting the Organization and User rows cascades
away everything a test created under that tenant/user, not just the two rows
themselves.
"""
from __future__ import annotations

import uuid
from typing import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.config import get_settings
from app.db import SessionLocal
from app.main import create_app
from app.models import Organization, User


def _register_client(
    email_prefix: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, UUID, UUID | None]:
    # Google Chat is disabled by default (GOOGLE_CHAT_ENABLED=false) and not
    # set in the repo's .env, so router tests that exercise it need it turned
    # on in mock mode. Setting the env vars before get_settings.cache_clear()
    # + create_app() ensures the freshly-built Settings picks them up.
    monkeypatch.setenv("GOOGLE_CHAT_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_CHAT_MODE", "mock")
    get_settings.cache_clear()
    client = TestClient(create_app())
    unique = uuid.uuid4().hex[:12]
    resp = client.post(
        "/v1/auth/register",
        json={
            "name": "Test Owner",
            "email": f"{email_prefix}-{unique}@example.com",
            "password": "Sup3rSecret!",
            "organization_name": f"Test Org {unique}",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    user_id = UUID(body["user"]["id"])
    tenant_id = UUID(body["membership"]["tenant_id"]) if body.get("membership") else None
    return client, user_id, tenant_id


def _cleanup(user_id: UUID, tenant_id: UUID | None) -> None:
    """Delete the org/user rows a fixture created, so repeated test runs
    don't accumulate permanent rows in whatever Postgres the suite runs
    against (a shared dev DB, or a CI DB reused across many runs).

    Uses Core `delete()` statements rather than `Session.delete(obj)`: the
    ORM's own unit-of-work cascade handling for relationships (e.g.
    Organization.members / User.memberships) defaults to nulling out the
    child's FK columns in Python before issuing the DELETE, instead of
    letting Postgres's `ondelete="CASCADE"` (declared on every FK to
    organizations.id / users.id in app/models/__init__.py) do it -- and
    those FK columns are NOT NULL, so that ORM-level nulling raises an
    IntegrityError. A Core DELETE skips ORM cascade handling entirely and
    lets the database-level ON DELETE CASCADE remove the dependent rows.
    """
    db = SessionLocal()
    try:
        if tenant_id is not None:
            db.execute(delete(Organization).where(Organization.id == tenant_id))
        db.execute(delete(User).where(User.id == user_id))
        db.commit()
    finally:
        db.close()


@pytest.fixture
def authed_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A TestClient authenticated as a freshly-registered org owner."""
    client, user_id, tenant_id = _register_client("member", monkeypatch)
    try:
        yield client
    finally:
        get_settings.cache_clear()
        _cleanup(user_id, tenant_id)


@pytest.fixture
def authed_admin_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A TestClient authenticated as a freshly-registered org owner (owner
    outranks admin, so this satisfies require_role(MemberRole.admin))."""
    client, user_id, tenant_id = _register_client("admin", monkeypatch)
    try:
        yield client
    finally:
        get_settings.cache_clear()
        _cleanup(user_id, tenant_id)
