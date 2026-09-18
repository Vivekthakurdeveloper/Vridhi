from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.security import MemberRole, MemberStatus


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    organization_name: Optional[str] = Field(default=None, min_length=1, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10)
    password: str = Field(min_length=8, max_length=128)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=10)


class CreateOrganizationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class PatchOrganizationRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)


class InviteUserRequest(BaseModel):
    email: EmailStr
    role: MemberRole = MemberRole.member


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=10)
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)


class PatchUserRequest(BaseModel):
    role: Optional[MemberRole] = None
    status: Optional[MemberStatus] = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    name: str
    email_verified_at: Optional[datetime]
    status: str


class MembershipOut(BaseModel):
    tenant_id: UUID
    role: MemberRole
    status: MemberStatus
    organization_name: str
    organization_slug: str


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    created_at: datetime


class AuthSessionOut(BaseModel):
    user: UserOut
    membership: Optional[MembershipOut] = None


class MemberOut(BaseModel):
    id: UUID
    user_id: UUID
    email: EmailStr
    name: str
    role: MemberRole
    status: MemberStatus
    created_at: datetime


class InviteOut(BaseModel):
    id: UUID
    email: EmailStr
    role: MemberRole
    status: str
    expires_at: datetime
    created_at: datetime
    # Only populated in development when using the log email provider.
    debug_token: Optional[str] = None


ConnectorStatus = Literal[
    "available",
    "connected",
    "disconnected",
    "connecting",
    "syncing",
    "sync_failed",
    "auth_required",
    "not_configured",
    "not_implemented",
]


class ConnectorOut(BaseModel):
    id: str
    name: str
    description: str
    status: ConnectorStatus
    enabled: bool
    last_sync_at: Optional[datetime] = None
    document_count: Optional[int] = None
    failed_document_count: Optional[int] = None
    health: Optional[str] = None
    account_email: Optional[str] = None
    mode: Optional[str] = None


class ConnectorsResponse(BaseModel):
    connectors: list[ConnectorOut]


class DashboardActivityOut(BaseModel):
    id: UUID
    action: str
    created_at: datetime
    user_id: Optional[UUID] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DashboardOut(BaseModel):
    documents: int
    chunks: int
    connections_connected: int
    connections_available: int
    questions: int
    users: int
    last_sync_at: Optional[datetime] = None
    recent_activity: list[DashboardActivityOut]


class DocumentOut(BaseModel):
    id: UUID
    title: str
    mime_type: Optional[str] = None
    source: Optional[str] = None
    status: str
    visibility: str = "private"
    uploaded_by_user_id: Optional[UUID] = None
    job_id: Optional[UUID] = None
    job_status: Optional[str] = None
    error_message: Optional[str] = None
    byte_size: Optional[int] = None
    granted_user_ids: list[UUID] = Field(default_factory=list)
    granted_group_ids: list[UUID] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: datetime


class DocumentsResponse(BaseModel):
    items: list[DocumentOut]
    total: int
    page: int
    limit: int


class DocumentPreviewOut(BaseModel):
    document_id: UUID
    title: str
    status: str
    preview: str
    truncated: bool
    chunk_count: int


class SyncJobOut(BaseModel):
    id: UUID
    connection_id: Optional[UUID] = None
    document_id: Optional[UUID] = None
    version_id: Optional[UUID] = None
    job_type: str
    status: str
    attempt: int
    max_attempts: int
    progress_total: int = 0
    progress_done: int = 0
    progress_failed: int = 0
    progress_skipped: int = 0
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class UploadResponse(BaseModel):
    document: DocumentOut
    job: SyncJobOut


class AuditEventOut(BaseModel):
    id: UUID
    action: str
    user_id: Optional[UUID] = None
    request_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AuditResponse(BaseModel):
    items: list[AuditEventOut]
    total: int
    page: int
    limit: int


class FeaturesOut(BaseModel):
    google_login_enabled: bool
    google_drive_enabled: bool
    gmail_enabled: bool
    file_upload_enabled: bool
    ai_query_enabled: bool
    search_enabled: bool
    audit_enabled: bool
    usage_enabled: bool
    billing_enabled: bool
    sso_enabled: bool
    upload_max_bytes: Optional[int] = None
    upload_allowed_extensions: list[str] = Field(default_factory=list)
    chat_stream_enabled: bool = True
    llm_provider: Optional[str] = None


class UsageOut(BaseModel):
    available: bool
    message: Optional[str] = None
    users: Optional[int] = None
    questions: Optional[int] = None
    documents: Optional[int] = None
    storage_bytes: Optional[int] = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=10, ge=1, le=50)


class SearchHitOut(BaseModel):
    document_id: UUID
    chunk_id: UUID
    title: str
    preview: str
    score: float
    page: Optional[int] = None
    chunk_index: Optional[int] = None
    source_url: Optional[str] = None


class SearchResponse(BaseModel):
    query: str
    total: int
    results: list[SearchHitOut]


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    conversation_id: Optional[UUID] = None
    stream: bool = True


class CitationOut(BaseModel):
    id: UUID
    rank: int
    document_id: Optional[UUID] = None
    chunk_id: Optional[UUID] = None
    title: str
    passage: str
    page: Optional[int] = None
    chunk_index: Optional[int] = None
    score: Optional[float] = None
    source_url: Optional[str] = None


class MessageOut(BaseModel):
    id: UUID
    role: str
    content: str
    no_answer: bool = False
    model: Optional[str] = None
    created_at: datetime
    citations: list[CitationOut] = Field(default_factory=list)
    feedback: Optional[str] = None


class ConversationOut(BaseModel):
    id: UUID
    title: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    messages: list[MessageOut] = Field(default_factory=list)


class ChatResponse(BaseModel):
    conversation_id: UUID
    user_message: MessageOut
    assistant_message: MessageOut


class FeedbackRequest(BaseModel):
    rating: Literal["up", "down"]
    comment: Optional[str] = Field(default=None, max_length=2000)


class FeedbackOut(BaseModel):
    id: UUID
    message_id: UUID
    rating: str
    comment: Optional[str] = None
    created_at: datetime
    updated_at: datetime
