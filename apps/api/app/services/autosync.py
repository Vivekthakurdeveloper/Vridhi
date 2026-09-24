"""Automatic-sync decision logic (Phase H).

Pure functions plus one small DB helper. The scheduler in
``worker/scheduler.py`` and the API's auto-sync toggle both build on these, and
they live under ``app.services`` (not ``worker``) so they can be unit-tested
from the api container.

State lives in ``connections.config`` (JSONB) - no migration:

* ``auto_sync_enabled``        - False switches it off; absent means on
* ``auto_sync_failures``       - consecutive failed syncs
* ``auto_sync_paused_reason``  - set when failures reach the threshold
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable, Optional
from uuid import UUID, uuid4

from app.models import AuditEvent
from app.security import ConnectionStatus

AUTO_SYNC_ENABLED_KEY = "auto_sync_enabled"
AUTO_SYNC_FAILURES_KEY = "auto_sync_failures"
AUTO_SYNC_PAUSED_KEY = "auto_sync_paused_reason"

SCHEDULABLE_CONNECTORS = ("google_drive", "gmail", "google_chat")
SCHEDULABLE_STATUSES = (ConnectionStatus.connected, ConnectionStatus.sync_failed)


def effective_status(status: ConnectionStatus, has_fresh_active_job: bool) -> ConnectionStatus:
    """A ``syncing`` connection with no fresh active job is stale (its worker
    died mid-run), so treat it as ``connected`` and let auto-sync resume."""
    if status == ConnectionStatus.syncing and not has_fresh_active_job:
        return ConnectionStatus.connected
    return status


def oldest_sync_first(candidates: Iterable[tuple[Any, Optional[datetime]]]) -> list[Any]:
    """Order ``(key, latest_sync_job_created_at)`` pairs so never-synced
    connections come first, then the longest-waiting. The scheduler starts at
    most N per tick, so this decides who goes first; ties keep input order."""
    ordered = sorted(candidates, key=lambda c: (c[1] is not None, c[1]))
    return [key for key, _ in ordered]


def is_enabled(config: Optional[dict[str, Any]]) -> bool:
    return (config or {}).get(AUTO_SYNC_ENABLED_KEY) is not False


def paused_reason(config: Optional[dict[str, Any]]) -> Optional[str]:
    return (config or {}).get(AUTO_SYNC_PAUSED_KEY) or None


def is_due(
    *,
    connector_type: str,
    status: ConnectionStatus,
    connected_by_user_id: Optional[UUID],
    config: Optional[dict[str, Any]],
    latest_job_created_at: Optional[datetime],
    active_job_exists: bool,
    now: datetime,
    interval_seconds: int,
) -> bool:
    if connector_type not in SCHEDULABLE_CONNECTORS:
        return False
    if status not in SCHEDULABLE_STATUSES:
        return False
    if connected_by_user_id is None:
        return False
    if not is_enabled(config) or paused_reason(config):
        return False
    if connector_type == "google_drive" and not (config or {}).get("selected_folder_ids"):
        return False
    if active_job_exists:
        return False
    if latest_job_created_at is None:
        return True
    return now - latest_job_created_at >= timedelta(seconds=interval_seconds)


def record_sync_outcome(
    config: Optional[dict[str, Any]], *, succeeded: bool, max_failures: int
) -> dict[str, Any]:
    cfg = dict(config or {})
    if succeeded:
        cfg[AUTO_SYNC_FAILURES_KEY] = 0
        cfg.pop(AUTO_SYNC_PAUSED_KEY, None)
        return cfg
    failures = int(cfg.get(AUTO_SYNC_FAILURES_KEY) or 0) + 1
    cfg[AUTO_SYNC_FAILURES_KEY] = failures
    if failures >= max_failures and not cfg.get(AUTO_SYNC_PAUSED_KEY):
        cfg[AUTO_SYNC_PAUSED_KEY] = (
            f"Auto-sync paused after {failures} failed syncs in a row. "
            "Reconnect or run Sync Now to resume."
        )
    return cfg


def set_enabled(config: Optional[dict[str, Any]], enabled: bool) -> dict[str, Any]:
    cfg = dict(config or {})
    cfg[AUTO_SYNC_ENABLED_KEY] = bool(enabled)
    cfg[AUTO_SYNC_FAILURES_KEY] = 0
    cfg.pop(AUTO_SYNC_PAUSED_KEY, None)
    return cfg


def set_auto_sync(
    db: Any,
    conn: Any,
    *,
    enabled: bool,
    actor_user_id: UUID,
    request_id: Optional[str] = None,
) -> None:
    conn.config = set_enabled(conn.config, enabled)
    db.add(
        AuditEvent(
            id=uuid4(),
            tenant_id=conn.tenant_id,
            user_id=actor_user_id,
            action="connection.auto_sync_changed",
            metadata_={"connector": conn.connector_type, "enabled": bool(enabled)},
            request_id=request_id,
        )
    )
    db.commit()
