import json

import pytest

from app.services import drive as drive_module


def _point_overrides_at(tmp_path, monkeypatch, payload):
    path = tmp_path / "overrides.json"
    if payload is not None:
        path.write_text(json.dumps(payload))
    monkeypatch.setattr(drive_module, "_MOCK_OVERRIDES_PATH", path)


def test_without_an_override_file_everything_is_listed(tmp_path, monkeypatch):
    _point_overrides_at(tmp_path, monkeypatch, None)
    ids = {f["id"] for f in drive_module.get_mock_files("folder-finance")}
    assert {"file-gst-sop", "file-finance-group-shared"} <= ids


def test_removed_files_are_omitted(tmp_path, monkeypatch):
    _point_overrides_at(tmp_path, monkeypatch, {"removed": ["file-gst-sop"]})
    ids = {f["id"] for f in drive_module.get_mock_files("folder-finance")}
    assert "file-gst-sop" not in ids
    assert "file-finance-group-shared" in ids


def test_fail_listing_raises(tmp_path, monkeypatch):
    _point_overrides_at(tmp_path, monkeypatch, {"fail_listing": True})
    with pytest.raises(RuntimeError):
        drive_module.get_mock_files("folder-finance")


def test_permission_overrides_still_apply(tmp_path, monkeypatch):
    perms = [{"type": "user", "emailAddress": "owner@example.com", "role": "owner"}]
    _point_overrides_at(
        tmp_path, monkeypatch, {"permissions": {"file-finance-group-shared": perms}}
    )
    files = {f["id"]: f for f in drive_module.get_mock_files("folder-finance")}
    assert files["file-finance-group-shared"]["permissions"] == perms
