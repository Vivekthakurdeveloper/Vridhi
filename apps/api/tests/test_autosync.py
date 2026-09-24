from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.models import AuditEvent
from app.security import ConnectionStatus, utcnow
from app.services import autosync

NOW = utcnow()


def _kwargs(**over):
    base = dict(
        connector_type="google_drive",
        status=ConnectionStatus.connected,
        connected_by_user_id=uuid4(),
        config={"selected_folder_ids": ["folder-a"]},
        latest_job_created_at=NOW - timedelta(seconds=901),
        active_job_exists=False,
        now=NOW,
        interval_seconds=900,
    )
    base.update(over)
    return base


def test_due_when_last_job_is_older_than_interval():
    assert autosync.is_due(**_kwargs()) is True


def test_due_exactly_at_interval_boundary():
    assert autosync.is_due(**_kwargs(latest_job_created_at=NOW - timedelta(seconds=900))) is True


def test_not_due_one_second_before_interval():
    assert autosync.is_due(**_kwargs(latest_job_created_at=NOW - timedelta(seconds=899))) is False


def test_due_when_never_synced():
    assert autosync.is_due(**_kwargs(latest_job_created_at=None)) is True


def test_not_due_when_switched_off():
    cfg = {"selected_folder_ids": ["f"], "auto_sync_enabled": False}
    assert autosync.is_due(**_kwargs(config=cfg)) is False


def test_missing_flag_means_on():
    assert autosync.is_enabled({}) is True
    assert autosync.is_enabled(None) is True
    assert autosync.is_enabled({"auto_sync_enabled": False}) is False


def test_not_due_when_paused():
    cfg = {"selected_folder_ids": ["f"], "auto_sync_paused_reason": "paused"}
    assert autosync.is_due(**_kwargs(config=cfg)) is False


def test_not_due_when_a_sync_is_already_active():
    assert autosync.is_due(**_kwargs(active_job_exists=True)) is False


def test_drive_without_folders_is_not_due():
    assert autosync.is_due(**_kwargs(config={})) is False


def test_gmail_does_not_need_folders():
    assert autosync.is_due(**_kwargs(connector_type="gmail", config={})) is True


def test_google_chat_does_not_need_folders():
    assert autosync.is_due(**_kwargs(connector_type="google_chat", config={})) is True


def test_disconnected_syncing_and_unconfigured_are_not_due():
    for status in (ConnectionStatus.disconnected, ConnectionStatus.syncing, ConnectionStatus.not_configured):
        assert autosync.is_due(**_kwargs(status=status)) is False


def test_sync_failed_connection_is_retried():
    assert autosync.is_due(**_kwargs(status=ConnectionStatus.sync_failed)) is True


def test_not_due_without_a_connecting_user():
    assert autosync.is_due(**_kwargs(connected_by_user_id=None)) is False


def test_unknown_connector_is_never_due():
    assert autosync.is_due(**_kwargs(connector_type="file_upload")) is False


def test_failures_accumulate_then_pause_at_threshold():
    cfg = {}
    for _ in range(4):
        cfg = autosync.record_sync_outcome(cfg, succeeded=False, max_failures=5)
    assert cfg["auto_sync_failures"] == 4
    assert autosync.paused_reason(cfg) is None
    cfg = autosync.record_sync_outcome(cfg, succeeded=False, max_failures=5)
    assert cfg["auto_sync_failures"] == 5
    assert "paused" in autosync.paused_reason(cfg).lower()


def test_success_resets_counter_and_clears_pause():
    cfg = {"auto_sync_failures": 5, "auto_sync_paused_reason": "x", "keep": 1}
    out = autosync.record_sync_outcome(cfg, succeeded=True, max_failures=5)
    assert out["auto_sync_failures"] == 0
    assert "auto_sync_paused_reason" not in out
    assert out["keep"] == 1


def test_record_outcome_does_not_mutate_input():
    cfg = {"auto_sync_failures": 1}
    autosync.record_sync_outcome(cfg, succeeded=False, max_failures=5)
    assert cfg == {"auto_sync_failures": 1}


def test_set_enabled_clears_pause_and_counter():
    cfg = {"auto_sync_failures": 5, "auto_sync_paused_reason": "x", "keep": 1}
    out = autosync.set_enabled(cfg, True)
    assert out["auto_sync_enabled"] is True
    assert out["auto_sync_failures"] == 0
    assert "auto_sync_paused_reason" not in out
    assert out["keep"] == 1
    assert autosync.set_enabled(cfg, False)["auto_sync_enabled"] is False


class _FakeDB:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1


def test_set_auto_sync_updates_config_writes_audit_and_commits():
    db = _FakeDB()
    conn = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        connector_type="gmail",
        config={"auto_sync_failures": 3, "auto_sync_paused_reason": "x"},
    )
    actor = uuid4()
    autosync.set_auto_sync(db, conn, enabled=False, actor_user_id=actor, request_id="req-1")
    assert conn.config["auto_sync_enabled"] is False
    assert "auto_sync_paused_reason" not in conn.config
    assert db.commits == 1
    (event,) = db.added
    assert isinstance(event, AuditEvent)
    assert event.action == "connection.auto_sync_changed"
    assert event.user_id == actor
    assert event.metadata_ == {"connector": "gmail", "enabled": False}


def test_effective_status_syncing_with_fresh_active_job_stays_syncing():
    assert (
        autosync.effective_status(ConnectionStatus.syncing, True) == ConnectionStatus.syncing
    )


def test_effective_status_syncing_without_fresh_active_job_is_connected():
    assert (
        autosync.effective_status(ConnectionStatus.syncing, False) == ConnectionStatus.connected
    )


def test_effective_status_connected_stays_connected():
    assert (
        autosync.effective_status(ConnectionStatus.connected, False) == ConnectionStatus.connected
    )
    assert (
        autosync.effective_status(ConnectionStatus.connected, True) == ConnectionStatus.connected
    )


def test_effective_status_sync_failed_stays_sync_failed():
    assert (
        autosync.effective_status(ConnectionStatus.sync_failed, False)
        == ConnectionStatus.sync_failed
    )


def test_effective_status_disconnected_stays_disconnected():
    assert (
        autosync.effective_status(ConnectionStatus.disconnected, False)
        == ConnectionStatus.disconnected
    )


def test_oldest_sync_first_puts_never_synced_then_longest_waiting_first():
    old, older, recent = "old", "older", "recent"
    ordered = autosync.oldest_sync_first(
        [
            (recent, NOW - timedelta(seconds=60)),
            ("never", None),
            (older, NOW - timedelta(hours=3)),
            (old, NOW - timedelta(hours=1)),
        ]
    )
    assert ordered == ["never", older, old, recent]


def test_oldest_sync_first_keeps_input_order_for_ties_and_handles_empty():
    assert autosync.oldest_sync_first([]) == []
    assert autosync.oldest_sync_first([("a", None), ("b", None), ("c", None)]) == ["a", "b", "c"]
    assert autosync.oldest_sync_first([("a", NOW), ("b", NOW)]) == ["a", "b"]


def test_max_starts_per_tick_setting_defaults_to_five():
    from app.config import Settings

    assert Settings.model_fields["auto_sync_max_starts_per_tick"].default == 5
