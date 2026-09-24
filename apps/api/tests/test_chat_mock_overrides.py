import json

from app.services.chat import (
    get_mock_members,
    get_mock_messages,
    get_mock_spaces,
)


def test_get_mock_spaces_returns_fixture_spaces():
    spaces = get_mock_spaces()
    ids = {s["name"] for s in spaces}
    assert "spaces/mockspace-finance" in ids
    assert "spaces/mockspace-eng" in ids


def test_get_mock_members_returns_fixture_members_for_known_space():
    members = get_mock_members("spaces/mockspace-finance")
    assert "finance-member@example.com" in members


def test_get_mock_members_empty_for_unknown_space():
    assert get_mock_members("spaces/does-not-exist") == []


def test_get_mock_messages_excludes_removed_ids_from_overrides(tmp_path, monkeypatch):
    overrides_path = tmp_path / "overrides.json"
    overrides_path.write_text(json.dumps({"removed_message_ids": ["msg-budget-001"]}))
    monkeypatch.setattr("app.services.chat._MOCK_OVERRIDES_PATH", overrides_path)
    messages = get_mock_messages()
    assert all(m["name"] != "msg-budget-001" for m in messages)


def test_get_mock_messages_all_present_with_no_overrides_file(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.chat._MOCK_OVERRIDES_PATH", tmp_path / "missing.json")
    messages = get_mock_messages()
    assert len(messages) > 0
