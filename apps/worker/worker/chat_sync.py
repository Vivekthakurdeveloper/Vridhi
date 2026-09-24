"""Google Chat sync worker (Piece 4B).

Mirrors `worker/gmail_sync.py`'s structure. Per selected space: list members
(always, to re-confirm grants even on an otherwise-unchanged thread -- same
rule Piece 2 added to Drive's ACL recheck in `_upsert_drive_file`), list
messages since the space's cursor, group into threads, upsert one Document
per thread plus one per supported attachment, then tombstone any stored
thread this run's full listing no longer returns.

No History-API equivalent exists for Chat, so (unlike Gmail) deletion
detection here is always a full re-list-and-diff per space per sync, not a
checkpoint-driven incremental fetch -- only the *new-message* fetch is
cursor-filtered.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings, get_settings
from app.models import Connection, Document, DocumentGrant, DocumentVersion, SyncJob
from app.security import (
    ConnectionHealth,
    ConnectionStatus,
    DocumentStatus,
    DocumentVisibility,
    SyncJobStatus,
    SyncJobType,
    utcnow,
)
from app.services import autosync
from app.services.chat import ChatService, get_mock_members, get_mock_messages, get_mock_spaces, resolve_member_grants
from app.services.chat_threads import (
    filter_messages_since,
    format_thread_transcript,
    group_messages_by_thread,
    member_grant_set_changed,
    newest_create_time,
    thread_title,
)
from app.services.queue import IngestQueue, get_ingest_queue
from app.services.storage import ObjectStorage, get_object_storage
from app.services.tokens import TokenStore, get_token_store
from app.services.tombstone import (
    REASON_MANUAL,
    REASON_SOURCE_REMOVED,
    base_external_id,
    missing_external_ids,
    tombstone_many,
)
from worker.google_api import google_get_with_retry

logger = logging.getLogger(__name__)

CHAT_API_BASE = "https://chat.googleapis.com/v1"

# Re-exported for backward compatibility -- the real implementation now lives
# in app/services/chat_threads.py (a pure module with no DB/network deps), so
# apps/api/tests/test_chat_sync_unit.py no longer needs `worker` on
# PYTHONPATH to exercise it. See ARCHITECTURE_NOTES.md / final-fix-report C1.
_member_grant_set_changed = member_grant_set_changed


def process_chat_sync_job(db: Session, *, job_id: UUID, search: Any) -> None:
    settings = get_settings()
    storage = get_object_storage()
    queue = get_ingest_queue()
    tokens = get_token_store()
    _run_chat_sync(db, settings=settings, storage=storage, queue=queue, tokens=tokens, search=search, job_id=job_id)


def _run_chat_sync(
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
        logger.warning("chat.job_missing", extra={"operation": "chat_sync", "request_id": str(job_id)})
        return
    if job.job_type != SyncJobType.chat_sync:
        logger.warning("chat.job_type_mismatch", extra={"operation": "chat_sync", "request_id": str(job_id)})
        return
    if job.status == SyncJobStatus.succeeded:
        return

    job.attempt += 1
    job.status = SyncJobStatus.running
    job.started_at = utcnow()
    job.error_message = None
    db.commit()

    chat = ChatService(db, settings, tokens, storage, queue)
    try:
        if not job.connection_id:
            raise RuntimeError("Chat sync job missing connection_id")
        conn = db.scalar(
            select(Connection).where(Connection.id == job.connection_id).options(selectinload(Connection.credentials))
        )
        if not conn:
            raise RuntimeError("Chat connection not found")

        payload = dict(job.payload or {})
        space_ids: list[str] = list(payload.get("space_ids") or (conn.config or {}).get("selected_space_ids") or [])
        incremental = bool(payload.get("incremental", True))
        requested_by = UUID(payload["requested_by"]) if payload.get("requested_by") else conn.connected_by_user_id
        if not requested_by:
            raise RuntimeError("Missing requested_by user")

        total_threads = 0
        total_failed = 0
        total_skipped = 0
        all_deletions_ok = True

        for space_id in space_ids:
            try:
                space_display_name = _space_display_name(chat, conn, space_id)
                member_emails = _fetch_space_members(chat, conn, space_id)
                grant_ids = resolve_member_grants(db, conn.tenant_id, member_emails)

                cursor = str(((conn.config or {}).get("chat_cursors") or {}).get(space_id) or "")
                all_messages = _list_messages(chat, settings, conn, space_id)
                new_messages = filter_messages_since(all_messages, cursor) if incremental else all_messages
                grouped_new = group_messages_by_thread(new_messages)
                grouped_all = group_messages_by_thread(all_messages)

                # Every thread present in this space gets its grants re-checked,
                # even threads with no new messages (Piece 2's "always re-confirm"
                # rule), so iterate every thread this run saw, not just changed ones.
                for thread_name, thread_messages in grouped_all.items():
                    try:
                        created = _upsert_chat_thread(
                            db=db,
                            settings=settings,
                            storage=storage,
                            queue=queue,
                            chat=chat,
                            conn=conn,
                            space_id=space_id,
                            space_display_name=space_display_name,
                            thread_name=thread_name,
                            thread_messages=thread_messages,
                            has_new_messages=thread_name in grouped_new,
                            grant_ids=grant_ids,
                            uploaded_by=requested_by,
                        )
                        if created is None:
                            total_skipped += 1
                        else:
                            total_threads += 1
                        db.commit()
                    except Exception:
                        logger.exception("chat.thread_failed", extra={"operation": "chat_sync"})
                        db.rollback()
                        total_failed += 1

                # Deletion: only trust the diff when this space's listing above
                # completed without raising (same fail-closed ordering as
                # Drive/Gmail's deletion pass).
                #
                # Threads and their attachments share source="chat" (see C2),
                # with an attachment's external_id shaped
                # "<thread_name>::att:<attachment_name>" (the same "::" inner-
                # id convention drive_sync.py uses for ZIP entries). The
                # listing (`grouped_all.keys()`) only ever contains thread
                # names, never attachment ids, so the diff must compare each
                # stored id's *base* (base_external_id strips the "::..."
                # suffix) against the listing -- exactly how
                # `_tombstone_missing_drive_docs` handles a ZIP's inner files.
                # Otherwise every live thread's attachments would look
                # "missing" on every sync (their own id is never listed) and
                # get tombstoned immediately.
                stored_docs = list(
                    db.scalars(
                        select(Document).where(
                            Document.tenant_id == conn.tenant_id,
                            Document.source == "chat",
                            Document.connection_id == conn.id,
                            Document.deleted_at.is_(None),
                            Document.external_id.startswith(f"{space_id}/threads/", autoescape=True),
                        )
                    ).all()
                )
                stored_bases = {base_external_id(d.external_id) for d in stored_docs if d.external_id}
                missing_bases = missing_external_ids(stored_bases, grouped_all.keys())
                if missing_bases:
                    docs_to_hide = [
                        d for d in stored_docs if d.external_id and base_external_id(d.external_id) in missing_bases
                    ]
                    done = tombstone_many(db, search, docs_to_hide, reason=REASON_SOURCE_REMOVED, log_operation="chat_sync")
                    all_deletions_ok = all_deletions_ok and done == len(docs_to_hide)

                new_cursor = newest_create_time(all_messages) or cursor
                db.refresh(conn, ["config"])
                cfg = dict(conn.config or {})
                cursors = dict(cfg.get("chat_cursors") or {})
                cursors[space_id] = new_cursor
                cfg["chat_cursors"] = cursors
                conn.config = cfg
                db.commit()
            except Exception:
                logger.exception("chat.space_failed", extra={"operation": "chat_sync"})
                total_failed += 1
                all_deletions_ok = False

        job = db.get(SyncJob, job_id)
        conn = db.get(Connection, job.connection_id) if job and job.connection_id else None
        if not job or not conn:
            raise RuntimeError("Chat sync job or connection missing after space loop")

        job.progress_total = total_threads + total_failed + total_skipped
        job.progress_done = total_threads
        job.progress_failed = total_failed
        job.progress_skipped = total_skipped

        db.refresh(conn, ["config", "status"])
        job.status = SyncJobStatus.succeeded
        job.finished_at = utcnow()
        if conn.status == ConnectionStatus.disconnected:
            db.commit()
            return

        sync_ok = not total_failed and all_deletions_ok
        conn.status = ConnectionStatus.connected
        if not sync_ok and not total_threads:
            conn.health = ConnectionHealth.error
            conn.status = ConnectionStatus.sync_failed
        elif not sync_ok:
            conn.health = ConnectionHealth.degraded
        else:
            conn.health = ConnectionHealth.healthy
            conn.last_error = None
        conn.last_sync_at = utcnow()
        conn.config = autosync.record_sync_outcome(
            conn.config, succeeded=sync_ok, max_failures=settings.auto_sync_max_consecutive_failures
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.get(SyncJob, job_id)
        conn = db.get(Connection, job.connection_id) if job and job.connection_id else None
        if job:
            job.error_message = str(exc)[:2000]
            job.finished_at = utcnow()
            job.status = SyncJobStatus.dead if job.attempt >= job.max_attempts else SyncJobStatus.failed
        if conn:
            conn.status = ConnectionStatus.sync_failed
            conn.health = ConnectionHealth.error
            conn.last_error = str(exc)[:2000]
            conn.last_error_at = utcnow()
            if job and (job.status == SyncJobStatus.dead or settings.queue_backend.lower().strip() == "db"):
                conn.config = autosync.record_sync_outcome(
                    conn.config, succeeded=False, max_failures=settings.auto_sync_max_consecutive_failures
                )
        db.commit()
        raise


def _space_display_name(chat: ChatService, conn: Connection, space_id: str) -> str:
    if chat.is_mock:
        return next((s["displayName"] for s in get_mock_spaces() if s["name"] == space_id), space_id)
    access = chat.access_token(conn)
    resp = google_get_with_retry(
        f"{CHAT_API_BASE}/{space_id}", params=None, headers={"Authorization": f"Bearer {access}"}, timeout=30.0
    )
    return str(resp.json().get("displayName") or space_id)


def _fetch_space_members(chat: ChatService, conn: Connection, space_id: str) -> list[str]:
    """Returns member email addresses. See Task 4's Known-risk note: real Chat
    API email availability on Membership.member is unconfirmed live; mock
    fixtures return email directly."""
    if chat.is_mock:
        return get_mock_members(space_id)
    access = chat.access_token(conn)
    emails: list[str] = []
    page_token = None
    while True:
        params: dict[str, Any] = {"pageSize": 100}
        if page_token:
            params["pageToken"] = page_token
        resp = google_get_with_retry(
            f"{CHAT_API_BASE}/{space_id}/members",
            params=params,
            headers={"Authorization": f"Bearer {access}"},
            timeout=30.0,
        )
        data = resp.json()
        for m in data.get("memberships") or []:
            member = m.get("member") or {}
            email = str(member.get("email") or "").strip()
            if email:
                emails.append(email)
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return emails


def _list_messages(chat: ChatService, settings: Settings, conn: Connection, space_id: str) -> list[dict[str, Any]]:
    if chat.is_mock:
        return [m for m in get_mock_messages() if str((m.get("thread") or {}).get("name") or "").startswith(f"{space_id}/threads/")]
    access = chat.access_token(conn)
    messages: list[dict[str, Any]] = []
    page_token = None
    while True:
        params: dict[str, Any] = {"pageSize": settings.chat_sync_page_size}
        if page_token:
            params["pageToken"] = page_token
        resp = google_get_with_retry(
            f"{CHAT_API_BASE}/{space_id}/messages",
            params=params,
            headers={"Authorization": f"Bearer {access}"},
            timeout=30.0,
        )
        data = resp.json()
        messages.extend(data.get("messages") or [])
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return messages


def _download_attachment(chat: ChatService, settings: Settings, conn: Connection, attachment: dict[str, Any]) -> bytes:
    if chat.is_mock:
        return f"Mock content for {attachment.get('contentName')}".encode("utf-8")
    access = chat.access_token(conn)
    download_uri = str(attachment["downloadUri"])
    try:
        resp = google_get_with_retry(
            download_uri, params=None, headers={"Authorization": f"Bearer {access}"}, timeout=120.0
        )
    except httpx.HTTPStatusError as exc:
        # google_get_with_retry raises httpx's default HTTPStatusError, whose
        # message embeds the full request URL -- downloadUri can carry a
        # signed query-param token, and this message can propagate into
        # job.error_message / conn.last_error, which the frontend renders
        # verbatim. Strip the query string and re-raise with a generic
        # message instead of letting the raw URL leak.
        sanitized_url = download_uri.split("?", 1)[0]
        status = exc.response.status_code if exc.response is not None else "?"
        raise RuntimeError(f"Attachment download failed (HTTP {status}) for {sanitized_url}") from None
    # Cheap pre-check: a Content-Length header (when present) lets us reject
    # an oversized attachment before touching resp.content, without needing
    # a streaming rewrite of google_get_with_retry (shared code, out of scope
    # here). Not a hard guarantee -- a server can lie about or omit this
    # header -- so the buffered-length check below still runs regardless.
    content_length = resp.headers.get("content-length")
    if content_length is not None:
        try:
            declared_bytes = int(content_length)
        except ValueError:
            declared_bytes = None
        if declared_bytes is not None and declared_bytes > settings.chat_max_attachment_bytes:
            raise RuntimeError(f"Attachment exceeds CHAT_MAX_ATTACHMENT_BYTES ({settings.chat_max_attachment_bytes})")
    data = resp.content
    if len(data) > settings.chat_max_attachment_bytes:
        raise RuntimeError(f"Attachment exceeds CHAT_MAX_ATTACHMENT_BYTES ({settings.chat_max_attachment_bytes})")
    return data


def _chat_room_url(thread_name: str) -> str:
    """Best-effort citation link into Chat's room UI ("spaces/<S>/threads/<T>"
    -> "https://chat.google.com/room/<S>"). Chat has no public deep-link
    format documented for a specific thread, and there is no live API here to
    verify against, so this is a plausible best-effort room-level link, not a
    guaranteed-correct thread permalink -- cosmetic/citation-only."""
    parts = thread_name.split("/")
    space = parts[1] if len(parts) > 1 else thread_name
    return f"https://chat.google.com/room/{space}"


def _chat_attachment_external_id(thread_name: str, attachment_name: str) -> str:
    """"<thread_name>::att:<attachment_name>" -- reuses the "::" inner-id
    convention drive_sync.py uses for ZIP entries, so a single
    source == "chat" deletion diff (base_external_id-stripped) naturally
    covers both threads and their attachments. See C2."""
    return f"{thread_name}::att:{attachment_name}"


def _refresh_document_grants(
    *,
    db: Session,
    settings: Settings,
    queue: IngestQueue,
    conn: Connection,
    doc: Document,
    grant_ids: list[UUID],
    external_id: str,
) -> None:
    """Delete+recreate ``doc``'s DocumentGrant rows from the freshly-resolved
    member set, enqueuing a re-ingest only if the resolved set actually
    changed (so the denormalised OpenSearch ACL fields get rewritten too).
    Shared by the thread-level and attachment-level "content unchanged, only
    re-confirm grants" paths."""
    before_grant_ids = {g.user_id for g in (doc.grants or [])}
    after_grant_ids = set(grant_ids)
    for g in list(doc.grants or []):
        db.delete(g)
    db.flush()
    for uid in grant_ids:
        db.add(DocumentGrant(id=uuid4(), tenant_id=conn.tenant_id, document_id=doc.id, user_id=uid))
    if member_grant_set_changed(before=before_grant_ids, after=after_grant_ids) and doc.current_version_id:
        _enqueue_ingest_job(
            db=db, settings=settings, queue=queue, conn=conn, document_id=doc.id,
            version_id=doc.current_version_id, external_id=external_id,
        )


def _refresh_chat_attachment_grants(
    *,
    db: Session,
    settings: Settings,
    queue: IngestQueue,
    conn: Connection,
    thread_name: str,
    attachment: dict[str, Any],
    grant_ids: list[UUID],
) -> None:
    """The attachment-side half of C2's fix: on a thread with no new messages
    (skipped in `_upsert_chat_thread` before any download/re-ingest), an
    existing attachment Document's grants still need re-confirming -- without
    re-downloading or re-versioning it. No-ops if the attachment was never
    stored (e.g. filtered by mime, or not yet synced) or is soft-deleted."""
    external_id = _chat_attachment_external_id(thread_name, str(attachment.get("name") or ""))
    existing = db.scalar(
        select(Document)
        .where(
            Document.tenant_id == conn.tenant_id,
            Document.source == "chat",
            Document.external_id == external_id,
            Document.deleted_at.is_(None),
        )
        .options(selectinload(Document.grants))
    )
    if existing is None:
        return
    _refresh_document_grants(
        db=db, settings=settings, queue=queue, conn=conn, doc=existing, grant_ids=grant_ids, external_id=external_id,
    )


def _upsert_chat_thread(
    *,
    db: Session,
    settings: Settings,
    storage: ObjectStorage,
    queue: IngestQueue,
    chat: ChatService,
    conn: Connection,
    space_id: str,
    space_display_name: str,
    thread_name: str,
    thread_messages: list[dict[str, Any]],
    has_new_messages: bool,
    grant_ids: list[UUID],
    uploaded_by: UUID,
) -> Any:
    external_id = thread_name  # already unique: "spaces/<S>/threads/<T>"
    existing = db.scalar(
        select(Document)
        .where(Document.tenant_id == conn.tenant_id, Document.source == "chat", Document.external_id == external_id)
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

    # I2/I3: only skip re-ingest when the existing document is actually
    # `ready` -- matching drive_sync.py's `content_unchanged` guard exactly.
    # Without the status check, a thread stuck `failed`/`pending` (a prior
    # ingest error, a worker crash mid-sync, etc.) would be permanently
    # skipped forever the moment the cursor advances past its messages, since
    # `has_new_messages` would then stay False on every subsequent run.
    if not has_new_messages and existing is not None and existing.status == DocumentStatus.ready:
        # Content unchanged -- still re-confirm and rewrite grants (Piece 2's
        # rule), mirroring drive_sync.py's `content_unchanged` branch.
        _refresh_document_grants(
            db=db, settings=settings, queue=queue, conn=conn, doc=existing, grant_ids=grant_ids, external_id=external_id,
        )
        # C2: attachment grants must also be re-confirmed here -- this early
        # return used to skip the attachment loop entirely, so an unchanged
        # thread's attachments never got their grants refreshed even when a
        # space member was removed. Only grants are refreshed (no download/
        # re-versioning) so this stays cheap on the common "nothing changed"
        # path.
        for message in thread_messages:
            for attachment in message.get("attachment") or []:
                try:
                    _refresh_chat_attachment_grants(
                        db=db, settings=settings, queue=queue, conn=conn,
                        thread_name=thread_name, attachment=attachment, grant_ids=grant_ids,
                    )
                    db.commit()
                except Exception:
                    logger.exception("chat.attachment_grant_refresh_failed", extra={"operation": "chat_sync"})
                    db.rollback()
        return None

    transcript = format_thread_transcript(thread_messages)
    title = thread_title(space_display_name, thread_messages)
    checksum = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
    source_url = _chat_room_url(thread_name)

    if existing:
        doc = existing
        doc.title = title
        doc.mime_type = "text/plain"
        doc.source_url = source_url
        doc.visibility = DocumentVisibility.selected
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
            source="chat",
            external_id=external_id,
            mime_type="text/plain",
            source_url=source_url,
            visibility=DocumentVisibility.selected,
            status=DocumentStatus.pending,
            uploaded_by_user_id=uploaded_by,
        )
        db.add(doc)
        db.flush()
        version_number = 1

    for g in list(existing.grants or []) if existing else []:
        db.delete(g)
    db.flush()
    for uid in grant_ids:
        db.add(DocumentGrant(id=uuid4(), tenant_id=conn.tenant_id, document_id=doc.id, user_id=uid))

    version_id = uuid4()
    storage_key = storage.object_key(str(conn.tenant_id), str(doc.id), str(version_id), "transcript.txt")
    storage.ensure_bucket()
    storage.put_bytes(storage_key, transcript.encode("utf-8"), content_type="text/plain")
    version = DocumentVersion(
        id=version_id,
        tenant_id=conn.tenant_id,
        document_id=doc.id,
        version_number=version_number,
        storage_key=storage_key,
        byte_size=len(transcript.encode("utf-8")),
        checksum_sha256=checksum,
        mime_type="text/plain",
        original_filename="transcript.txt",
    )
    doc.current_version_id = version_id
    db.add(version)
    db.flush()

    _enqueue_ingest_job(
        db=db, settings=settings, queue=queue, conn=conn, document_id=doc.id, version_id=version.id, external_id=external_id,
    )

    for message in thread_messages:
        for attachment in message.get("attachment") or []:
            try:
                _upsert_chat_attachment(
                    db=db, settings=settings, storage=storage, queue=queue, chat=chat, conn=conn,
                    thread_name=thread_name, message=message, attachment=attachment,
                    grant_ids=grant_ids, uploaded_by=uploaded_by,
                )
                db.commit()
            except Exception:
                logger.exception("chat.attachment_failed", extra={"operation": "chat_sync"})
                db.rollback()

    return doc


def _upsert_chat_attachment(
    *,
    db: Session,
    settings: Settings,
    storage: ObjectStorage,
    queue: IngestQueue,
    chat: ChatService,
    conn: Connection,
    thread_name: str,
    message: dict[str, Any],
    attachment: dict[str, Any],
    grant_ids: list[UUID],
    uploaded_by: UUID,
) -> None:
    mime = str(attachment.get("contentType") or "application/octet-stream").lower().split(";")[0].strip()
    if mime not in settings.chat_allowed_mime_set:
        logger.info("chat.attachment_skipped_mime", extra={"operation": "chat_sync", "tenant_id": str(conn.tenant_id)})
        return

    # C2: source="chat" (not "chat_attachment") with the "::att:" inner-id
    # convention, so one source == "chat" deletion diff on the thread covers
    # threads and attachments together -- see _run_chat_sync's deletion pass
    # and _chat_attachment_external_id's docstring.
    external_id = _chat_attachment_external_id(thread_name, str(attachment["name"]))
    existing = db.scalar(
        select(Document)
        .where(Document.tenant_id == conn.tenant_id, Document.source == "chat", Document.external_id == external_id)
        .order_by(Document.deleted_at.is_(None).desc(), Document.created_at.desc())
        .limit(1)
        .options(selectinload(Document.grants))
    )
    if existing is not None and existing.deleted_at is not None:
        if existing.deleted_reason == REASON_MANUAL:
            return
        existing.deleted_at = None
        existing.deleted_reason = None
        existing.status = DocumentStatus.pending

    data = _download_attachment(chat, settings, conn, attachment)
    filename = str(attachment.get("contentName") or "attachment.bin")
    title = filename.rsplit(".", 1)[0] if "." in filename else filename
    checksum = hashlib.sha256(data).hexdigest()
    source_url = _chat_room_url(thread_name)

    if existing:
        doc = existing
        doc.title = title
        doc.mime_type = mime
        doc.source_url = source_url
        doc.visibility = DocumentVisibility.selected
        doc.status = DocumentStatus.pending
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
            source="chat",
            external_id=external_id,
            mime_type=mime,
            source_url=source_url,
            visibility=DocumentVisibility.selected,
            status=DocumentStatus.pending,
            uploaded_by_user_id=uploaded_by,
        )
        db.add(doc)
        db.flush()
        version_number = 1

    for g in list(existing.grants or []) if existing else []:
        db.delete(g)
    db.flush()
    for uid in grant_ids:
        db.add(DocumentGrant(id=uuid4(), tenant_id=conn.tenant_id, document_id=doc.id, user_id=uid))

    version_id = uuid4()
    storage_key = storage.object_key(str(conn.tenant_id), str(doc.id), str(version_id), filename.replace("/", "_"))
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
    db.flush()

    _enqueue_ingest_job(
        db=db, settings=settings, queue=queue, conn=conn, document_id=doc.id, version_id=version.id, external_id=external_id,
    )


def _enqueue_ingest_job(
    *, db: Session, settings: Settings, queue: IngestQueue, conn: Connection, document_id: UUID, version_id: UUID, external_id: str,
) -> None:
    ingest_job = SyncJob(
        id=uuid4(),
        tenant_id=conn.tenant_id,
        connection_id=conn.id,
        document_id=document_id,
        version_id=version_id,
        job_type=SyncJobType.ingest,
        status=SyncJobStatus.queued,
        max_attempts=settings.ingest_max_attempts,
        payload={"source": "chat", "external_id": external_id},
    )
    db.add(ingest_job)
    db.flush()
    msg_id = queue.enqueue_ingest(job_id=ingest_job.id, tenant_id=conn.tenant_id, document_id=document_id, version_id=version_id)
    ingest_job.sqs_message_id = msg_id
