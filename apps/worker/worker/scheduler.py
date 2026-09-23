"""Automatic sync scheduler (Phase H).

``run_scheduler_tick`` is called by the worker loop about once a minute. For each
connected Drive/Gmail connection that is due (see ``app.services.autosync``) it
starts a sync by calling the same ``start_sync`` the Sync Now button uses, so
every existing safeguard applies.

Two workers must never queue the same connection twice: each candidate is
re-read with ``SELECT ... FOR UPDATE SKIP LOCKED`` in its own transaction, the
due-check runs on that fresh row, and ``start_sync`` commits the new job (which
the other worker then sees as an active job).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings
from app.errors import AppError
from app.models import Connection, SyncJob
from app.security import ConnectionStatus, DocumentVisibility, SyncJobStatus, SyncJobType, utcnow
from app.services import autosync
from app.services.chat import ChatService
from app.services.drive import DriveService
from app.services.gmail import GmailService
from app.services.queue import get_ingest_queue
from app.services.storage import get_object_storage
from app.services.tokens import get_token_store
from worker.db import SessionLocal

logger = logging.getLogger(__name__)

_SYNC_JOB_TYPES = (SyncJobType.drive_sync, SyncJobType.gmail_sync, SyncJobType.chat_sync)


def run_scheduler_tick(settings: Settings, *, now: Optional[datetime] = None) -> int:
    if not settings.auto_sync_enabled_global:
        return 0
    now = now or utcnow()
    with SessionLocal() as db:
        latest_job = (
            select(SyncJob.connection_id, func.max(SyncJob.created_at).label("latest"))
            .where(SyncJob.job_type.in_(_SYNC_JOB_TYPES))
            .group_by(SyncJob.connection_id)
            .subquery()
        )
        rows = db.execute(
            select(Connection.id, latest_job.c.latest)
            .outerjoin(latest_job, latest_job.c.connection_id == Connection.id)
            .where(
                Connection.connector_type.in_(autosync.SCHEDULABLE_CONNECTORS),
                # syncing is included so a connection stranded by a dead worker
                # is reconsidered; _start_if_due decides via effective_status.
                Connection.status.in_(
                    tuple(autosync.SCHEDULABLE_STATUSES) + (ConnectionStatus.syncing,)
                ),
            )
        ).all()
    # Never-synced / longest-waiting first, and at most N started per tick: a
    # first deploy or a big backlog spreads over several ticks instead of
    # queuing everything at once. Connections not reached stay due next tick.
    candidate_ids = autosync.oldest_sync_first((r[0], r[1]) for r in rows)
    max_starts = max(1, settings.auto_sync_max_starts_per_tick)  # 0 must not stall all syncs
    started = 0
    for connection_id in candidate_ids:
        if started >= max_starts:
            break
        try:
            with SessionLocal() as db:
                if _start_if_due(db, settings, connection_id, now):
                    started += 1
        except Exception:
            logger.exception("scheduler.connection_failed", extra={"operation": "scheduler"})
    return started


def _start_if_due(db: Session, settings: Settings, connection_id: UUID, now: datetime) -> bool:
    conn = db.scalar(
        select(Connection)
        .where(Connection.id == connection_id)
        .with_for_update(skip_locked=True)
        .options(selectinload(Connection.credentials))
    )
    if conn is None:  # another worker holds it
        db.rollback()
        return False

    latest = db.scalar(
        select(func.max(SyncJob.created_at)).where(
            SyncJob.connection_id == conn.id, SyncJob.job_type.in_(_SYNC_JOB_TYPES)
        )
    )
    active = (
        db.scalar(
            select(func.count())
            .select_from(SyncJob)
            .where(
                SyncJob.connection_id == conn.id,
                SyncJob.job_type.in_(_SYNC_JOB_TYPES),
                SyncJob.status.in_([SyncJobStatus.queued, SyncJobStatus.running]),
                # updated_at is bumped by per-file progress commits, so it is a
                # heartbeat: an older queued/running job is stale, not active.
                SyncJob.updated_at
                >= now - timedelta(seconds=settings.auto_sync_stale_after_seconds),
            )
        )
        or 0
    ) > 0

    if not autosync.is_due(
        connector_type=conn.connector_type,
        status=autosync.effective_status(conn.status, active),
        connected_by_user_id=conn.connected_by_user_id,
        config=conn.config,
        latest_job_created_at=latest,
        active_job_exists=active,
        now=now,
        interval_seconds=settings.auto_sync_interval_seconds,
    ):
        db.rollback()
        return False

    if conn.status == ConnectionStatus.syncing:  # syncing but no fresh job: stale
        logger.info(
            "scheduler.stale_sync_ignored",
            extra={"operation": "scheduler", "tenant_id": str(conn.tenant_id)},
        )

    cfg = conn.config or {}
    tokens, storage, queue = get_token_store(), get_object_storage(), get_ingest_queue()
    try:
        if conn.connector_type == "google_drive":
            DriveService(db, settings, tokens, storage, queue).start_sync(
                tenant_id=conn.tenant_id,
                user_id=conn.connected_by_user_id,
                visibility=DocumentVisibility(cfg.get("default_visibility") or "org"),
                selected_user_ids=[UUID(u) for u in cfg.get("selected_user_ids") or []] or None,
                incremental=True,
                trigger="schedule",
            )
        elif conn.connector_type == "google_chat":
            ChatService(db, settings, tokens, storage, queue).start_sync(
                tenant_id=conn.tenant_id,
                user_id=conn.connected_by_user_id,
                incremental=True,
                trigger="schedule",
            )
        else:
            GmailService(db, settings, tokens, storage, queue).start_sync(
                tenant_id=conn.tenant_id,
                user_id=conn.connected_by_user_id,
                incremental=True,
                trigger="schedule",
            )
    except AppError as exc:
        db.rollback()
        logger.warning(
            "scheduler.start_failed", extra={"operation": "scheduler", "request_id": exc.code}
        )
        return False
    logger.info(
        "scheduler.sync_started",
        extra={"operation": "scheduler", "tenant_id": str(conn.tenant_id)},
    )
    return True
