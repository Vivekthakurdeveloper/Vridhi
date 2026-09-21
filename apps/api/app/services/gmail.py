"""Gmail connector service (Phase E).

Mirrors `services/drive.py`, with two deliberate simplifications:

* No folder discovery — Gmail has no folder-picking step, so `start_sync` has
  no selection precondition.
* No permission mapping. A mailbox is personal, so every Gmail-sourced
  Document is unconditionally `DocumentVisibility.private` with zero
  DocumentGrant rows. Note that `private` still does not hide a document from
  org admins: `can_access` and the OpenSearch `acl_filter` both short-circuit
  on `role_at_least(role, admin)` before visibility is consulted.

`connections` is unique on (tenant_id, connector_type), so there is exactly one
Gmail connection per organization — a single designated mailbox, connected by
an admin. Per-user mailboxes would require changing that constraint.
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
from app.models import Connection, ConnectionCredential, Document, SyncJob
from app.security import (
    ConnectionHealth,
    ConnectionStatus,
    DocumentStatus,
    SyncJobStatus,
    SyncJobType,
    generate_token,
    utcnow,
)
from app.services import google_oauth
from app.services.queue import IngestQueue
from app.services.storage import ObjectStorage
from app.services.tokens import TokenStore

logger = logging.getLogger(__name__)

CONNECTOR_TYPE = "gmail"
MOCK_ACCESS_TOKEN = "mock-access-token"

# Fixture mailbox used when GMAIL_MODE=mock, shaped like real
# users.messages.get(format=full) responses. `_mock_content` is the only
# non-Gmail field; the worker reads it instead of calling attachments.get.
MOCK_MESSAGES: list[dict[str, Any]] = [
    {
        "id": "msg-invoice-001",
        "threadId": "thread-001",
        "internalDate": "1730457600000",
        "payload": {
            "mimeType": "multipart/mixed",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": "Invoice INV-2024-0912 for Aranya Foods"},
                {"name": "From", "value": "billing@vendor.example"},
            ],
            "body": {"size": 0},
            "parts": [
                {
                    "partId": "0",
                    "mimeType": "text/plain",
                    "filename": "",
                    "body": {"size": 58},
                },
                {
                    "partId": "1",
                    "mimeType": "text/plain",
                    "filename": "invoice-INV-2024-0912.txt",
                    "body": {"attachmentId": "att-invoice-001", "size": 214},
                    "_mock_content": (
                        "Invoice INV-2024-0912.\n"
                        "Billed to Aranya Foods Private Limited, Pune.\n"
                        "Line item: annual platform subscription, INR 24,999 per month "
                        "billed annually.\n"
                        "Payment terms are net 30 days from invoice date.\n"
                    ),
                },
            ],
        },
    },
    {
        # Nested multipart/alternative inside multipart/mixed — exercises the
        # recursive part walk in worker/gmail_sync.py.
        "id": "msg-policy-002",
        "threadId": "thread-002",
        "internalDate": "1731667200000",
        "payload": {
            "mimeType": "multipart/mixed",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": "Updated vendor onboarding checklist"},
                {"name": "From", "value": "ops@vendor.example"},
            ],
            "body": {"size": 0},
            "parts": [
                {
                    "partId": "0",
                    "mimeType": "multipart/alternative",
                    "filename": "",
                    "body": {"size": 0},
                    "parts": [
                        {
                            "partId": "0.0",
                            "mimeType": "text/plain",
                            "filename": "",
                            "body": {"size": 40},
                        },
                        {
                            "partId": "0.1",
                            "mimeType": "text/html",
                            "filename": "",
                            "body": {"size": 96},
                        },
                    ],
                },
                {
                    "partId": "1",
                    "mimeType": "text/csv",
                    "filename": "vendor-onboarding-checklist.csv",
                    "body": {"attachmentId": "att-policy-002", "size": 189},
                    "_mock_content": (
                        "step,owner,sla_days\n"
                        "Collect GST registration certificate,Finance,3\n"
                        "Verify bank account via penny drop,Finance,2\n"
                        "Sign mutual NDA before data sharing,Legal,5\n"
                        "Vridhi Phase E mock attachment marker.\n"
                    ),
                },
            ],
        },
    },
    {
        # Attachment MIME outside GMAIL_ALLOWED_MIME — must be skipped, not failed.
        "id": "msg-photo-003",
        "threadId": "thread-003",
        "internalDate": "1732012800000",
        "payload": {
            "mimeType": "multipart/mixed",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": "Warehouse photos"},
                {"name": "From", "value": "logistics@vendor.example"},
            ],
            "body": {"size": 0},
            "parts": [
                {
                    "partId": "0",
                    "mimeType": "image/png",
                    "filename": "warehouse-pune.png",
                    "body": {"attachmentId": "att-photo-003", "size": 12},
                    "_mock_content": "not-a-real-png",
                }
            ],
        },
    },
]

MOCK_HISTORY_ID = "1000"

# Test-only side channel (mock mode only), same idea as the Drive one in
# services/drive.py: scripts/smoke-phase-h.sh writes this file on the host and
# both bind-mounted containers see it. Keys (all optional):
#   removed_message_ids - messages that no longer exist (omitted from listings)
#   history             - Gmail-shaped history records for get_mock_history
#   history_id          - the mailbox's current historyId
_MOCK_OVERRIDES_PATH = Path(
    os.environ.get("GMAIL_MOCK_OVERRIDES_PATH")
    or (Path(__file__).resolve().parents[2] / ".mock-gmail-overrides.json")
)


def _read_mock_overrides() -> dict[str, Any]:
    try:
        return json.loads(_MOCK_OVERRIDES_PATH.read_text())
    except (FileNotFoundError, ValueError, OSError):
        return {}


def get_mock_messages() -> list[dict[str, Any]]:
    removed = set(_read_mock_overrides().get("removed_message_ids") or [])
    return [m for m in MOCK_MESSAGES if m["id"] not in removed]


def get_mock_history(start_history_id: str) -> tuple[list[dict[str, Any]], str]:
    """Mock ``users.history.list``: (records newer than the checkpoint, current historyId)."""
    overrides = _read_mock_overrides()
    current = str(overrides.get("history_id") or MOCK_HISTORY_ID)
    records = [
        r for r in (overrides.get("history") or []) if int(r["id"]) > int(start_history_id)
    ]
    return records, current


class GmailService:
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

    # --- readiness / lookup ---

    @property
    def is_mock(self) -> bool:
        return self.settings.gmail_mode.lower().strip() == "mock"

    def require_ready(self) -> None:
        if not self.settings.gmail_ready:
            raise AppError(
                "CONNECTOR_NOT_AVAILABLE",
                "Gmail isn't available yet.",
                501,
            )

    def get_connection(self, tenant_id: UUID) -> Optional[Connection]:
        return self.db.scalar(
            select(Connection)
            .where(
                Connection.tenant_id == tenant_id,
                Connection.connector_type == CONNECTOR_TYPE,
            )
            .options(selectinload(Connection.credentials))
        )

    def connection_detail(self, tenant_id: UUID) -> dict[str, Any]:
        conn = self.get_connection(tenant_id)
        if not conn:
            return {
                "connected": False,
                "status": "available" if self.settings.gmail_ready else "not_implemented",
                "health": None,
                "account_email": None,
                "last_sync_at": None,
                "last_error": None,
                "document_count": 0,
                "failed_document_count": 0,
                "mode": self.settings.gmail_mode,
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
            "mode": self.settings.gmail_mode,
            "connection_id": conn.id,
        }

    # --- OAuth ---

    def oauth_start_url(self, *, state: str) -> str:
        self.require_ready()
        if self.is_mock:
            base = self.settings.app_url.rstrip("/")
            return f"{base}/v1/connections/gmail/oauth/callback?code=mock&state={state}"
        return google_oauth.build_auth_url(
            client_id=self.settings.google_client_id,
            redirect_uri=self.settings.gmail_redirect_uri,
            scopes=self.settings.gmail_scope_list,
            state=state,
        )

    def complete_oauth(self, *, tenant_id: UUID, user_id: UUID, code: str) -> Connection:
        self.require_ready()
        if self.is_mock or code == "mock":
            return self._upsert_connection(
                tenant_id=tenant_id,
                user_id=user_id,
                account_email="mock-gmail@vridhi.local",
                refresh_token="mock-refresh-token",
                access_token=MOCK_ACCESS_TOKEN,
                expires_in=3600,
                scopes=" ".join(self.settings.gmail_scope_list),
            )

        token_data = google_oauth.exchange_code(
            code=code,
            client_id=self.settings.google_client_id,
            client_secret=self.settings.google_client_secret,
            redirect_uri=self.settings.gmail_redirect_uri,
        )
        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            # Google only issues a refresh token on the first grant for a scope
            # set. An existing Drive grant for this account does NOT cover
            # gmail.readonly, so this is the expected failure when a user
            # reconnects without revoking first.
            raise AppError(
                "GMAIL_TOKEN_MISSING",
                "Google did not return a refresh token. Disconnect the app in your "
                "Google Account and reconnect to grant Gmail access.",
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
            scopes=token_data.get("scope") or " ".join(self.settings.gmail_scope_list),
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
        conn.config = {**(conn.config or {}), "message_cursors": {}}
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

    # --- sync ---

    def start_sync(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        query: Optional[str] = None,
        incremental: bool = True,
        trigger: str = "manual",
    ) -> SyncJob:
        self.require_ready()
        conn = self.get_connection(tenant_id)
        if not conn or conn.status == ConnectionStatus.disconnected:
            raise AppError("GMAIL_NOT_CONNECTED", "Connect Gmail first.", 400)

        conn.status = ConnectionStatus.syncing
        conn.health = ConnectionHealth.healthy
        conn.last_error = None

        job = SyncJob(
            id=uuid4(),
            tenant_id=tenant_id,
            connection_id=conn.id,
            job_type=SyncJobType.gmail_sync,
            status=SyncJobStatus.queued,
            max_attempts=self.settings.ingest_max_attempts,
            payload={
                "query": query or self.settings.gmail_query,
                "incremental": incremental,
                "trigger": trigger,
                "requested_by": str(user_id),
            },
        )
        self.db.add(job)
        # Commit BEFORE enqueuing. An SQS message is visible to the worker the
        # instant it is sent, so enqueuing inside the transaction races: a
        # worker sitting in receive_message can pick up the id, find no
        # committed row, and drop the message — leaving the job stuck in
        # `queued` forever. Durable first, then announce.
        self.db.commit()

        try:
            message_id = self.queue.enqueue_job(
                job_id=job.id,
                tenant_id=tenant_id,
                job_type="gmail_sync",
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
            raise AppError("QUEUE_ERROR", "Could not queue Gmail sync.", 503) from exc

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
                    SyncJob.job_type == SyncJobType.gmail_sync,
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


def new_oauth_state() -> str:
    return generate_token(24)
