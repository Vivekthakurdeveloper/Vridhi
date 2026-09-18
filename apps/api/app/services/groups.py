"""Google Groups sync (Phase G).

Owns exactly one capability beyond the freshness check: keep this
tenant's Groups and group memberships reasonably fresh, and let callers
ask "what groups is this user in." Fail-closed throughout -- any error
here means "grant nothing extra," never "grant something wrong."

Group membership changes far less often than document content, so this
refreshes at most once per 15 minutes per tenant rather than on every
sync, matching the same rate-limit lesson learned from Gmail sync.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Group, GroupMembership, OrganizationMember, User, WorkspaceEnterpriseConnection
from app.security import MemberStatus, WorkspaceEnterpriseStatus, utcnow
from app.services.workspace_enterprise import get_admin_impersonated_token

logger = logging.getLogger(__name__)

ADMIN_API_BASE = "https://admin.googleapis.com/admin/directory/v1"
GROUPS_SCOPES = [
    "https://www.googleapis.com/auth/admin.directory.group.readonly",
    "https://www.googleapis.com/auth/admin.directory.group.member.readonly",
]
_FRESHNESS_WINDOW = timedelta(minutes=15)

# Mock mode fixture: 2 groups, matching the same fixture org members Drive's
# own MOCK_FILES already assumes exist in a freshly-registered test org.
MOCK_GROUPS: list[dict[str, Any]] = [
    {"id": "mockgroup-finance", "email": "finance@acme.com", "name": "Finance"},
    {"id": "mockgroup-eng", "email": "eng@acme.com", "name": "Engineering"},
]
# member emails per mock group id -- resolved against whatever active org
# members actually exist at sync time, same fail-closed match as Drive.
# "finance-member@example.com" is the fixed email scripts/smoke-phase-g.sh
# always invites as its second org member, so that group resolution actually
# produces a real GroupMembership row (and, in turn, group-based document
# access) in mock mode rather than an empty match.
MOCK_GROUP_MEMBERS: dict[str, list[str]] = {
    "mockgroup-finance": ["finance-member@example.com"],
    "mockgroup-eng": [],
}


def _needs_refresh(last_synced_at: Optional[datetime], *, now: Optional[datetime] = None) -> bool:
    if last_synced_at is None:
        return True
    return (now or utcnow()) - last_synced_at > _FRESHNESS_WINDOW


def sync_groups_and_memberships(db: Session, tenant_id: UUID, *, is_mock: bool) -> None:
    conn = db.scalar(
        select(WorkspaceEnterpriseConnection).where(
            WorkspaceEnterpriseConnection.tenant_id == tenant_id,
            WorkspaceEnterpriseConnection.status == WorkspaceEnterpriseStatus.verified,
        )
    )
    if not conn:
        logger.info("groups.no_verified_connection", extra={"operation": "groups_sync"})
        return
    if not _needs_refresh(conn.groups_last_synced_at):
        return

    try:
        if is_mock:
            groups_data = MOCK_GROUPS
            members_by_group = MOCK_GROUP_MEMBERS
        else:
            groups_data, members_by_group = _fetch_groups_and_members(db, tenant_id)
    except Exception:
        logger.warning("groups.sync_failed", extra={"operation": "groups_sync"})
        return  # fail-closed: leave groups_last_synced_at untouched, retry next sync

    for g in groups_data:
        group_row = db.scalar(
            select(Group).where(Group.tenant_id == tenant_id, Group.google_group_id == g["id"])
        )
        if not group_row:
            group_row = Group(id=uuid4(), tenant_id=tenant_id, google_group_id=g["id"], email=g["email"], name=g.get("name"))
            db.add(group_row)
            db.flush()
        else:
            group_row.email = g["email"]
            group_row.name = g.get("name")

        member_emails = {e.lower() for e in members_by_group.get(g["id"], [])}
        matched_user_ids = set(
            db.execute(
                select(User.id)
                .join(OrganizationMember, OrganizationMember.user_id == User.id)
                .where(
                    OrganizationMember.tenant_id == tenant_id,
                    OrganizationMember.status == MemberStatus.active,
                    func.lower(User.email).in_(member_emails),
                )
            )
            .scalars()
            .all()
        ) if member_emails else set()

        existing_memberships = db.scalars(
            select(GroupMembership).where(GroupMembership.group_id == group_row.id)
        ).all()
        existing_user_ids = {m.user_id for m in existing_memberships}
        for m in existing_memberships:
            if m.user_id not in matched_user_ids:
                db.delete(m)
        for uid in matched_user_ids - existing_user_ids:
            db.add(GroupMembership(id=uuid4(), tenant_id=tenant_id, group_id=group_row.id, user_id=uid))

    conn.groups_last_synced_at = utcnow()
    db.commit()


def _fetch_groups_and_members(db: Session, tenant_id: UUID) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    from worker.google_api import google_get_with_retry

    token = get_admin_impersonated_token(db, tenant_id, GROUPS_SCOPES)
    headers = {"Authorization": f"Bearer {token}"}
    domain = db.scalar(
        select(WorkspaceEnterpriseConnection.google_domain).where(
            WorkspaceEnterpriseConnection.tenant_id == tenant_id
        )
    )

    groups_data: list[dict[str, Any]] = []
    page_token = None
    while True:
        params: dict[str, Any] = {"domain": domain, "maxResults": 200}
        if page_token:
            params["pageToken"] = page_token
        resp = google_get_with_retry(f"{ADMIN_API_BASE}/groups", params=params, headers=headers, timeout=30.0)
        data = resp.json()
        for g in data.get("groups") or []:
            groups_data.append({"id": g["id"], "email": g["email"], "name": g.get("name")})
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    members_by_group: dict[str, list[str]] = {}
    for g in groups_data:
        emails: list[str] = []
        member_page_token = None
        while True:
            params = {"maxResults": 200}
            if member_page_token:
                params["pageToken"] = member_page_token
            resp = google_get_with_retry(
                f"{ADMIN_API_BASE}/groups/{g['id']}/members", params=params, headers=headers, timeout=30.0
            )
            data = resp.json()
            emails.extend(m.get("email", "") for m in (data.get("members") or []) if m.get("email"))
            member_page_token = data.get("nextPageToken")
            if not member_page_token:
                break
        members_by_group[g["id"]] = emails

    return groups_data, members_by_group


def user_group_ids(db: Session, user_id: UUID) -> set[UUID]:
    return set(
        db.execute(select(GroupMembership.group_id).where(GroupMembership.user_id == user_id)).scalars().all()
    )
