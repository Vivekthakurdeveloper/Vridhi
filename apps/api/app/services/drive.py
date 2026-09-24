from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings
from app.errors import AppError
from app.models import (
    Connection,
    ConnectionCredential,
    Document,
    Group,
    OrganizationMember,
    SyncJob,
    User,
)
from app.security import (
    ConnectionHealth,
    ConnectionStatus,
    DocumentStatus,
    DocumentVisibility,
    MemberStatus,
    SyncJobStatus,
    SyncJobType,
    generate_token,
    utcnow,
)
from app.services.queue import IngestQueue
from app.services.storage import ObjectStorage
from app.services.tokens import TokenStore

logger = logging.getLogger(__name__)

MOCK_FOLDERS = [
    {"id": "folder-contracts", "name": "Contracts", "path": "My Drive / Contracts"},
    {"id": "folder-hr", "name": "HR Policies", "path": "My Drive / HR Policies"},
    {"id": "folder-finance", "name": "Finance", "path": "My Drive / Finance"},
]

MOCK_FILES = {
    "folder-contracts": [
        {
            "id": "file-msa-2024",
            "name": "Master Service Agreement 2024.txt",
            "mimeType": "text/plain",
            "webViewLink": "https://drive.google.com/file/d/file-msa-2024/view",
            "modifiedTime": "2024-11-01T10:00:00Z",
            "permissions": [{"type": "domain", "role": "reader"}, {"type": "user", "emailAddress": "owner@example.com", "role": "owner"}],
            "content": (
                "Master Service Agreement 2024.\n"
                "Vendor contracts renew automatically unless cancelled 30 days prior.\n"
                "Liability cap is INR 50,00,000 per incident.\n"
            ),
        }
    ],
    "folder-hr": [
        {
            "id": "file-leave-policy",
            "name": "Leave Policy.txt",
            "mimeType": "text/plain",
            "webViewLink": "https://drive.google.com/file/d/file-leave-policy/view",
            "modifiedTime": "2024-08-15T08:00:00Z",
            "permissions": [{"type": "domain", "role": "reader"}],
            "content": (
                "HR Leave Policy.\n"
                "Employees receive 18 days of paid leave per calendar year.\n"
                "Leave requests require manager approval within 3 business days.\n"
            ),
        }
    ],
    "folder-finance": [
        {
            "id": "file-gst-sop",
            "name": "GST Invoice SOP.txt",
            "mimeType": "text/plain",
            "webViewLink": "https://drive.google.com/file/d/file-gst-sop/view",
            "modifiedTime": "2025-01-10T12:00:00Z",
            "permissions": [
                {"type": "user", "emailAddress": "owner@example.com", "role": "owner"},
                {"type": "user", "emailAddress": "finance@example.com", "role": "reader"},
            ],
            "content": (
                "Finance SOP.\n"
                "GST invoice must be issued within 7 days of payment receipt.\n"
                "Professional plan is priced at INR 24,999 per month billed annually.\n"
            ),
        },
        {
            "id": "file-finance-group-shared",
            "name": "Finance Group Shared Report.txt",
            "mimeType": "text/plain",
            "webViewLink": "https://drive.google.com/file/d/file-finance-group-shared/view",
            "modifiedTime": "2025-02-01T09:00:00Z",
            # Shared with the "finance@acme.com" Google Group (see
            # services/groups.py MOCK_GROUPS) rather than an individual user or
            # the whole domain. This is the fixture the Phase G smoke test
            # (scripts/smoke-phase-g.sh) uses to prove group-based access: a
            # group member should see this file once Groups sync resolves the
            # membership, and lose access again once this permission is
            # revoked and Drive re-syncs -- even though the file content and
            # modifiedTime never change. See get_mock_files() below for how
            # the smoke test mutates this between two sync calls.
            "permissions": [
                {"type": "user", "emailAddress": "owner@example.com", "role": "owner"},
                {"type": "group", "emailAddress": "finance@acme.com", "role": "reader"},
            ],
            "content": (
                "Finance Group Shared Report.\n"
                "Visible to the Finance Google Group only.\n"
            ),
        },
    ],
}


# --- Test-only mock permission overrides ------------------------------------
#
# The Phase G smoke test needs to mutate a mock file's Drive `permissions`
# between two sync calls in the same run (e.g. revoke a group share, then
# re-sync) to prove worker/drive_sync.py's fail-closed re-resolution path
# actually revokes access. The api and worker run as separate processes
# (separate containers in docker-compose), so an in-memory mutation from one
# wouldn't be visible to the other -- but docker-compose bind-mounts this same
# `apps/api` source tree into both, so a small JSON file living next to this
# module is a cheap, host-writable side channel between an external test
# script and both processes, without a real API endpoint or IPC mechanism.
#
# This is consulted ONLY by get_mock_files(), which is itself only ever
# called from the mock-mode fixture branch in
# worker/drive_sync.py::_list_files_for_folders (already gated on
# `settings.google_drive_mode == "mock"`). It has no effect on, and is never
# read from, the real Google Drive API code path. If the file is absent (the
# default for anyone not running this smoke test), MOCK_FILES is served
# as-is.
_MOCK_OVERRIDES_PATH = Path(
    os.environ.get("DRIVE_MOCK_OVERRIDES_PATH")
    or (Path(__file__).resolve().parents[2] / ".mock-drive-overrides.json")
)


def get_mock_files(folder_id: str) -> list[dict[str, Any]]:
    """Mock-mode file listing for `folder_id`, with any test-only permission
    overrides from `_MOCK_OVERRIDES_PATH` applied on top of MOCK_FILES."""
    files = [dict(f) for f in MOCK_FILES.get(folder_id) or []]
    overrides = _read_mock_overrides().get("permissions") or {}
    for f in files:
        if f["id"] in overrides:
            f["permissions"] = overrides[f["id"]]
    return files


def _read_mock_overrides() -> dict[str, Any]:
    try:
        return json.loads(_MOCK_OVERRIDES_PATH.read_text())
    except (FileNotFoundError, ValueError, OSError):
        return {}


@dataclass
class DriveFolder:
    id: str
    name: str
    path: str


class DriveService:
    def __init__(
        self,
        db: Session,
        settings: Settings,
        tokens: TokenStore,
        storage: ObjectStorage,
        queue: IngestQueue,
    ):
        self.db = db
        self.settings = settings
        self.tokens = tokens
        self.storage = storage
        self.queue = queue

    def require_ready(self) -> None:
        if not self.settings.google_drive_ready:
            raise AppError(
                "CONNECTOR_NOT_AVAILABLE",
                "Google Drive isn't available yet.",
                501,
            )

    def get_connection(self, tenant_id: UUID) -> Optional[Connection]:
        return self.db.scalar(
            select(Connection)
            .where(
                Connection.tenant_id == tenant_id,
                Connection.connector_type == "google_drive",
            )
            .options(selectinload(Connection.credentials))
        )

    def connection_detail(self, tenant_id: UUID) -> dict[str, Any]:
        conn = self.get_connection(tenant_id)
        if not conn:
            return {
                "connected": False,
                "status": "available" if self.settings.google_drive_ready else "not_implemented",
                "health": None,
                "account_email": None,
                "last_sync_at": None,
                "last_error": None,
                "document_count": 0,
                "failed_document_count": 0,
                "selected_folder_ids": [],
                "mode": self.settings.google_drive_mode,
            }
        docs = int(
            self.db.scalar(
                select(func.count())
                .select_from(Document)
                .where(
                    Document.tenant_id == tenant_id,
                    Document.connection_id == conn.id,
                    Document.deleted_at.is_(None),
                )
            )
            or 0
        )
        failed = int(
            self.db.scalar(
                select(func.count())
                .select_from(Document)
                .where(
                    Document.tenant_id == tenant_id,
                    Document.connection_id == conn.id,
                    Document.status == DocumentStatus.failed,
                    Document.deleted_at.is_(None),
                )
            )
            or 0
        )
        return {
            "connected": conn.status
            in {ConnectionStatus.connected, ConnectionStatus.syncing, ConnectionStatus.sync_failed},
            "status": conn.status.value,
            "health": conn.health.value if conn.health else "unknown",
            "account_email": conn.account_email,
            "last_sync_at": conn.last_sync_at,
            "last_error": conn.last_error,
            "document_count": docs,
            "failed_document_count": failed,
            "selected_folder_ids": list((conn.config or {}).get("selected_folder_ids") or []),
            "mode": self.settings.google_drive_mode,
            "connection_id": conn.id,
        }

    # --- OAuth ---

    def oauth_start_url(self, *, tenant_id: UUID, user_id: UUID, state: str) -> str:
        self.require_ready()
        if self.settings.google_drive_mode.lower() == "mock":
            return f"{self.settings.app_url.rstrip('/')}/v1/connections/google_drive/oauth/callback?code=mock&state={state}"
        params = {
            "client_id": self.settings.google_client_id,
            "redirect_uri": self.settings.google_drive_redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.settings.drive_scope_list),
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": state,
        }
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)

    def complete_oauth(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        code: str,
    ) -> Connection:
        self.require_ready()
        if self.settings.google_drive_mode.lower() == "mock" or code == "mock":
            return self._upsert_connection(
                tenant_id=tenant_id,
                user_id=user_id,
                account_email="mock-drive@vridhi.local",
                refresh_token="mock-refresh-token",
                access_token="mock-access-token",
                expires_in=3600,
                scopes=" ".join(self.settings.drive_scope_list),
            )

        token_resp = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret,
                "redirect_uri": self.settings.google_drive_redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=30.0,
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()
        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            raise AppError(
                "DRIVE_TOKEN_MISSING",
                "Google did not return a refresh token. Disconnect the app in Google Account and reconnect.",
                400,
            )
        email = self._fetch_user_email(access_token)
        return self._upsert_connection(
            tenant_id=tenant_id,
            user_id=user_id,
            account_email=email,
            refresh_token=refresh_token,
            access_token=access_token,
            expires_in=int(token_data.get("expires_in") or 3600),
            scopes=token_data.get("scope") or " ".join(self.settings.drive_scope_list),
        )

    def disconnect(self, *, tenant_id: UUID, user_id: UUID) -> None:
        conn = self.get_connection(tenant_id)
        if not conn:
            return
        creds = conn.credentials
        if creds:
            self.db.delete(creds)
        conn.status = ConnectionStatus.disconnected
        conn.health = ConnectionHealth.unknown
        conn.account_email = None
        conn.config = {**(conn.config or {}), "selected_folder_ids": [], "page_token": None}
        conn.updated_at = utcnow()
        self.db.commit()

    def _upsert_connection(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        account_email: str,
        refresh_token: str,
        access_token: str,
        expires_in: int,
        scopes: str,
    ) -> Connection:
        conn = self.get_connection(tenant_id)
        if not conn:
            conn = Connection(
                id=uuid4(),
                tenant_id=tenant_id,
                connector_type="google_drive",
                status=ConnectionStatus.connected,
                health=ConnectionHealth.healthy,
                config={},
            )
            self.db.add(conn)
            self.db.flush()
        conn.status = ConnectionStatus.connected
        conn.health = ConnectionHealth.healthy
        conn.connected_by_user_id = user_id
        conn.account_email = account_email
        conn.last_error = None
        conn.last_error_at = None
        conn.updated_at = utcnow()

        creds = conn.credentials or ConnectionCredential(
            id=uuid4(),
            tenant_id=tenant_id,
            connection_id=conn.id,
        )
        creds.encrypted_refresh_token = self.tokens.encrypt(refresh_token)
        creds.encrypted_access_token = self.tokens.encrypt(access_token)
        creds.access_token_expires_at = utcnow() + timedelta(seconds=max(expires_in - 60, 60))
        creds.scopes = scopes
        creds.token_backend = self.settings.token_backend
        if not conn.credentials:
            self.db.add(creds)
        self.db.commit()
        self.db.refresh(conn)
        return conn

    def _fetch_user_email(self, access_token: str) -> str:
        resp = httpx.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20.0,
        )
        resp.raise_for_status()
        return str(resp.json().get("email") or "unknown@google")

    # --- Folders ---

    def list_folders(self, *, tenant_id: UUID) -> list[DriveFolder]:
        self.require_ready()
        conn = self.get_connection(tenant_id)
        if not conn or conn.status == ConnectionStatus.disconnected:
            raise AppError("DRIVE_NOT_CONNECTED", "Connect Google Drive first.", 400)
        if self.settings.google_drive_mode.lower() == "mock":
            return [DriveFolder(**f) for f in MOCK_FOLDERS]
        access = self._access_token(conn)
        folders: list[DriveFolder] = []
        page_token = None
        while True:
            params: dict[str, Any] = {
                "q": "mimeType='application/vnd.google-apps.folder' and trashed=false",
                "spaces": "drive",
                "fields": "nextPageToken, files(id, name, parents)",
                "pageSize": self.settings.drive_sync_page_size,
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
                folders.append(
                    DriveFolder(id=f["id"], name=f.get("name") or "Untitled", path=f.get("name") or "Untitled")
                )
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return folders

    def save_selected_folders(self, *, tenant_id: UUID, folder_ids: list[str]) -> Connection:
        conn = self.get_connection(tenant_id)
        if not conn:
            raise AppError("DRIVE_NOT_CONNECTED", "Connect Google Drive first.", 400)
        known = {f.id for f in self.list_folders(tenant_id=tenant_id)}
        invalid = [fid for fid in folder_ids if fid not in known]
        if invalid and self.settings.google_drive_mode.lower() != "mock":
            # Real Drive: allow any folder id returned by API; if empty known list, accept selected
            pass
        if self.settings.google_drive_mode.lower() == "mock":
            invalid = [fid for fid in folder_ids if fid not in {f["id"] for f in MOCK_FOLDERS}]
            if invalid:
                raise AppError("INVALID_FOLDER", f"Unknown folder ids: {', '.join(invalid)}", 400)
        cfg = dict(conn.config or {})
        cfg["selected_folder_ids"] = list(dict.fromkeys(folder_ids))
        conn.config = cfg
        conn.updated_at = utcnow()
        self.db.commit()
        self.db.refresh(conn)
        return conn

    # --- Sync ---

    def start_sync(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        folder_ids: Optional[list[str]] = None,
        visibility: DocumentVisibility = DocumentVisibility.org,
        selected_user_ids: Optional[list[UUID]] = None,
        incremental: bool = True,
    ) -> SyncJob:
        self.require_ready()
        conn = self.get_connection(tenant_id)
        if not conn or conn.status == ConnectionStatus.disconnected:
            raise AppError("DRIVE_NOT_CONNECTED", "Connect Google Drive first.", 400)

        selected = folder_ids or list((conn.config or {}).get("selected_folder_ids") or [])
        if not selected:
            raise AppError("FOLDERS_REQUIRED", "Select at least one Drive folder to sync.", 400)

        if visibility == DocumentVisibility.selected and not selected_user_ids:
            raise AppError(
                "SELECTED_USERS_REQUIRED",
                "Select at least one user for selected visibility.",
                400,
            )

        cfg = dict(conn.config or {})
        cfg["selected_folder_ids"] = selected
        cfg["default_visibility"] = visibility.value
        if selected_user_ids:
            cfg["selected_user_ids"] = [str(u) for u in selected_user_ids]
        conn.config = cfg
        conn.status = ConnectionStatus.syncing
        conn.health = ConnectionHealth.healthy
        conn.last_error = None

        job = SyncJob(
            id=uuid4(),
            tenant_id=tenant_id,
            connection_id=conn.id,
            job_type=SyncJobType.drive_sync,
            status=SyncJobStatus.queued,
            max_attempts=self.settings.ingest_max_attempts,
            payload={
                "folder_ids": selected,
                "visibility": visibility.value,
                "selected_user_ids": [str(u) for u in (selected_user_ids or [])],
                "incremental": incremental,
                "requested_by": str(user_id),
                "page_token": (conn.config or {}).get("page_token") if incremental else None,
            },
        )
        self.db.add(job)
        # Commit (making the job row durably visible to other DB sessions)
        # BEFORE publishing to the queue. Publishing first and committing
        # after is a classic race: the worker runs in a separate process
        # with its own DB session/connection, and a fast worker can receive
        # and look up the job before this transaction lands, find nothing,
        # and silently ack the message -- leaving the job stuck at "queued"
        # forever with no error anywhere. This was observed directly against
        # the mock stack (worker consumed and deleted the SQS message inside
        # single-digit milliseconds of it being sent, well before this
        # session's commit had a chance to complete).
        self.db.commit()
        self.db.refresh(job)
        try:
            message_id = self.queue.enqueue_job(
                job_id=job.id,
                tenant_id=tenant_id,
                job_type="drive_sync",
                document_id=None,
                version_id=None,
            )
            job.sqs_message_id = message_id
            self.db.commit()
        except Exception as exc:
            job.status = SyncJobStatus.failed
            job.error_message = str(exc)
            conn.status = ConnectionStatus.sync_failed
            conn.health = ConnectionHealth.error
            conn.last_error = str(exc)
            conn.last_error_at = utcnow()
            self.db.commit()
            raise AppError("QUEUE_ERROR", "Could not queue Drive sync.", 503) from exc

        self.db.refresh(job)
        return job

    def list_sync_history(self, *, tenant_id: UUID, limit: int = 20) -> list[SyncJob]:
        conn = self.get_connection(tenant_id)
        if not conn:
            return []
        return list(
            self.db.scalars(
                select(SyncJob)
                .where(
                    SyncJob.tenant_id == tenant_id,
                    SyncJob.connection_id == conn.id,
                    SyncJob.job_type == SyncJobType.drive_sync,
                )
                .order_by(SyncJob.created_at.desc())
                .limit(limit)
            ).all()
        )

    def list_failed_documents(self, *, tenant_id: UUID, limit: int = 50) -> list[Document]:
        conn = self.get_connection(tenant_id)
        if not conn:
            return []
        return list(
            self.db.scalars(
                select(Document)
                .where(
                    Document.tenant_id == tenant_id,
                    Document.connection_id == conn.id,
                    Document.status == DocumentStatus.failed,
                    Document.deleted_at.is_(None),
                )
                .order_by(Document.updated_at.desc())
                .limit(limit)
            ).all()
        )

    def get_sync_job(self, *, tenant_id: UUID, job_id: UUID) -> SyncJob:
        job = self.db.scalar(
            select(SyncJob).where(SyncJob.id == job_id, SyncJob.tenant_id == tenant_id)
        )
        if not job:
            raise AppError("JOB_NOT_FOUND", "Sync job not found.", 404)
        return job

    # --- Worker-facing helpers ---

    def _access_token(self, conn: Connection) -> str:
        creds = conn.credentials
        if not creds or not creds.encrypted_access_token:
            raise RuntimeError("Missing Drive credentials")
        if (
            creds.access_token_expires_at
            and creds.access_token_expires_at > utcnow() + timedelta(seconds=30)
        ):
            return self.tokens.decrypt(creds.encrypted_access_token)
        if self.settings.google_drive_mode.lower() == "mock":
            return "mock-access-token"
        if not creds.encrypted_refresh_token:
            raise RuntimeError("Missing refresh token")
        refresh = self.tokens.decrypt(creds.encrypted_refresh_token)
        resp = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret,
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        access = data["access_token"]
        creds.encrypted_access_token = self.tokens.encrypt(access)
        creds.access_token_expires_at = utcnow() + timedelta(seconds=int(data.get("expires_in") or 3600) - 60)
        self.db.commit()
        return access

    def map_permissions_to_acl(
        self,
        *,
        tenant_id: UUID,
        permissions: list[dict[str, Any]],
        default_visibility: DocumentVisibility,
        selected_user_ids: list[UUID],
    ) -> tuple[DocumentVisibility, list[UUID], list[UUID]]:
        """
        Conservative fail-closed mapping:
        - domain/anyone/org-wide Drive share -> org (only if default allows org)
        - user shares -> selected grants for matched active members only
        - group shares -> selected grants for the matched local Group (if known)
        - unmatched emails/groups are skipped (never widen access)
        - if no mappable grants and not domain -> private

        Returns (visibility, user_grant_ids, group_grant_ids).
        """
        if default_visibility == DocumentVisibility.private:
            return DocumentVisibility.private, [], []
        if default_visibility == DocumentVisibility.selected:
            return DocumentVisibility.selected, selected_user_ids, []

        # default org -- still fail-closed if Drive file is only shared to specific users/groups
        has_domain = any(
            p.get("type") in {"domain", "anyone"} and p.get("role") in {"reader", "commenter", "writer", "owner"}
            for p in permissions
        )
        if has_domain:
            return DocumentVisibility.org, [], []

        emails = [
            str(p.get("emailAddress") or "").lower()
            for p in permissions
            if p.get("type") == "user" and p.get("emailAddress")
        ]
        group_emails = [
            str(p.get("emailAddress") or "").lower()
            for p in permissions
            if p.get("type") == "group" and p.get("emailAddress")
        ]

        user_ids = list(
            self.db.execute(
                select(User.id)
                .join(OrganizationMember, OrganizationMember.user_id == User.id)
                .where(
                    OrganizationMember.tenant_id == tenant_id,
                    OrganizationMember.status == MemberStatus.active,
                    func.lower(User.email).in_(emails),
                )
            )
            .scalars()
            .all()
        ) if emails else []

        group_ids = list(
            self.db.execute(
                select(Group.id).where(
                    Group.tenant_id == tenant_id,
                    func.lower(Group.email).in_(group_emails),
                )
            )
            .scalars()
            .all()
        ) if group_emails else []

        if not user_ids and not group_ids:
            # No discoverable/resolvable sharing metadata -> keep private
            return DocumentVisibility.private, [], []
        return DocumentVisibility.selected, user_ids, group_ids


def new_oauth_state() -> str:
    return generate_token(24)
