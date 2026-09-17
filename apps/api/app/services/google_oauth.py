"""Shared Google OAuth helpers for connectors.

Phase D (`services/drive.py`) grew its own copies of the authorization-URL,
code-exchange and refresh logic, and `worker/drive_sync.py` reaches across the
module boundary into the private `DriveService._access_token`. This module
factors that logic out so Phase E (Gmail) does not repeat it.

Additive only: `DriveService` deliberately still uses its own copies. Migrating
Phase D onto these helpers is a separate, independently verifiable change.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.models import Connection
from app.security import utcnow
from app.services.tokens import TokenStore

logger = logging.getLogger(__name__)

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo"


def build_auth_url(
    *,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    state: str,
) -> str:
    """Authorization-code URL with offline access and a forced consent screen.

    `prompt=consent` matters: Google only returns a refresh token on the first
    grant for a scope set, so a re-connect after a partial grant would otherwise
    silently yield credentials that cannot be refreshed.
    """
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"


def exchange_code(
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    resp = httpx.post(
        GOOGLE_TOKEN_ENDPOINT,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(
    *,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    resp = httpx.post(
        GOOGLE_TOKEN_ENDPOINT,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_userinfo_email(access_token: str, *, timeout: float = 20.0) -> str:
    resp = httpx.get(
        GOOGLE_USERINFO_ENDPOINT,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return str(resp.json().get("email") or "unknown@google")


def valid_access_token(
    db: Session,
    conn: Connection,
    tokens: TokenStore,
    *,
    client_id: str,
    client_secret: str,
    mock_value: Optional[str] = None,
) -> str:
    """Return a usable access token, refreshing and re-encrypting if stale.

    `mock_value`, when supplied, short-circuits the refresh for mock-mode
    connectors that never talk to Google.
    """
    creds = conn.credentials
    if not creds or not creds.encrypted_access_token:
        raise RuntimeError(f"Missing {conn.connector_type} credentials")

    if (
        creds.access_token_expires_at
        and creds.access_token_expires_at > utcnow() + timedelta(seconds=30)
    ):
        return tokens.decrypt(creds.encrypted_access_token)

    if mock_value is not None:
        return mock_value

    if not creds.encrypted_refresh_token:
        raise RuntimeError(f"Missing {conn.connector_type} refresh token")

    data = refresh_access_token(
        refresh_token=tokens.decrypt(creds.encrypted_refresh_token),
        client_id=client_id,
        client_secret=client_secret,
    )
    access = data["access_token"]
    creds.encrypted_access_token = tokens.encrypt(access)
    creds.access_token_expires_at = utcnow() + timedelta(
        seconds=int(data.get("expires_in") or 3600) - 60
    )
    db.commit()
    return access
