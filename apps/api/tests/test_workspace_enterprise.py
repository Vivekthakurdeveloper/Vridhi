import json
import uuid

import pytest

from app.errors import AppError
from app.models import WorkspaceEnterpriseConnection
from app.security import WorkspaceEnterpriseStatus
from app.services.workspace_enterprise import (
    WorkspaceEnterpriseService,
    translate_workspace_enterprise_error,
    validate_service_account_key,
)


class _FakeDB:
    """Minimal stand-in for the SQLAlchemy Session calls WorkspaceEnterpriseService
    makes, so these tests can exercise the service without a real database."""

    def __init__(self, conn):
        self._conn = conn

    def scalar(self, _stmt):
        return self._conn

    def add(self, _obj):
        pass

    def commit(self):
        pass

    def refresh(self, _obj):
        pass


class _TokenStoreThatMustNotBeCalled:
    """Fails the test if verify() ever tries to decrypt — used to prove the
    disabled/empty-key guard short-circuits before reaching decrypt()."""

    def decrypt(self, _ciphertext):
        raise AssertionError("decrypt() should not be called when the guard rejects the connection")

    def encrypt(self, plaintext):
        return plaintext


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


def test_translate_unknown_error_falls_back_to_generic_message():
    msg = translate_workspace_enterprise_error("some completely novel google error")
    assert msg == "Verification failed. Check the service account configuration and try again."
    assert "some completely novel google error" not in msg


def test_verify_rejects_error_status_connection_with_stale_empty_key():
    """Regression test for the crash bug: disable() clears encrypted_key to
    "" but leaves status alone; a subsequent failed re-submit (submit_and_verify
    on an existing connection) sets status=error without restoring the key,
    so a disabled-then-failed connection can end up with status=error and
    encrypted_key="". verify()'s guard must reject this by checking the key
    is present, not just by checking status == disabled — otherwise it falls
    through to json.loads(self.tokens.decrypt("")), which raises and 500s.
    """
    conn = WorkspaceEnterpriseConnection(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        google_domain="acme.com",
        service_account_email="svc@acme-proj.iam.gserviceaccount.com",
        encrypted_key="",
        status=WorkspaceEnterpriseStatus.error,
    )
    service = WorkspaceEnterpriseService(
        db=_FakeDB(conn),
        tokens=_TokenStoreThatMustNotBeCalled(),
        is_mock=True,
        scopes=["https://www.googleapis.com/auth/admin.directory.user.readonly"],
    )

    with pytest.raises(AppError) as exc:
        service.verify(tenant_id=conn.tenant_id, admin_email="admin@acme.com")

    assert exc.value.code == "WORKSPACE_ENTERPRISE_NOT_CONFIGURED"


def test_verify_still_rejects_disabled_connection_with_key_present():
    """Belt-and-braces: the original disabled-status guard must keep working
    even though the check is now OR'd with the empty-key check."""
    conn = WorkspaceEnterpriseConnection(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        google_domain="acme.com",
        service_account_email="svc@acme-proj.iam.gserviceaccount.com",
        encrypted_key="still-here-but-should-not-matter",
        status=WorkspaceEnterpriseStatus.disabled,
    )
    service = WorkspaceEnterpriseService(
        db=_FakeDB(conn),
        tokens=_TokenStoreThatMustNotBeCalled(),
        is_mock=True,
        scopes=["https://www.googleapis.com/auth/admin.directory.user.readonly"],
    )

    with pytest.raises(AppError) as exc:
        service.verify(tenant_id=conn.tenant_id, admin_email="admin@acme.com")

    assert exc.value.code == "WORKSPACE_ENTERPRISE_NOT_CONFIGURED"


def test_verify_failure_persists_translated_message_not_raw_detail(monkeypatch):
    """Regression test: _verify() must store the translated, safe message
    (exc.message) into conn.last_error — which is returned by the
    member-readable GET /v1/enterprise/google-workspace — never the raw
    Google error text (exc.raw_detail), which can contain internal
    project/config identifiers.
    """
    raw_detail = "internal google error mentioning project acme-proj-987654 and other detail"
    friendly_message = "Verification failed. Check the service account configuration and try again."

    def fake_verify_directory_access(*_args, **_kwargs):
        err = AppError("WORKSPACE_ENTERPRISE_VERIFICATION_FAILED", friendly_message, 400)
        err.raw_detail = raw_detail
        raise err

    monkeypatch.setattr(
        "app.services.workspace_enterprise.verify_directory_access",
        fake_verify_directory_access,
    )

    conn = WorkspaceEnterpriseConnection(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        google_domain="acme.com",
        service_account_email="svc@acme-proj.iam.gserviceaccount.com",
        encrypted_key="whatever",
        status=WorkspaceEnterpriseStatus.pending_verification,
    )
    service = WorkspaceEnterpriseService(
        db=_FakeDB(conn),
        tokens=_TokenStoreThatMustNotBeCalled(),
        is_mock=False,
        scopes=["https://www.googleapis.com/auth/admin.directory.user.readonly"],
    )

    with pytest.raises(AppError):
        service._verify(conn, {"client_id": "123"}, "admin@acme.com")

    assert conn.last_error == friendly_message
    assert raw_detail not in (conn.last_error or "")
    assert conn.status == WorkspaceEnterpriseStatus.error
