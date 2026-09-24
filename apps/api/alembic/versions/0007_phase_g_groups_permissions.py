"""phase G real permission model + google groups resolution

Revision ID: 0007_groups_permissions
Revises: 0006_workspace_enterprise
Create Date: 2026-09-18
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_groups_permissions"
down_revision: Union[str, None] = "0006_workspace_enterprise"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workspace_enterprise_connections",
        sa.Column("groups_last_synced_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("google_group_id", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "google_group_id", name="groups_tenant_google_id_uidx"),
    )
    op.create_index("groups_tenant_idx", "groups", ["tenant_id"])

    op.create_table(
        "group_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("group_id", "user_id", name="group_memberships_group_user_uidx"),
    )
    op.create_index("group_memberships_tenant_idx", "group_memberships", ["tenant_id"])
    op.create_index("group_memberships_user_idx", "group_memberships", ["user_id"])

    op.create_table(
        "document_group_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("document_id", "group_id", name="document_group_grants_doc_group_uidx"),
    )
    op.create_index("document_group_grants_tenant_idx", "document_group_grants", ["tenant_id"])
    op.create_index("document_group_grants_group_idx", "document_group_grants", ["group_id"])


def downgrade() -> None:
    op.drop_table("document_group_grants")
    op.drop_table("group_memberships")
    op.drop_index("groups_tenant_idx", table_name="groups")
    op.drop_table("groups")
    op.drop_column("workspace_enterprise_connections", "groups_last_synced_at")
