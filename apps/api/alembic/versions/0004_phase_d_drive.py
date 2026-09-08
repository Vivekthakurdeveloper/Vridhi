"""phase D google drive connections, credentials, sync progress

Revision ID: 0004_phase_d_drive
Revises: 0003_phase_c_rag
Create Date: 2026-09-08
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_phase_d_drive"
down_revision: Union[str, None] = "0003_phase_c_rag"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE TYPE connection_health AS ENUM ('healthy', 'degraded', 'error', 'unknown')")
    op.execute("ALTER TYPE sync_job_type ADD VALUE IF NOT EXISTS 'drive_sync'")

    op.add_column(
        "connections",
        sa.Column(
            "health",
            postgresql.ENUM(name="connection_health", create_type=False),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column(
        "connections",
        sa.Column(
            "connected_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("connections", sa.Column("account_email", sa.Text(), nullable=True))
    op.add_column("connections", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("connections", sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("documents", sa.Column("external_id", sa.Text(), nullable=True))
    op.create_index("documents_external_id_idx", "documents", ["tenant_id", "source", "external_id"])
    # Partial unique: only when external_id is present
    op.execute(
        "CREATE UNIQUE INDEX documents_tenant_source_external_uidx "
        "ON documents (tenant_id, source, external_id) "
        "WHERE external_id IS NOT NULL"
    )

    op.add_column(
        "sync_jobs",
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("connections.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "sync_jobs", sa.Column("progress_total", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "sync_jobs", sa.Column("progress_done", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "sync_jobs", sa.Column("progress_failed", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "sync_jobs", sa.Column("progress_skipped", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "sync_jobs",
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("sync_jobs_connection_idx", "sync_jobs", ["connection_id"])

    op.create_table(
        "connection_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("encrypted_access_token", sa.Text(), nullable=True),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column("token_backend", sa.Text(), nullable=False, server_default="fernet"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("connection_id", name="connection_credentials_connection_uidx"),
    )


def downgrade() -> None:
    op.drop_table("connection_credentials")
    op.drop_index("sync_jobs_connection_idx", table_name="sync_jobs")
    op.drop_column("sync_jobs", "payload")
    op.drop_column("sync_jobs", "progress_skipped")
    op.drop_column("sync_jobs", "progress_failed")
    op.drop_column("sync_jobs", "progress_done")
    op.drop_column("sync_jobs", "progress_total")
    op.drop_column("sync_jobs", "connection_id")
    op.execute("DROP INDEX IF EXISTS documents_tenant_source_external_uidx")
    op.drop_index("documents_external_id_idx", table_name="documents")
    op.drop_column("documents", "external_id")
    op.drop_column("connections", "last_error_at")
    op.drop_column("connections", "last_error")
    op.drop_column("connections", "account_email")
    op.drop_column("connections", "connected_by_user_id")
    op.drop_column("connections", "health")
    op.execute("DROP TYPE connection_health")
