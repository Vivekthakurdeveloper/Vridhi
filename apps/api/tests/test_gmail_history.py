import json

from app.services import gmail as gmail_module
from app.services.gmail_history import (
    has_excluded_label,
    interpret_history,
    is_gone,
    message_id_from_external_id,
)


def _m(mid):
    return {"message": {"id": mid}}


def test_added_message_is_reported():
    ch = interpret_history([{"id": "1", "messagesAdded": [_m("a")]}])
    assert ch.added == {"a"} and ch.removed == set()


def test_deleted_message_is_reported():
    ch = interpret_history([{"id": "1", "messagesDeleted": [_m("a")]}])
    assert ch.removed == {"a"} and ch.added == set()


def test_delete_after_add_cancels_the_addition():
    ch = interpret_history(
        [
            {"id": "1", "messagesAdded": [_m("a")]},
            {"id": "2", "messagesDeleted": [_m("a")]},
        ]
    )
    assert ch.added == set() and ch.removed == {"a"}


def test_trash_label_counts_as_removed():
    ch = interpret_history([{"id": "1", "labelsAdded": [{**_m("a"), "labelIds": ["TRASH"]}]}])
    assert ch.removed == {"a"}


def test_removing_the_trash_label_counts_as_added_again():
    ch = interpret_history(
        [
            {"id": "1", "labelsAdded": [{**_m("a"), "labelIds": ["TRASH"]}]},
            {"id": "2", "labelsRemoved": [{**_m("a"), "labelIds": ["TRASH"]}]},
        ]
    )
    assert ch.added == {"a"} and ch.removed == set()


def test_unrelated_labels_are_ignored():
    ch = interpret_history([{"id": "1", "labelsAdded": [{**_m("a"), "labelIds": ["STARRED"]}]}])
    assert ch.added == set() and ch.removed == set()


def test_records_are_applied_in_id_order_even_if_given_out_of_order():
    ch = interpret_history(
        [
            {"id": "5", "messagesDeleted": [_m("a")]},
            {"id": "3", "messagesAdded": [_m("a")]},
        ]
    )
    assert ch.removed == {"a"} and ch.added == set()


def test_message_id_from_external_id():
    assert message_id_from_external_id("abc123:att-9") == "abc123"
    assert message_id_from_external_id("abc123") == "abc123"


def _point_overrides_at(tmp_path, monkeypatch, payload):
    path = tmp_path / "gmail-overrides.json"
    if payload is not None:
        path.write_text(json.dumps(payload))
    monkeypatch.setattr(gmail_module, "_MOCK_OVERRIDES_PATH", path)


def test_mock_messages_without_overrides_are_all_returned(tmp_path, monkeypatch):
    _point_overrides_at(tmp_path, monkeypatch, None)
    assert {m["id"] for m in gmail_module.get_mock_messages()} == {
        "msg-invoice-001",
        "msg-policy-002",
        "msg-photo-003",
    }


def test_mock_messages_omit_removed_ids(tmp_path, monkeypatch):
    _point_overrides_at(tmp_path, monkeypatch, {"removed_message_ids": ["msg-invoice-001"]})
    ids = {m["id"] for m in gmail_module.get_mock_messages()}
    assert "msg-invoice-001" not in ids and "msg-policy-002" in ids


def test_mock_history_defaults_to_no_changes(tmp_path, monkeypatch):
    _point_overrides_at(tmp_path, monkeypatch, None)
    records, current = gmail_module.get_mock_history("1000")
    assert records == [] and current == gmail_module.MOCK_HISTORY_ID


def test_mock_history_returns_only_records_after_the_checkpoint(tmp_path, monkeypatch):
    payload = {
        "history_id": "1002",
        "history": [
            {"id": "1001", "messagesDeleted": [_m("a")]},
            {"id": "1002", "messagesDeleted": [_m("b")]},
        ],
    }
    _point_overrides_at(tmp_path, monkeypatch, payload)
    records, current = gmail_module.get_mock_history("1001")
    assert [r["id"] for r in records] == ["1002"] and current == "1002"


def test_has_excluded_label():
    assert has_excluded_label(None) is False
    assert has_excluded_label({"id": "a"}) is False
    assert has_excluded_label({"id": "a", "labelIds": []}) is False
    assert has_excluded_label({"id": "a", "labelIds": ["INBOX"]}) is False
    assert has_excluded_label({"id": "a", "labelIds": ["INBOX", "SPAM"]}) is True
    assert has_excluded_label({"id": "a", "labelIds": ["TRASH"]}) is True
    assert has_excluded_label({"id": "a", "labelIds": ["DRAFT"]}) is False


def test_is_gone():
    assert is_gone(None) is True
    assert is_gone({"id": "a", "labelIds": ["TRASH"]}) is True
    assert is_gone({"id": "a", "labelIds": ["SPAM"]}) is True
    assert is_gone({"id": "a", "labelIds": ["INBOX"]}) is False
    assert is_gone({"id": "a"}) is False


def test_spam_label_counts_as_removed():
    ch = interpret_history([{"id": "1", "labelsAdded": [{**_m("a"), "labelIds": ["SPAM"]}]}])
    assert ch.removed == {"a"} and ch.added == set()


def test_removing_the_spam_label_counts_as_added_again():
    ch = interpret_history(
        [
            {"id": "1", "labelsAdded": [{**_m("a"), "labelIds": ["SPAM"]}]},
            {"id": "2", "labelsRemoved": [{**_m("a"), "labelIds": ["SPAM"]}]},
        ]
    )
    assert ch.added == {"a"} and ch.removed == set()
