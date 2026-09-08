from __future__ import annotations

import enum
import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timezone

from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class MemberRole(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    member = "member"


class MemberStatus(str, enum.Enum):
    active = "active"
    deactivated = "deactivated"


class UserStatus(str, enum.Enum):
    active = "active"
    deactivated = "deactivated"


class InviteStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    revoked = "revoked"
    expired = "expired"


class OAuthProvider(str, enum.Enum):
    google = "google"


class DocumentVisibility(str, enum.Enum):
    private = "private"
    org = "org"
    selected = "selected"


class DocumentStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    ready = "ready"
    failed = "failed"
    deleted = "deleted"


class ConnectionStatus(str, enum.Enum):
    available = "available"
    connected = "connected"
    disconnected = "disconnected"
    syncing = "syncing"
    sync_failed = "sync_failed"
    not_configured = "not_configured"


class SyncJobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    dead = "dead"


class SyncJobType(str, enum.Enum):
    ingest = "ingest"
    drive_sync = "drive_sync"


class ConnectionHealth(str, enum.Enum):
    healthy = "healthy"
    degraded = "degraded"
    error = "error"
    unknown = "unknown"


class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"
    system = "system"


class FeedbackRating(str, enum.Enum):
    up = "up"
    down = "down"


ROLE_RANK = {
    MemberRole.member: 1,
    MemberRole.admin: 2,
    MemberRole.owner: 3,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def generate_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    return pwd_context.verify(password, password_hash)


def slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")[:80] or "organization"


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def role_at_least(role: MemberRole, minimum: MemberRole) -> bool:
    return ROLE_RANK[role] >= ROLE_RANK[minimum]
