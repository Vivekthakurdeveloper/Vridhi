"""Enterprise Google Workspace auth foundation (Phase F).

Domain-Wide Delegation lets a per-tenant service account impersonate any
employee in a Google Workspace domain. This module owns exactly one
capability: given a tenant and an employee's email, return a working
Google API access token for that employee. It does not decide what to do
with that token — later sub-projects (a sync engine, the Chat connector)
consume this module, they do not extend it.

Deliberately separate from `google_oauth.py`, which does human-consent
authorization-code flows. Domain-Wide Delegation is server-to-server: no
consent screen, no redirect URI, just a service-account key and Google's
own `google-auth` library.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.errors import AppError

logger = logging.getLogger(__name__)

REQUIRED_KEY_FIELDS = ("type", "project_id", "private_key", "client_email", "client_id")


def validate_service_account_key(raw: str) -> dict[str, Any]:
    """Parse and sanity-check a service-account JSON key.

    Raises AppError(INVALID_SERVICE_ACCOUNT_KEY) rather than a bare
    exception so the router can return a clean 400 with a message the
    admin can act on, instead of a stack trace.
    """
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise AppError(
            "INVALID_SERVICE_ACCOUNT_KEY",
            "This doesn't look like a valid service account key — make sure "
            "you copied the entire JSON file.",
            400,
        ) from exc

    if not isinstance(parsed, dict):
        raise AppError(
            "INVALID_SERVICE_ACCOUNT_KEY",
            "This doesn't look like a valid service account key — make sure "
            "you copied the entire JSON file.",
            400,
        )

    missing = [f for f in REQUIRED_KEY_FIELDS if not parsed.get(f)]
    if missing or parsed.get("type") != "service_account":
        raise AppError(
            "INVALID_SERVICE_ACCOUNT_KEY",
            "This doesn't look like a valid service account key — make sure "
            "you copied the entire JSON file.",
            400,
        )

    return parsed


def translate_workspace_enterprise_error(raw_message: str, *, client_id: str | None = None) -> str:
    """Map a known Google Domain-Wide Delegation failure signature to an
    actionable message. Falls back to the raw message for anything not
    recognized — never invent a misleading explanation for an error we
    don't actually recognize.
    """
    lowered = raw_message.lower()

    if "unauthorized_client" in lowered or "not authorized for any of the scopes" in lowered:
        cid = client_id or "<shown on the setup page>"
        return (
            "Domain-Wide Delegation isn't authorized for this service account yet. "
            "In Google Admin Console → Security → API Controls → "
            f"Domain-wide Delegation, authorize Client ID {cid} for scope "
            "admin.directory.user.readonly."
        )

    if "invalid_grant" in lowered and ("invalid email" in lowered or "user id" in lowered):
        return (
            "Couldn't act as that user — check this is an active user in "
            "your Google Workspace."
        )

    if "403" in lowered and ("forbidden" in lowered or "not authorized" in lowered):
        return (
            "This account doesn't have Super Admin privileges. Directory access "
            "requires impersonating a Super Admin — use a different admin "
            "account for verification."
        )

    return raw_message
