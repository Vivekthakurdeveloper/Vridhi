from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings
from app.errors import AppError
from app.models import (
    AuditEvent,
    Chunk,
    Connection,
    Document,
    DocumentGrant,
    DocumentGroupGrant,
    DocumentVersion,
    GroupMembership,
    OrganizationMember,
    SyncJob,
)
from app.security import (
    ConnectionStatus,
    DocumentStatus,
    DocumentVisibility,
    MemberRole,
    MemberStatus,
    SyncJobStatus,
    SyncJobType,
    role_at_least,
    utcnow,
)
from app.services.queue import IngestQueue
from app.services.storage import ObjectStorage
from app.services.tombstone import REASON_MANUAL, tombstone_document

logger = logging.getLogger(__name__)

MIME_BY_EXT = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "txt": "text/plain",
    "csv": "text/csv",
}


class DocumentService:
    def __init__(
        self,
        db: Session,
        settings: Settings,
        storage: ObjectStorage,
        queue: IngestQueue,
        search: Optional[Any] = None,
    ):
        self.db = db
        self.settings = settings
        self.storage = storage
        self.queue = queue
        self.search = search

    def require_upload_enabled(self) -> None:
        if not self.settings.file_upload_enabled:
            raise AppError("UPLOAD_NOT_AVAILABLE", "File upload isn't available yet.", 501)
        if not self.settings.file_upload_ready:
            raise AppError(
                "UPLOAD_NOT_CONFIGURED",
                "File upload isn't configured. Set S3 and SQS environment variables.",
                503,
            )

    def ensure_file_upload_connection(self, tenant_id: UUID) -> Connection:
        row = self.db.scalar(
            select(Connection).where(
                Connection.tenant_id == tenant_id,
                Connection.connector_type == "file_upload",
            )
        )
        if row:
            return row
        row = Connection(
            id=uuid4(),
            tenant_id=tenant_id,
            connector_type="file_upload",
            status=ConnectionStatus.connected,
            config={},
        )
        self.db.add(row)
        self.db.flush()
        return row

    def _validate_file(self, filename: str, content_type: Optional[str], size: int) -> tuple[str, str]:
        if size <= 0:
            raise AppError("EMPTY_FILE", "Uploaded file is empty.", 400)
        if size > self.settings.upload_max_bytes:
            raise AppError(
                "FILE_TOO_LARGE",
                f"File exceeds the {self.settings.upload_max_bytes} byte limit.",
                400,
            )
        ext = Path(filename).suffix.lstrip(".").lower()
        if ext not in self.settings.allowed_upload_extensions:
            raise AppError(
                "UNSUPPORTED_FILE_TYPE",
                f"File type '.{ext}' is not allowed.",
                400,
            )
        mime = (content_type or "").lower().split(";")[0].strip() or MIME_BY_EXT.get(ext, "application/octet-stream")
        if mime not in self.settings.allowed_upload_mime and mime != "application/octet-stream":
            # Allow octet-stream when extension is known; browsers often send this.
            if ext not in MIME_BY_EXT:
                raise AppError("UNSUPPORTED_MIME", f"MIME type '{mime}' is not allowed.", 400)
            mime = MIME_BY_EXT[ext]
        if mime == "application/octet-stream" and ext in MIME_BY_EXT:
            mime = MIME_BY_EXT[ext]
        return ext, mime

    def can_access(
        self,
        doc: Document,
        *,
        user_id: UUID,
        role: MemberRole,
        grant_user_ids: Optional[set[UUID]] = None,
        grant_group_ids: Optional[set[UUID]] = None,
        user_group_ids: Optional[set[UUID]] = None,
    ) -> bool:
        if doc.status == DocumentStatus.deleted or doc.deleted_at is not None:
            return False
        if role_at_least(role, MemberRole.admin):
            return True
        if doc.uploaded_by_user_id == user_id:
            return True
        if doc.visibility == DocumentVisibility.org:
            return True
        if doc.visibility == DocumentVisibility.private:
            return False
        if doc.visibility == DocumentVisibility.selected:
            if grant_user_ids is not None and user_id in grant_user_ids:
                return True
            if grant_group_ids is not None and user_group_ids is not None:
                if grant_group_ids & user_group_ids:
                    return True
                if grant_user_ids is not None:
                    return False  # both precomputed sets provided and neither matched
            granted = self.db.scalar(
                select(DocumentGrant.id).where(
                    DocumentGrant.document_id == doc.id,
                    DocumentGrant.user_id == user_id,
                )
            )
            if granted is not None:
                return True
            from app.services.groups import user_group_ids as fetch_user_group_ids

            uids = fetch_user_group_ids(self.db, user_id)
            group_granted = self.db.scalar(
                select(DocumentGroupGrant.id).where(
                    DocumentGroupGrant.document_id == doc.id,
                    DocumentGroupGrant.group_id.in_(uids) if uids else False,
                )
            )
            return group_granted is not None
        return False

    def accessible_filter(self, tenant_id: UUID, user_id: UUID, role: MemberRole):
        base = and_(
            Document.tenant_id == tenant_id,
            Document.deleted_at.is_(None),
            Document.status != DocumentStatus.deleted,
        )
        if role_at_least(role, MemberRole.admin):
            return base
        grant_exists = exists(
            select(DocumentGrant.id).where(
                DocumentGrant.document_id == Document.id,
                DocumentGrant.user_id == user_id,
            )
        )
        group_grant_exists = exists(
            select(DocumentGroupGrant.id)
            .join(GroupMembership, GroupMembership.group_id == DocumentGroupGrant.group_id)
            .where(
                DocumentGroupGrant.document_id == Document.id,
                GroupMembership.user_id == user_id,
            )
        )
        return and_(
            base,
            or_(
                Document.uploaded_by_user_id == user_id,
                Document.visibility == DocumentVisibility.org,
                and_(
                    Document.visibility == DocumentVisibility.selected,
                    or_(grant_exists, group_grant_exists),
                ),
            ),
        )

    def upload(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        filename: str,
        content_type: Optional[str],
        data: bytes,
        visibility: DocumentVisibility,
        selected_user_ids: list[UUID],
        request_id: Optional[str] = None,
    ) -> tuple[Document, SyncJob]:
        self.require_upload_enabled()
        _, mime = self._validate_file(filename, content_type, len(data))

        if visibility == DocumentVisibility.selected:
            if not selected_user_ids:
                raise AppError(
                    "SELECTED_USERS_REQUIRED",
                    "Select at least one user for selected visibility.",
                    400,
                )
            self._validate_selected_users(tenant_id, selected_user_ids)
        else:
            selected_user_ids = []

        connection = self.ensure_file_upload_connection(tenant_id)
        document_id = uuid4()
        version_id = uuid4()
        checksum = hashlib.sha256(data).hexdigest()
        storage_key = self.storage.object_key(
            str(tenant_id), str(document_id), str(version_id), filename
        )

        try:
            self.storage.ensure_bucket()
            self.storage.put_bytes(storage_key, data, content_type=mime)
        except Exception as exc:
            logger.exception("s3.upload_failed", extra={"operation": "document_upload"})
            raise AppError("STORAGE_ERROR", "Could not store the uploaded file.", 503) from exc

        title = Path(filename).stem or filename
        doc = Document(
            id=document_id,
            tenant_id=tenant_id,
            connection_id=connection.id,
            title=title,
            source="file_upload",
            mime_type=mime,
            visibility=visibility,
            status=DocumentStatus.pending,
            uploaded_by_user_id=user_id,
            current_version_id=version_id,
        )
        version = DocumentVersion(
            id=version_id,
            tenant_id=tenant_id,
            document_id=document_id,
            version_number=1,
            storage_key=storage_key,
            byte_size=len(data),
            checksum_sha256=checksum,
            mime_type=mime,
            original_filename=filename,
        )
        job = SyncJob(
            id=uuid4(),
            tenant_id=tenant_id,
            document_id=document_id,
            version_id=version_id,
            job_type=SyncJobType.ingest,
            status=SyncJobStatus.queued,
            max_attempts=self.settings.ingest_max_attempts,
        )
        self.db.add(doc)
        self.db.add(version)
        self.db.flush()
        self.db.add(job)
        for uid in set(selected_user_ids):
            self.db.add(
                DocumentGrant(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    document_id=document_id,
                    user_id=uid,
                )
            )
        self.db.flush()

        try:
            message_id = self.queue.enqueue_ingest(
                job_id=job.id,
                tenant_id=tenant_id,
                document_id=document_id,
                version_id=version_id,
            )
            job.sqs_message_id = message_id
        except Exception as exc:
            doc.status = DocumentStatus.failed
            doc.error_message = "Failed to enqueue ingest job."
            job.status = SyncJobStatus.failed
            job.error_message = str(exc)
            self.db.commit()
            logger.exception("sqs.enqueue_failed", extra={"operation": "document_upload"})
            raise AppError("QUEUE_ERROR", "Could not queue the document for processing.", 503) from exc

        self.db.add(
            AuditEvent(
                id=uuid4(),
                tenant_id=tenant_id,
                user_id=user_id,
                action="document.uploaded",
                metadata_={
                    "document_id": str(document_id),
                    "filename": filename,
                    "visibility": visibility.value,
                    "byte_size": len(data),
                },
                request_id=request_id,
            )
        )
        connection.last_sync_at = utcnow()
        self.db.commit()
        self.db.refresh(doc)
        self.db.refresh(job)
        return doc, job

    def _validate_selected_users(self, tenant_id: UUID, user_ids: list[UUID]) -> None:
        rows = self.db.scalars(
            select(OrganizationMember.user_id).where(
                OrganizationMember.tenant_id == tenant_id,
                OrganizationMember.user_id.in_(user_ids),
                OrganizationMember.status == MemberStatus.active,
            )
        ).all()
        found = set(rows)
        missing = [str(uid) for uid in user_ids if uid not in found]
        if missing:
            raise AppError(
                "INVALID_SELECTED_USERS",
                "One or more selected users are not active members of this workspace.",
                400,
            )

    def list_documents(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        role: MemberRole,
        page: int,
        limit: int,
    ) -> tuple[list[Document], int]:
        filt = self.accessible_filter(tenant_id, user_id, role)
        total = self.db.scalar(select(func.count()).select_from(Document).where(filt)) or 0
        rows = self.db.scalars(
            select(Document)
            .where(filt)
            .order_by(Document.updated_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
            .options(selectinload(Document.grants))
        ).all()
        return list(rows), int(total)

    def get_document(
        self,
        *,
        document_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
        role: MemberRole,
    ) -> Document:
        doc = self.db.scalar(
            select(Document)
            .where(Document.id == document_id, Document.tenant_id == tenant_id)
            .options(selectinload(Document.grants))
        )
        if not doc or not self.can_access(doc, user_id=user_id, role=role):
            raise AppError("DOCUMENT_NOT_FOUND", "Document not found.", 404)
        return doc

    def delete_document(
        self,
        *,
        document_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
        role: MemberRole,
        request_id: Optional[str] = None,
    ) -> None:
        doc = self.get_document(
            document_id=document_id, tenant_id=tenant_id, user_id=user_id, role=role
        )
        if doc.uploaded_by_user_id != user_id and not role_at_least(role, MemberRole.admin):
            raise AppError("FORBIDDEN", "You do not have permission to delete this document.", 403)
        if self.search is None:
            raise AppError(
                "SEARCH_CLEANUP_UNAVAILABLE",
                "Search cleanup isn't available, so the document was not deleted.",
                503,
            )
        tombstone_document(
            self.db,
            self.search,
            doc,
            reason=REASON_MANUAL,
            actor_user_id=user_id,
            request_id=request_id,
        )
        self.db.commit()

    def preview(
        self,
        *,
        document_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
        role: MemberRole,
    ) -> dict:
        doc = self.get_document(
            document_id=document_id, tenant_id=tenant_id, user_id=user_id, role=role
        )
        chunks = self.db.scalars(
            select(Chunk)
            .where(
                Chunk.document_id == doc.id,
                Chunk.tenant_id == tenant_id,
            )
            .order_by(Chunk.chunk_index.asc())
            .limit(20)
        ).all()
        text = "\n\n".join(c.content for c in chunks)
        max_chars = self.settings.document_preview_max_chars
        truncated = len(text) > max_chars
        return {
            "document_id": doc.id,
            "title": doc.title,
            "status": doc.status.value,
            "preview": text[:max_chars],
            "truncated": truncated,
            "chunk_count": len(chunks),
        }

    def get_job(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
        role: MemberRole,
    ) -> SyncJob:
        job = self.db.scalar(
            select(SyncJob).where(SyncJob.id == job_id, SyncJob.tenant_id == tenant_id)
        )
        if not job:
            raise AppError("JOB_NOT_FOUND", "Job not found.", 404)
        if job.document_id:
            self.get_document(
                document_id=job.document_id,
                tenant_id=tenant_id,
                user_id=user_id,
                role=role,
            )
        return job

    def latest_job_for_document(
        self,
        *,
        document_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
        role: MemberRole,
    ) -> Optional[SyncJob]:
        self.get_document(
            document_id=document_id, tenant_id=tenant_id, user_id=user_id, role=role
        )
        return self.db.scalar(
            select(SyncJob)
            .where(SyncJob.document_id == document_id, SyncJob.tenant_id == tenant_id)
            .order_by(SyncJob.created_at.desc())
            .limit(1)
        )

    def count_ready_documents(self, tenant_id: UUID) -> int:
        return int(
            self.db.scalar(
                select(func.count())
                .select_from(Document)
                .where(
                    Document.tenant_id == tenant_id,
                    Document.status == DocumentStatus.ready,
                    Document.deleted_at.is_(None),
                )
            )
            or 0
        )

    def count_chunks(self, tenant_id: UUID) -> int:
        return int(
            self.db.scalar(
                select(func.count()).select_from(Chunk).where(Chunk.tenant_id == tenant_id)
            )
            or 0
        )
