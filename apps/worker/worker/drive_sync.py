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
from app.services.drive import MOCK_FILES, DriveService
from app.services.queue import IngestQueue, get_ingest_queue
from app.services.storage import ObjectStorage, get_object_storage
from app.services.tokens import TokenStore, get_token_store

logger = logging.getLogger(__name__)


def process_drive_sync_job(db: Session, *, job_id: UUID) -> None:
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
        job_id=job_id,
    )


def _run_drive_sync(
    db: Session,
    *,
    settings: Settings,
    storage: ObjectStorage,
    queue: IngestQueue,
    tokens: TokenStore,
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


def _list_files_for_folders(
    drive: DriveService,
    settings: Settings,
    conn: Connection,
    folder_ids: list[str],
) -> list[dict[str, Any]]:
    if settings.google_drive_mode.lower() == "mock":
        out: list[dict[str, Any]] = []
        for fid in folder_ids:
            out.extend(MOCK_FILES.get(fid) or [])
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
    existing = db.scalar(
        select(Document)
        .where(
            Document.tenant_id == conn.tenant_id,
            Document.source == "google_drive",
            Document.external_id == external_id,
            Document.deleted_at.is_(None),
        )
        .options(selectinload(Document.grants))
    )
    prev_modified = ((conn.config or {}).get("file_cursors") or {}).get(external_id)
    if existing and prev_modified and prev_modified == modified and existing.status == DocumentStatus.ready:
        return None

    data, mime = _download_file_bytes(drive, settings, conn, file_meta)
    visibility, grant_ids = drive.map_permissions_to_acl(
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

    ingest_job = SyncJob(
        id=uuid4(),
        tenant_id=conn.tenant_id,
        connection_id=conn.id,
        document_id=doc.id,
        version_id=version.id,
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
        document_id=doc.id,
        version_id=version.id,
    )
    ingest_job.sqs_message_id = msg_id

    cfg = dict(conn.config or {})
    cursors = dict(cfg.get("file_cursors") or {})
    cursors[external_id] = modified
    cfg["file_cursors"] = cursors
    conn.config = cfg
    db.flush()
    return doc
