from __future__ import annotations

import hashlib
import logging
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings, get_settings
from app.models import (
    Connection,
    Document,
    DocumentGrant,
    DocumentGroupGrant,
    DocumentVersion,
    SyncJob,
)
from app.security import (
    ConnectionHealth,
    ConnectionStatus,
    DocumentStatus,
    DocumentVisibility,
    SyncJobStatus,
    SyncJobType,
    utcnow,
)
from app.services.drive import DriveService, get_mock_files
from app.services.queue import IngestQueue, get_ingest_queue
from app.services.storage import ObjectStorage, get_object_storage
from app.services.tokens import TokenStore, get_token_store
from app.services.tombstone import (
    REASON_MANUAL,
    REASON_SOURCE_REMOVED,
    missing_external_ids,
    tombstone_many,
)

logger = logging.getLogger(__name__)


def process_drive_sync_job(db: Session, *, job_id: UUID, search: Any) -> None:
    """Worker entrypoint: uses API app services (storage/queue/tokens/settings)."""
    settings = get_settings()
    storage = get_object_storage()
    queue = get_ingest_queue()
    tokens = get_token_store()
    _run_drive_sync(
        db,
        settings=settings,
        storage=storage,
        queue=queue,
        tokens=tokens,
        search=search,
        job_id=job_id,
    )


def _run_drive_sync(
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
    if not job or job.job_type != SyncJobType.drive_sync:
        return
    if job.status == SyncJobStatus.succeeded:
        return

    job.attempt += 1
    job.status = SyncJobStatus.running
    job.started_at = utcnow()
    job.error_message = None
    db.commit()

    drive = DriveService(db, settings, tokens, storage, queue)
    try:
        if not job.connection_id:
            raise RuntimeError("Drive sync job missing connection_id")
        conn = db.scalar(
            select(Connection)
            .where(Connection.id == job.connection_id)
            .options(selectinload(Connection.credentials))
        )
        if not conn:
            raise RuntimeError("Drive connection not found")

        from app.services.groups import sync_groups_and_memberships

        # Groups sync is a Workspace-Enterprise capability: the token it uses
        # (get_admin_impersonated_token) is minted from the *enterprise* mock
        # flag, so keying this off Drive's mode would either write fixture
        # groups into a live tenant or fire a mock token at the real Admin SDK
        # whenever the two flags disagree.
        sync_groups_and_memberships(
            db, conn.tenant_id, is_mock=settings.workspace_enterprise_is_mock
        )

        payload = dict(job.payload or {})
        folder_ids = list(payload.get("folder_ids") or [])
        visibility = DocumentVisibility(payload.get("visibility") or "org")
        selected_user_ids = [UUID(x) for x in (payload.get("selected_user_ids") or [])]
        requested_by = (
            UUID(payload["requested_by"]) if payload.get("requested_by") else conn.connected_by_user_id
        )
        if not requested_by:
            raise RuntimeError("Missing requested_by user")

        files = _list_files_for_folders(drive, settings, conn, folder_ids)
        job.progress_total = len(files)
        job.progress_done = 0
        job.progress_failed = 0
        job.progress_skipped = 0
        db.commit()

        for item in files:
            try:
                created = _upsert_drive_file(
                    db=db,
                    settings=settings,
                    storage=storage,
                    queue=queue,
                    drive=drive,
                    conn=conn,
                    file_meta=item,
                    visibility_default=visibility,
                    selected_user_ids=selected_user_ids,
                    uploaded_by=requested_by,
                )
                if created is None:
                    job.progress_skipped += 1
                else:
                    job.progress_done += 1
                db.commit()
            except Exception as exc:
                logger.exception("drive.file_failed", extra={"operation": "drive_sync"})
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
            raise RuntimeError("Drive sync job or connection missing after file loop")

        # Deletion propagation. Reaching this point means the folder listing
        # completed without raising (a listing error fails the whole job above),
        # so "stored but not listed" really means deleted, trashed, moved out,
        # or its folder was un-selected. Never run this on an incomplete listing.
        removed_count = _tombstone_missing_drive_docs(
            db, search=search, conn=conn, listed_ids={str(f["id"]) for f in files}
        )
        if removed_count:
            logger.info(
                "drive.tombstoned_missing",
                extra={"operation": "drive_sync", "tenant_id": str(conn.tenant_id)},
            )
        job = db.get(SyncJob, job_id)
        conn = db.get(Connection, job.connection_id) if job and job.connection_id else None
        if not job or not conn:
            raise RuntimeError("Drive sync job or connection missing after deletion pass")

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
        cfg = dict(conn.config or {})
        cfg["page_token"] = utcnow().isoformat()
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


def _tombstone_missing_drive_docs(
    db: Session, *, search: Any, conn: Connection, listed_ids: set[str]
) -> int:
    live = db.scalars(
        select(Document).where(
            Document.tenant_id == conn.tenant_id,
            Document.source == "google_drive",
            Document.connection_id == conn.id,
            Document.deleted_at.is_(None),
        )
    ).all()
    missing = missing_external_ids((d.external_id for d in live if d.external_id), listed_ids)
    if not missing:
        return 0
    docs = [d for d in live if d.external_id in missing]
    return tombstone_many(
        db, search, docs, reason=REASON_SOURCE_REMOVED, log_operation="drive_sync"
    )


def _list_files_for_folders(
    drive: DriveService,
    settings: Settings,
    conn: Connection,
    folder_ids: list[str],
) -> list[dict[str, Any]]:
    if settings.google_drive_mode.lower() == "mock":
        out: list[dict[str, Any]] = []
        for fid in folder_ids:
            out.extend(get_mock_files(fid))
        return out

    access = drive._access_token(conn)
    import httpx

    files: list[dict[str, Any]] = []
    for folder_id in folder_ids:
        page_token = None
        while True:
            q = f"'{folder_id}' in parents and trashed=false"
            params: dict[str, Any] = {
                "q": q,
                "fields": "nextPageToken, files(id, name, mimeType, webViewLink, modifiedTime, size)",
                "pageSize": settings.drive_sync_page_size,
            }
            if page_token:
                params["pageToken"] = page_token
            resp = httpx.get(
                "https://www.googleapis.com/drive/v3/files",
                params=params,
                headers={"Authorization": f"Bearer {access}"},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            for f in data.get("files") or []:
                mime = f.get("mimeType") or ""
                if mime.startswith("application/vnd.google-apps.folder"):
                    continue
                if mime not in settings.drive_allowed_mime_set and not mime.startswith(
                    "application/vnd.google-apps."
                ):
                    continue
                perm_resp = httpx.get(
                    f"https://www.googleapis.com/drive/v3/files/{f['id']}/permissions",
                    params={"fields": "permissions(type,role,emailAddress)"},
                    headers={"Authorization": f"Bearer {access}"},
                    timeout=20.0,
                )
                permissions = []
                if perm_resp.status_code == 200:
                    permissions = perm_resp.json().get("permissions") or []
                f["permissions"] = permissions
                files.append(f)
            page_token = data.get("nextPageToken")
            if not page_token:
                break
    return files


def _download_file_bytes(
    drive: DriveService,
    settings: Settings,
    conn: Connection,
    file_meta: dict[str, Any],
) -> tuple[bytes, str]:
    if settings.google_drive_mode.lower() == "mock":
        content = (file_meta.get("content") or "").encode("utf-8")
        return content, file_meta.get("mimeType") or "text/plain"

    import httpx

    access = drive._access_token(conn)
    file_id = file_meta["id"]
    mime = file_meta.get("mimeType") or "application/octet-stream"
    if mime == "application/vnd.google-apps.document":
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export"
        params = {"mimeType": "text/plain"}
        out_mime = "text/plain"
    elif mime == "application/vnd.google-apps.spreadsheet":
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export"
        params = {"mimeType": "text/csv"}
        out_mime = "text/csv"
    elif mime == "application/vnd.google-apps.presentation":
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export"
        params = {"mimeType": "text/plain"}
        out_mime = "text/plain"
    else:
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
        params = {"alt": "media"}
        out_mime = mime
    resp = httpx.get(
        url, params=params, headers={"Authorization": f"Bearer {access}"}, timeout=120.0
    )
    resp.raise_for_status()
    data = resp.content
    if len(data) > settings.drive_max_file_bytes:
        raise RuntimeError(f"File exceeds DRIVE_MAX_FILE_BYTES ({settings.drive_max_file_bytes})")
    return data, out_mime


def _enqueue_ingest_job(
    *,
    db: Session,
    settings: Settings,
    queue: IngestQueue,
    conn: Connection,
    document_id: UUID,
    version_id: UUID,
    external_id: str,
) -> SyncJob:
    """Create + enqueue the child ingest job for a document version.

    Both the normal (re-)ingest path and the content-unchanged ACL-refresh path
    go through here so the job row and the queue message keep exactly one shape.
    Re-running ingest for a version is idempotent (worker/ingest.py deletes the
    version's chunks and calls ``search.delete_by_document`` before reindexing),
    which is what makes it safe to replay purely to refresh the denormalised ACL
    fields in OpenSearch.
    """
    ingest_job = SyncJob(
        id=uuid4(),
        tenant_id=conn.tenant_id,
        connection_id=conn.id,
        document_id=document_id,
        version_id=version_id,
        job_type=SyncJobType.ingest,
        status=SyncJobStatus.queued,
        max_attempts=settings.ingest_max_attempts,
        payload={"source": "google_drive", "external_id": external_id},
    )
    db.add(ingest_job)
    db.flush()
    msg_id = queue.enqueue_ingest(
        job_id=ingest_job.id,
        tenant_id=conn.tenant_id,
        document_id=document_id,
        version_id=version_id,
    )
    ingest_job.sqs_message_id = msg_id
    return ingest_job


def _upsert_drive_file(
    *,
    db: Session,
    settings: Settings,
    storage: ObjectStorage,
    queue: IngestQueue,
    drive: DriveService,
    conn: Connection,
    file_meta: dict[str, Any],
    visibility_default: DocumentVisibility,
    selected_user_ids: list[UUID],
    uploaded_by: UUID,
) -> Optional[Document]:
    external_id = str(file_meta["id"])
    modified = str(file_meta.get("modifiedTime") or "")
    # Include deleted rows, preferring a live one. A document the user deleted by
    # hand must stay deleted; one we hid because the file left Drive is revived
    # (same row, no duplicate) when the file returns.
    existing = db.scalar(
        select(Document)
        .where(
            Document.tenant_id == conn.tenant_id,
            Document.source == "google_drive",
            Document.external_id == external_id,
        )
        .order_by(Document.deleted_at.is_(None).desc(), Document.created_at.desc())
        .limit(1)
        .options(selectinload(Document.grants), selectinload(Document.group_grants))
    )
    if existing is not None and existing.deleted_at is not None:
        if existing.deleted_reason == REASON_MANUAL:
            return None
        existing.deleted_at = None
        existing.deleted_reason = None
        existing.status = DocumentStatus.pending
    prev_modified = ((conn.config or {}).get("file_cursors") or {}).get(external_id)
    content_unchanged = (
        existing and prev_modified and prev_modified == modified and existing.status == DocumentStatus.ready
    )
    if content_unchanged:
        # Content hasn't changed, so skip re-downloading/re-ingesting it --
        # but permissions can change without touching modifiedTime (the
        # single biggest gap this phase exists to close), so always
        # re-resolve and update grants even on this otherwise-skipped path.
        visibility, grant_ids, group_grant_ids = drive.map_permissions_to_acl(
            tenant_id=conn.tenant_id,
            permissions=list(file_meta.get("permissions") or []),
            default_visibility=visibility_default,
            selected_user_ids=selected_user_ids,
        )
        # Snapshot what is actually stored (and therefore what is denormalised
        # into OpenSearch) *before* we overwrite it, so we can tell whether this
        # sync really changed the ACL.
        before_visibility = existing.visibility
        before_grant_ids = {g.user_id for g in (existing.grants or [])}
        before_group_grant_ids = {g.group_id for g in (existing.group_grants or [])}

        existing.visibility = visibility
        for g in list(existing.grants or []):
            db.delete(g)
        for g in list(existing.group_grants or []):
            db.delete(g)
        db.flush()
        for uid in grant_ids:
            db.add(DocumentGrant(id=uuid4(), tenant_id=conn.tenant_id, document_id=existing.id, user_id=uid))
        for gid in group_grant_ids:
            db.add(DocumentGroupGrant(id=uuid4(), tenant_id=conn.tenant_id, document_id=existing.id, group_id=gid))

        acl_changed = (
            before_visibility != visibility
            or before_grant_ids != set(grant_ids)
            or before_group_grant_ids != set(group_grant_ids)
        )
        if acl_changed and existing.current_version_id:
            # Postgres is now correct, but worker/ingest.py is the only writer of
            # the denormalised ACL fields (visibility / granted_user_ids /
            # granted_group_ids) into OpenSearch -- and retrieval treats that
            # index as the authorization authority. Without replaying ingest for
            # the current version, every already-indexed chunk would keep the
            # stale (pre-revocation) ACL and stay answerable via /v1/search and
            # /v1/chat. Ingest's delete-then-reindex is idempotent, so this is a
            # safe replay; we only pay for it when the ACL actually moved.
            _enqueue_ingest_job(
                db=db,
                settings=settings,
                queue=queue,
                conn=conn,
                document_id=existing.id,
                version_id=existing.current_version_id,
                external_id=external_id,
            )
            logger.info(
                "drive.acl_changed_reingest_enqueued",
                extra={
                    "operation": "drive_sync",
                    "tenant_id": str(conn.tenant_id),
                    "request_id": str(existing.id),
                },
            )
        db.commit()
        return None

    data, mime = _download_file_bytes(drive, settings, conn, file_meta)
    visibility, grant_ids, group_grant_ids = drive.map_permissions_to_acl(
        tenant_id=conn.tenant_id,
        permissions=list(file_meta.get("permissions") or []),
        default_visibility=visibility_default,
        selected_user_ids=selected_user_ids,
    )

    title = str(file_meta.get("name") or "Untitled")
    source_url = file_meta.get("webViewLink")
    checksum = hashlib.sha256(data).hexdigest()

    if existing:
        doc = existing
        doc.title = title.rsplit(".", 1)[0] if "." in title else title
        doc.mime_type = mime
        doc.source_url = source_url
        doc.visibility = visibility
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
        for g in list(doc.grants or []):
            db.delete(g)
        for g in list(doc.group_grants or []):
            db.delete(g)
    else:
        doc = Document(
            id=uuid4(),
            tenant_id=conn.tenant_id,
            connection_id=conn.id,
            title=title.rsplit(".", 1)[0] if "." in title else title,
            source="google_drive",
            external_id=external_id,
            mime_type=mime,
            source_url=source_url,
            visibility=visibility,
            status=DocumentStatus.pending,
            uploaded_by_user_id=uploaded_by,
        )
        db.add(doc)
        db.flush()
        version_number = 1

    version_id = uuid4()
    storage_key = storage.object_key(
        str(conn.tenant_id), str(doc.id), str(version_id), title.replace("/", "_")
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
        original_filename=title,
    )
    doc.current_version_id = version_id
    db.add(version)
    db.flush()  # persist version before ingest SyncJob FK

    for uid in grant_ids:
        db.add(
            DocumentGrant(
                id=uuid4(),
                tenant_id=conn.tenant_id,
                document_id=doc.id,
                user_id=uid,
            )
        )
    for gid in group_grant_ids:
        db.add(DocumentGroupGrant(id=uuid4(), tenant_id=conn.tenant_id, document_id=doc.id, group_id=gid))

    _enqueue_ingest_job(
        db=db,
        settings=settings,
        queue=queue,
        conn=conn,
        document_id=doc.id,
        version_id=version.id,
        external_id=external_id,
    )

    cfg = dict(conn.config or {})
    cursors = dict(cfg.get("file_cursors") or {})
    cursors[external_id] = modified
    cfg["file_cursors"] = cursors
    conn.config = cfg
    db.flush()
    return doc
