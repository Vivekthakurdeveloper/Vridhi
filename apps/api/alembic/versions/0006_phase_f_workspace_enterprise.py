"""phase F enterprise google workspace auth foundation (domain-wide delegation)

Revision ID: 0006_workspace_enterprise
Revises: 0005_phase_e_gmail
Create Date: 2026-09-15
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_workspace_enterprise"
down_revision: Union[str, None] = "0005_phase_e_gmail"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE workspace_enterprise_status AS ENUM "
        "('pending_verification', 'verified', 'error', 'disabled')"
    )
    op.create_table(
        "workspace_enterprise_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("google_domain", sa.Text(), nullable=False),
        sa.Column("service_account_email", sa.Text(), nullable=False),
        sa.Column("encrypted_key", sa.Text(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="workspace_enterprise_status", create_type=False),
            nullable=False,
            server_default="pending_verification",
        ),
        sa.Column("verified_scopes", sa.Text(), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", name="workspace_enterprise_connections_tenant_uidx"),
    )


def downgrade() -> None:
    op.drop_table("workspace_enterprise_connections")
    op.execute("DROP TYPE workspace_enterprise_status")
