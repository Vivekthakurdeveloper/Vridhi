"""Gmail sync worker (Phase E).

Mirrors `worker/drive_sync.py`: a parent `gmail_sync` job walks the mailbox,
downloads each allowed attachment, upserts a Document + DocumentVersion, and
enqueues a child `ingest` job. The ingest pipeline is source-agnostic and needs
no Gmail-specific changes.

Two deliberate differences from Drive:

* Every document is `DocumentVisibility.private` with no DocumentGrant rows —
  a mailbox is personal, so there is no permission graph to resolve.
* The incremental cursor actually honours `payload["incremental"]`. Drive's
  equivalent consults `file_cursors` unconditionally, which makes
  `incremental: false` a no-op there; that bug is not reproduced here.

Change tracking: `connection.config["gmail_history_id"]` is a Gmail History
checkpoint. The first run does a full pass and stores it; later incremental
runs read only what changed via `users.history.list` and hide attachments of
deleted/trashed messages.

Known limitation: `message_cursors` still grows unbounded in
`connections.config` JSONB, one key per synced attachment.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Any, Iterator, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings, get_settings
from app.models import Connection, Document, DocumentVersion, SyncJob
from app.security import (
    ConnectionHealth,
    ConnectionStatus,
    DocumentStatus,
    DocumentVisibility,
    SyncJobStatus,
    SyncJobType,
    utcnow,
)
from app.services.gmail import GmailService, get_mock_history, get_mock_messages
from app.services.gmail_history import (
    HistoryChanges,
    interpret_history,
    message_id_from_external_id,
)
from app.services.queue import IngestQueue, get_ingest_queue
from app.services.storage import ObjectStorage, get_object_storage
from app.services.tokens import TokenStore, get_token_store
from app.services.tombstone import (
    REASON_MANUAL,
    REASON_SOURCE_REMOVED,
    tombstone_many,
)
from worker.google_api import google_get_with_retry

logger = logging.getLogger(__name__)

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def process_gmail_sync_job(db: Session, *, job_id: UUID, search: Any) -> None:
    """Worker entrypoint: uses API app services (storage/queue/tokens/settings)."""
    settings = get_settings()
    storage = get_object_storage()
    queue = get_ingest_queue()
    tokens = get_token_store()
    _run_gmail_sync(
        db,
        settings=settings,
        storage=storage,
        queue=queue,
        tokens=tokens,
        search=search,
        job_id=job_id,
    )


def _run_gmail_sync(
    db: Session,
    *,
    settings: Settings,
    storage: ObjectStorage,
    queue: IngestQueue,
    tokens: TokenStore,
    search: Any,
    job_id: UUID,
) -> None:
    job = db.get(SyncJob, job_id)
    if not job:
        # Should be unreachable now that start_sync commits before enqueuing,
        # but log rather than returning silently — an invisible early return
        # here is what leaves a job stranded in `queued` with no diagnostic.
        logger.warning(
            "gmail.job_missing",
            extra={"operation": "gmail_sync", "request_id": str(job_id)},
        )
        return
    if job.job_type != SyncJobType.gmail_sync:
        logger.warning(
            "gmail.job_type_mismatch",
            extra={"operation": "gmail_sync", "request_id": str(job_id)},
        )
        return
    if job.status == SyncJobStatus.succeeded:
        return

    job.attempt += 1
    job.status = SyncJobStatus.running
    job.started_at = utcnow()
    job.error_message = None
    db.commit()

    gmail = GmailService(db, settings, tokens, storage, queue)
    try:
        if not job.connection_id:
            raise RuntimeError("Gmail sync job missing connection_id")
        conn = db.scalar(
            select(Connection)
            .where(Connection.id == job.connection_id)
            .options(selectinload(Connection.credentials))
        )
        if not conn:
            raise RuntimeError("Gmail connection not found")

        payload = dict(job.payload or {})
        query = str(payload.get("query") or settings.gmail_query)
        incremental = bool(payload.get("incremental", True))
        requested_by = (
            UUID(payload["requested_by"])
            if payload.get("requested_by")
            else conn.connected_by_user_id
        )
        if not requested_by:
            raise RuntimeError("Missing requested_by user")

        checkpoint = str((conn.config or {}).get("gmail_history_id") or "")
        changes: Optional[HistoryChanges] = None
        new_checkpoint = ""
        if incremental and checkpoint:
            try:
                records, new_checkpoint = _fetch_history(gmail, conn, checkpoint)
                changes = interpret_history(records)
            except HistoryExpired:
                logger.info("gmail.history_expired", extra={"operation": "gmail_sync"})
                changes = None
        listed_message_ids: Optional[set[str]] = None
        if changes is None:
            # Full pass. Take the checkpoint BEFORE listing so any mail that
            # arrives while we list is replayed by the next incremental run.
            new_checkpoint = _current_history_id(gmail, conn)
            messages = _list_messages(gmail, settings, conn, query)
            listed_message_ids = {str(m["id"]) for m in messages}
        else:
            messages = [
                m
                for m in (_fetch_message(gmail, conn, mid) for mid in sorted(changes.added))
                if m is not None
            ]

        # progress_total counts attachments, not messages — it is what the UI
        # renders as "n of m", and a message may carry several attachments.
        attachments: list[tuple[dict[str, Any], dict[str, Any], str]] = []
        for message in messages:
            for part, part_path in _walk_attachment_parts(message.get("payload") or {}):
                attachments.append((message, part, part_path))

        job.progress_total = len(attachments)
        job.progress_done = 0
        job.progress_failed = 0
        job.progress_skipped = 0
        db.commit()

        for message, part, part_path in attachments:
            try:
                created = _upsert_gmail_attachment(
                    db=db,
                    settings=settings,
                    storage=storage,
                    queue=queue,
                    gmail=gmail,
                    conn=conn,
                    message=message,
                    part=part,
                    part_path=part_path,
                    incremental=incremental,
                    uploaded_by=requested_by,
                )
                if created is None:
                    job.progress_skipped += 1
                else:
                    job.progress_done += 1
                db.commit()
            except Exception as exc:
                logger.exception("gmail.attachment_failed", extra={"operation": "gmail_sync"})
                db.rollback()
                job = db.get(SyncJob, job_id)
                conn = db.get(Connection, job.connection_id) if job and job.connection_id else conn
                if job:
                    job.progress_failed = int(job.progress_failed or 0) + 1
                    job.error_message = str(exc)[:2000]
                    db.commit()

        job = db.get(SyncJob, job_id)
        conn = db.get(Connection, job.connection_id) if job and job.connection_id else None
        if not job or not conn:
            raise RuntimeError("Gmail sync job or connection missing after attachment loop")

        # Deletion propagation. Incremental runs know exactly which messages were
        # deleted/trashed. A full pass lists every message matching the default
        # query, so an unlisted stored message is gone (messages.list excludes
        # Trash) - but only trust that when the query was not narrowed by hand.
        deletions_ok = True
        if changes is not None:
            deletions_ok = _tombstone_gmail_messages(
                db, search=search, conn=conn, message_ids=changes.removed
            )
        elif listed_message_ids is not None and query == settings.gmail_query:
            deletions_ok = _tombstone_gmail_unlisted(
                db, search=search, conn=conn, listed_message_ids=listed_message_ids
            )
        job = db.get(SyncJob, job_id)
        conn = db.get(Connection, job.connection_id) if job and job.connection_id else None
        if not job or not conn:
            raise RuntimeError("Gmail sync job or connection missing after deletion pass")

        job.status = SyncJobStatus.succeeded
        job.finished_at = utcnow()
        conn.status = ConnectionStatus.connected
        if job.progress_failed and not job.progress_done:
            conn.health = ConnectionHealth.error
            conn.status = ConnectionStatus.sync_failed
            conn.last_error = job.error_message
            conn.last_error_at = utcnow()
        elif job.progress_failed:
            conn.health = ConnectionHealth.degraded
            conn.last_error = job.error_message
            conn.last_error_at = utcnow()
        else:
            conn.health = ConnectionHealth.healthy
            conn.last_error = None
        conn.last_sync_at = utcnow()
        # Advance the checkpoint only when every attachment was handled AND every
        # deletion was applied; otherwise the next run replays the same window
        # (ready attachments are skipped by the existing per-attachment cursor,
        # failed ones get retried, failed deletions are attempted again).
        if new_checkpoint and not job.progress_failed and deletions_ok:
            cfg = dict(conn.config or {})
            cfg["gmail_history_id"] = new_checkpoint
            conn.config = cfg
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.get(SyncJob, job_id)
        conn = db.get(Connection, job.connection_id) if job and job.connection_id else None
        if job:
            job.error_message = str(exc)[:2000]
            job.finished_at = utcnow()
            job.status = (
                SyncJobStatus.dead if job.attempt >= job.max_attempts else SyncJobStatus.failed
            )
        if conn:
            conn.status = ConnectionStatus.sync_failed
            conn.health = ConnectionHealth.error
            conn.last_error = str(exc)[:2000]
            conn.last_error_at = utcnow()
        db.commit()
        raise


def _walk_attachment_parts(
    payload: dict[str, Any],
    prefix: str = "",
) -> Iterator[tuple[dict[str, Any], str]]:
    """Yield (part, dotted_part_path) for every attachment part, recursively.

    Gmail nests arbitrarily — a multipart/mixed can contain a
    multipart/alternative which itself contains further parts. A part is an
    attachment when it carries both a body.attachmentId and a non-empty
    filename; inline body parts have one or neither.
    """
    parts = payload.get("parts") or []
    for idx, part in enumerate(parts):
        path = f"{prefix}{idx}"
        body = part.get("body") or {}
        if body.get("attachmentId") and (part.get("filename") or "").strip():
            yield part, path
        if part.get("parts"):
            yield from _walk_attachment_parts(part, prefix=f"{path}.")


def _header(message: dict[str, Any], name: str) -> str:
    headers = (message.get("payload") or {}).get("headers") or []
    for h in headers:
        if str(h.get("name", "")).lower() == name.lower():
            return str(h.get("value") or "")
    return ""


def _list_messages(
    gmail: GmailService,
    settings: Settings,
    conn: Connection,
    query: str,
) -> list[dict[str, Any]]:
    if gmail.is_mock:
        return get_mock_messages()

    access = gmail.access_token(conn)
    headers = {"Authorization": f"Bearer {access}"}
    out: list[dict[str, Any]] = []
    page_token: Optional[str] = None
    while True:
        params: dict[str, Any] = {
            "q": query,
            "maxResults": settings.gmail_sync_page_size,
        }
        if page_token:
            params["pageToken"] = page_token
        resp = google_get_with_retry(
            f"{GMAIL_API_BASE}/messages", params=params, headers=headers, timeout=30.0
        )
        data = resp.json()
        for stub in data.get("messages") or []:
            detail = google_get_with_retry(
                f"{GMAIL_API_BASE}/messages/{stub['id']}",
                params={"format": "full"},
                headers=headers,
                timeout=30.0,
            )
            out.append(detail.json())
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return out


class HistoryExpired(Exception):
    """Gmail no longer has history for our checkpoint (HTTP 404)."""


def _current_history_id(gmail: GmailService, conn: Connection) -> str:
    if gmail.is_mock:
        return get_mock_history("0")[1]
    resp = google_get_with_retry(
        f"{GMAIL_API_BASE}/profile",
        params=None,
        headers={"Authorization": f"Bearer {gmail.access_token(conn)}"},
        timeout=30.0,
    )
    return str(resp.json().get("historyId") or "")


def _fetch_history(
    gmail: GmailService, conn: Connection, start_history_id: str
) -> tuple[list[dict[str, Any]], str]:
    """All history records since the checkpoint, plus the newest historyId."""
    if gmail.is_mock:
        return get_mock_history(start_history_id)

    import httpx

    headers = {"Authorization": f"Bearer {gmail.access_token(conn)}"}
    records: list[dict[str, Any]] = []
    latest = start_history_id
    page_token: Optional[str] = None
    while True:
        params: dict[str, Any] = {
            "startHistoryId": start_history_id,
            "maxResults": 500,
            "historyTypes": ["messageAdded", "messageDeleted", "labelAdded", "labelRemoved"],
        }
        if page_token:
            params["pageToken"] = page_token
        try:
            resp = google_get_with_retry(
                f"{GMAIL_API_BASE}/history", params=params, headers=headers, timeout=30.0
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HistoryExpired() from exc
            raise
        data = resp.json()
        records.extend(data.get("history") or [])
        latest = str(data.get("historyId") or latest)
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return records, latest


def _fetch_message(
    gmail: GmailService, conn: Connection, message_id: str
) -> Optional[dict[str, Any]]:
    if gmail.is_mock:
        return next((m for m in get_mock_messages() if m["id"] == message_id), None)

    import httpx

    try:
        resp = google_get_with_retry(
            f"{GMAIL_API_BASE}/messages/{message_id}",
            params={"format": "full"},
            headers={"Authorization": f"Bearer {gmail.access_token(conn)}"},
            timeout=30.0,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise
    return resp.json()


def _tombstone_gmail_messages(
    db: Session, *, search: Any, conn: Connection, message_ids: set[str]
) -> bool:
    """Returns True only if every matching document was hidden."""
    docs: list[Document] = []
    for mid in sorted(message_ids):
        docs.extend(
            db.scalars(
                select(Document).where(
                    Document.tenant_id == conn.tenant_id,
                    Document.source == "gmail",
                    Document.connection_id == conn.id,
                    Document.deleted_at.is_(None),
                    Document.external_id.startswith(f"{mid}:", autoescape=True),
                )
            ).all()
        )
    done = tombstone_many(
        db, search, docs, reason=REASON_SOURCE_REMOVED, log_operation="gmail_sync"
    )
    return done == len(docs)


def _tombstone_gmail_unlisted(
    db: Session, *, search: Any, conn: Connection, listed_message_ids: set[str]
) -> bool:
    """Returns True only if every unlisted document was hidden."""
    live = db.scalars(
        select(Document).where(
            Document.tenant_id == conn.tenant_id,
            Document.source == "gmail",
            Document.connection_id == conn.id,
            Document.deleted_at.is_(None),
        )
    ).all()
    docs = [
        d
        for d in live
        if d.external_id and message_id_from_external_id(d.external_id) not in listed_message_ids
    ]
    done = tombstone_many(
        db, search, docs, reason=REASON_SOURCE_REMOVED, log_operation="gmail_sync"
    )
    return done == len(docs)


def _decode_base64url(data: str) -> bytes:
    """Gmail returns base64url with padding stripped."""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _download_attachment(
    gmail: GmailService,
    settings: Settings,
    conn: Connection,
    message: dict[str, Any],
    part: dict[str, Any],
) -> bytes:
    if gmail.is_mock:
        data = (part.get("_mock_content") or "").encode("utf-8")
    else:
        access = gmail.access_token(conn)
        attachment_id = (part.get("body") or {}).get("attachmentId")
        resp = google_get_with_retry(
            f"{GMAIL_API_BASE}/messages/{message['id']}/attachments/{attachment_id}",
            params=None,
            headers={"Authorization": f"Bearer {access}"},
            timeout=120.0,
        )
        data = _decode_base64url(resp.json().get("data") or "")

    if len(data) > settings.gmail_max_attachment_bytes:
        raise RuntimeError(
            f"Attachment exceeds GMAIL_MAX_ATTACHMENT_BYTES ({settings.gmail_max_attachment_bytes})"
        )
    return data


def _upsert_gmail_attachment(
    *,
    db: Session,
    settings: Settings,
    storage: ObjectStorage,
    queue: IngestQueue,
    gmail: GmailService,
    conn: Connection,
    message: dict[str, Any],
    part: dict[str, Any],
    part_path: str,
    incremental: bool,
    uploaded_by: UUID,
) -> Optional[Document]:
    message_id = str(message["id"])
    attachment_id = str((part.get("body") or {}).get("attachmentId") or "")
    # Never derive an identifier from the filename — filenames are not unique
    # across (or even within) messages. If a mailbox ever reissues attachment
    # ids, `part_path` is the deterministic fallback.
    external_id = f"{message_id}:{attachment_id or part_path}"
    internal_date = str(message.get("internalDate") or "")

    mime = str(part.get("mimeType") or "application/octet-stream").lower().split(";")[0].strip()
    if mime not in settings.gmail_allowed_mime_set:
        logger.info(
            "gmail.attachment_skipped_mime",
            extra={"operation": "gmail_sync", "tenant_id": str(conn.tenant_id)},
        )
        return None

    existing = db.scalar(
        select(Document)
        .where(
            Document.tenant_id == conn.tenant_id,
            Document.source == "gmail",
            Document.external_id == external_id,
        )
        .order_by(Document.deleted_at.is_(None).desc(), Document.created_at.desc())
        .limit(1)
        .options(selectinload(Document.grants))
    )
    if existing is not None and existing.deleted_at is not None:
        if existing.deleted_reason == REASON_MANUAL:
            return None
        existing.deleted_at = None
        existing.deleted_reason = None
        existing.status = DocumentStatus.pending
    prev_cursor = ((conn.config or {}).get("message_cursors") or {}).get(external_id)
    if (
        incremental
        and existing
        and prev_cursor
        and prev_cursor == internal_date
        and existing.status == DocumentStatus.ready
    ):
        return None

    data = _download_attachment(gmail, settings, conn, message, part)

    filename = str(part.get("filename") or "attachment.bin")
    title = filename.rsplit(".", 1)[0] if "." in filename else filename
    subject = _header(message, "Subject")
    if subject:
        title = f"{title} — {subject}"
    source_url = f"https://mail.google.com/mail/u/0/#all/{message_id}"
    checksum = hashlib.sha256(data).hexdigest()

    if existing:
        doc = existing
        doc.title = title
        doc.mime_type = mime
        doc.source_url = source_url
        doc.visibility = DocumentVisibility.private
        doc.status = DocumentStatus.pending
        doc.error_message = None
        doc.connection_id = conn.id
        version_number = (
            db.scalar(
                select(DocumentVersion.version_number)
                .where(DocumentVersion.document_id == doc.id)
                .order_by(DocumentVersion.version_number.desc())
                .limit(1)
            )
            or 0
        ) + 1
    else:
        doc = Document(
            id=uuid4(),
            tenant_id=conn.tenant_id,
            connection_id=conn.id,
            title=title,
            source="gmail",
            external_id=external_id,
            mime_type=mime,
            source_url=source_url,
            # A mailbox is personal: always private, never any DocumentGrant rows.
            visibility=DocumentVisibility.private,
            status=DocumentStatus.pending,
            uploaded_by_user_id=uploaded_by,
        )
        db.add(doc)
        db.flush()
        version_number = 1

    version_id = uuid4()
    storage_key = storage.object_key(
        str(conn.tenant_id), str(doc.id), str(version_id), filename.replace("/", "_")
    )
    storage.ensure_bucket()
    storage.put_bytes(storage_key, data, content_type=mime)
    version = DocumentVersion(
        id=version_id,
        tenant_id=conn.tenant_id,
        document_id=doc.id,
        version_number=version_number,
        storage_key=storage_key,
        byte_size=len(data),
        checksum_sha256=checksum,
        mime_type=mime,
        original_filename=filename,
    )
    doc.current_version_id = version_id
    db.add(version)
    db.flush()  # persist version before ingest SyncJob FK

    ingest_job = SyncJob(
        id=uuid4(),
        tenant_id=conn.tenant_id,
        connection_id=conn.id,
        document_id=doc.id,
        version_id=version.id,
        job_type=SyncJobType.ingest,
        status=SyncJobStatus.queued,
        max_attempts=settings.ingest_max_attempts,
        payload={"source": "gmail", "external_id": external_id},
    )
    db.add(ingest_job)
    db.flush()
    msg_id = queue.enqueue_ingest(
        job_id=ingest_job.id,
        tenant_id=conn.tenant_id,
        document_id=doc.id,
        version_id=version.id,
    )
    ingest_job.sqs_message_id = msg_id

    cfg = dict(conn.config or {})
    cursors = dict(cfg.get("message_cursors") or {})
    cursors[external_id] = internal_date
    cfg["message_cursors"] = cursors
    conn.config = cfg
    db.flush()
    return doc
