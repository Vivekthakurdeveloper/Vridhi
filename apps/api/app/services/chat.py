"""Google Chat connector service (Piece 4B).

Mirrors `services/gmail.py`'s shape, with two differences:

* A space picker, like Drive's folder picker (`services/drive.py`), because
  unlike a mailbox a Chat connection can see many spaces and nothing should
  sync until an admin ticks it.
* Real permissions: unlike Gmail (always private), each thread's visibility
  is `selected`, with one DocumentGrant per resolved space member,
  re-checked on every sync (worker/chat_sync.py owns that re-check; this
  service only exposes member lookup for it).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings
from app.errors import AppError
from app.models import Connection, ConnectionCredential, Document, OrganizationMember, SyncJob, User
from app.security import (
    ConnectionHealth,
    ConnectionStatus,
    DocumentStatus,
    MemberStatus,
    SyncJobStatus,
    SyncJobType,
    generate_token,
    utcnow,
)
from app.services import autosync, google_oauth
from app.services.queue import IngestQueue
from app.services.storage import ObjectStorage
from app.services.tokens import TokenStore

logger = logging.getLogger(__name__)

CONNECTOR_TYPE = "google_chat"
MOCK_ACCESS_TOKEN = "mock-chat-access-token"
CHAT_API_BASE = "https://chat.googleapis.com/v1"

MOCK_SPACES: list[dict[str, Any]] = [
    {"name": "spaces/mockspace-finance", "displayName": "Finance"},
    {"name": "spaces/mockspace-eng", "displayName": "Engineering"},
]

# Same fixed test-org email Groups' mock fixture uses (see services/groups.py),
# so a fresh test org's second invited member resolves to a real grant.
MOCK_MEMBERS: dict[str, list[str]] = {
    "spaces/mockspace-finance": ["finance-member@example.com"],
    "spaces/mockspace-eng": [],
}

MOCK_MESSAGES: list[dict[str, Any]] = [
    {
        "name": "msg-budget-001",
        "createTime": "2026-09-20T10:00:00Z",
        "thread": {"name": "spaces/mockspace-finance/threads/thread-budget"},
        "text": "Kicking off the Q4 budget review — first draft numbers attached.",
        "sender": {"name": "users/mock-1", "displayName": "Priya Shah"},
        "attachment": [
            {
                "name": "spaces/mockspace-finance/messages/msg-budget-001/attachments/1",
                "contentName": "q4-budget-draft.csv",
                "contentType": "text/csv",
                "downloadUri": "mock://q4-budget-draft.csv",
            }
        ],
    },
    {
        "name": "msg-budget-002",
        "createTime": "2026-09-20T10:05:00Z",
        "thread": {"name": "spaces/mockspace-finance/threads/thread-budget"},
        "text": "Looks good, one line item needs a correction in row 12.",
        "sender": {"name": "users/mock-2", "displayName": "Ravi Kumar"},
    },
    {
        "name": "msg-standup-001",
        "createTime": "2026-09-21T09:00:00Z",
        "thread": {"name": "spaces/mockspace-eng/threads/thread-standup"},
        "text": "Daily standup: shipping the Chat connector today.",
        "sender": {"name": "users/mock-3", "displayName": "Dev Bot"},
    },
]

# Mock-mode-only side channel, same mechanism as services/drive.py and
# services/gmail.py: scripts/smoke-phase-j.sh writes this file on the host
# and both bind-mounted containers see it. Keys (all optional):
#   removed_message_ids - messages that no longer exist (omitted from listings)
_MOCK_OVERRIDES_PATH = Path(
    os.environ.get("CHAT_MOCK_OVERRIDES_PATH")
    or (Path(__file__).resolve().parents[2] / ".mock-chat-overrides.json")
)


def _read_mock_overrides() -> dict[str, Any]:
    try:
        return json.loads(_MOCK_OVERRIDES_PATH.read_text())
    except (FileNotFoundError, ValueError, OSError):
        return {}


def get_mock_spaces() -> list[dict[str, Any]]:
    return list(MOCK_SPACES)


def get_mock_members(space_name: str) -> list[str]:
    return list(MOCK_MEMBERS.get(space_name, []))


def get_mock_messages() -> list[dict[str, Any]]:
    removed = set(_read_mock_overrides().get("removed_message_ids") or [])
    return [m for m in MOCK_MESSAGES if m["name"] not in removed]


def new_oauth_state() -> str:
    return generate_token(24)


def resolve_member_grants(db: Session, tenant_id: UUID, member_emails: list[str]) -> list[UUID]:
    """Fail-closed email -> active org member resolution, same query shape as
    `drive.py:map_permissions_to_acl` and `groups.py:sync_groups_and_memberships`."""
    emails = {e.lower() for e in member_emails if e}
    if not emails:
        return []
    return list(
        db.execute(
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
    )


class ChatService:
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

    @property
    def is_mock(self) -> bool:
        return self.settings.google_chat_mode.lower().strip() == "mock"

    def require_ready(self) -> None:
        if not self.settings.google_chat_ready:
            raise AppError("CONNECTOR_NOT_AVAILABLE", "Google Chat isn't available yet.", 501)

    def get_connection(self, tenant_id: UUID) -> Optional[Connection]:
        return self.db.scalar(
            select(Connection)
            .where(Connection.tenant_id == tenant_id, Connection.connector_type == CONNECTOR_TYPE)
            .options(selectinload(Connection.credentials))
        )

    def connection_detail(self, tenant_id: UUID) -> dict[str, Any]:
        conn = self.get_connection(tenant_id)
        if not conn:
            return {
                "connected": False,
                "status": "available" if self.settings.google_chat_ready else "not_implemented",
                "health": None,
                "account_email": None,
                "last_sync_at": None,
                "last_error": None,
                "document_count": 0,
                "failed_document_count": 0,
                "mode": self.settings.google_chat_mode,
                "auto_sync_enabled": None,
                "auto_sync_paused_reason": None,
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
            "mode": self.settings.google_chat_mode,
            "connection_id": conn.id,
            "selected_space_ids": list((conn.config or {}).get("selected_space_ids") or []),
            "auto_sync_enabled": autosync.is_enabled(conn.config),
            "auto_sync_paused_reason": autosync.paused_reason(conn.config),
        }

    def set_auto_sync(self, *, tenant_id: UUID, user_id: UUID, enabled: bool) -> None:
        conn = self.get_connection(tenant_id)
        if not conn or conn.status == ConnectionStatus.disconnected:
            raise AppError("CHAT_NOT_CONNECTED", "Connect Google Chat first.", 400)
        autosync.set_auto_sync(self.db, conn, enabled=enabled, actor_user_id=user_id)

    # --- OAuth ---

    def oauth_start_url(self, *, state: str) -> str:
        self.require_ready()
        if self.is_mock:
            base = self.settings.app_url.rstrip("/")
            return f"{base}/v1/connections/google_chat/oauth/callback?code=mock&state={state}"
        return google_oauth.build_auth_url(
            client_id=self.settings.google_client_id,
            redirect_uri=self.settings.google_chat_redirect_uri,
            scopes=self.settings.chat_scope_list,
            state=state,
        )

    def complete_oauth(self, *, tenant_id: UUID, user_id: UUID, code: str) -> Connection:
        self.require_ready()
        if self.is_mock or code == "mock":
            return self._upsert_connection(
                tenant_id=tenant_id,
                user_id=user_id,
                account_email="mock-chat@vridhi.local",
                refresh_token="mock-refresh-token",
                access_token=MOCK_ACCESS_TOKEN,
                expires_in=3600,
                scopes=" ".join(self.settings.chat_scope_list),
            )
        token_data = google_oauth.exchange_code(
            code=code,
            client_id=self.settings.google_client_id,
            client_secret=self.settings.google_client_secret,
            redirect_uri=self.settings.google_chat_redirect_uri,
        )
        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            raise AppError(
                "CHAT_TOKEN_MISSING",
                "Google did not return a refresh token. Disconnect the app in your "
                "Google Account and reconnect to grant Chat access.",
                400,
            )
        email = google_oauth.fetch_userinfo_email(access_token)
        return self._upsert_connection(
            tenant_id=tenant_id,
            user_id=user_id,
            account_email=email,
            refresh_token=refresh_token,
            access_token=access_token,
            expires_in=int(token_data.get("expires_in") or 3600),
            scopes=token_data.get("scope") or " ".join(self.settings.chat_scope_list),
        )

    def disconnect(self, *, tenant_id: UUID, user_id: UUID) -> None:
        _ = user_id
        conn = self.get_connection(tenant_id)
        if not conn:
            return
        creds = conn.credentials
        if creds:
            self.db.delete(creds)
        conn.status = ConnectionStatus.disconnected
        conn.health = ConnectionHealth.unknown
        conn.account_email = None
        conn.config = {**(conn.config or {}), "selected_space_ids": [], "chat_cursors": {}}
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
                connector_type=CONNECTOR_TYPE,
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

        creds = conn.credentials or ConnectionCredential(id=uuid4(), tenant_id=tenant_id, connection_id=conn.id)
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

    # --- Space picker ---

    def list_spaces(self, *, tenant_id: UUID) -> list[dict[str, str]]:
        self.require_ready()
        conn = self.get_connection(tenant_id)
        if not conn or conn.status == ConnectionStatus.disconnected:
            raise AppError("CHAT_NOT_CONNECTED", "Connect Google Chat first.", 400)
        if self.is_mock:
            return [{"id": s["name"], "name": s["displayName"]} for s in get_mock_spaces()]
        # This runs inside the api process (every Connections-page load and
        # every space-save), which never has apps/worker on its PYTHONPATH
        # (only apps/api is copied into the api image -- see
        # apps/api/Dockerfile). worker.google_api's shared retry helper is
        # therefore not importable here, unlike the worker-only sync path
        # (chat_sync.py), which does use it. Plain httpx, matching how
        # DriveService.list_folders (the api-side equivalent for Drive)
        # already calls Google directly from this same process.
        import httpx

        access = self.access_token(conn)
        spaces: list[dict[str, str]] = []
        page_token = None
        while True:
            params: dict[str, Any] = {"pageSize": self.settings.chat_sync_page_size}
            if page_token:
                params["pageToken"] = page_token
            resp = httpx.get(
                f"{CHAT_API_BASE}/spaces",
                params=params,
                headers={"Authorization": f"Bearer {access}"},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            for s in data.get("spaces") or []:
                spaces.append({"id": s["name"], "name": s.get("displayName") or s["name"]})
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        logger.info(
            "chat.spaces_listed",
            extra={"operation": "chat_list_spaces", "tenant_id": str(tenant_id)},
        )
        return spaces

    def save_selected_spaces(self, *, tenant_id: UUID, space_ids: list[str]) -> Connection:
        conn = self.get_connection(tenant_id)
        if not conn:
            raise AppError("CHAT_NOT_CONNECTED", "Connect Google Chat first.", 400)
        known = {s["id"] for s in self.list_spaces(tenant_id=tenant_id)}
        invalid = [sid for sid in space_ids if sid not in known]
        if invalid:
            raise AppError("INVALID_SPACE", f"Unknown space ids: {', '.join(invalid)}", 400)
        cfg = dict(conn.config or {})
        cfg["selected_space_ids"] = list(dict.fromkeys(space_ids))
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
        space_ids: Optional[list[str]] = None,
        incremental: bool = True,
        trigger: str = "manual",
    ) -> SyncJob:
        self.require_ready()
        conn = self.get_connection(tenant_id)
        if not conn or conn.status == ConnectionStatus.disconnected:
            raise AppError("CHAT_NOT_CONNECTED", "Connect Google Chat first.", 400)

        selected = space_ids or list((conn.config or {}).get("selected_space_ids") or [])
        if not selected:
            raise AppError("SPACES_REQUIRED", "Select at least one Chat space to sync.", 400)

        cfg = dict(conn.config or {})
        cfg["selected_space_ids"] = selected
        conn.config = cfg
        conn.status = ConnectionStatus.syncing
        conn.health = ConnectionHealth.healthy
        conn.last_error = None

        job = SyncJob(
            id=uuid4(),
            tenant_id=tenant_id,
            connection_id=conn.id,
            job_type=SyncJobType.chat_sync,
            status=SyncJobStatus.queued,
            max_attempts=self.settings.ingest_max_attempts,
            payload={
                "space_ids": selected,
                "incremental": incremental,
                "trigger": trigger,
                "requested_by": str(user_id),
            },
        )
        self.db.add(job)
        # Commit before enqueuing -- same race avoided in drive.py/gmail.py's
        # start_sync (a fast worker can otherwise receive the SQS message
        # before this transaction lands and drop it).
        self.db.commit()
        self.db.refresh(job)
        try:
            message_id = self.queue.enqueue_job(
                job_id=job.id, tenant_id=tenant_id, job_type="chat_sync", document_id=None, version_id=None
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
            raise AppError("QUEUE_ERROR", "Could not queue Chat sync.", 503) from exc
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
                    SyncJob.job_type == SyncJobType.chat_sync,
                )
                .order_by(SyncJob.created_at.desc())
                .limit(limit)
            ).all()
        )

    # --- worker-facing ---

    def access_token(self, conn: Connection) -> str:
        return google_oauth.valid_access_token(
            self.db,
            conn,
            self.tokens,
            client_id=self.settings.google_client_id,
            client_secret=self.settings.google_client_secret,
            mock_value=MOCK_ACCESS_TOKEN if self.is_mock else None,
        )
