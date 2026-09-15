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
