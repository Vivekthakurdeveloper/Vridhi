"""phase B documents, connections, jobs, chunks, embeddings

Revision ID: 0002_phase_b_documents
Revises: 0001_phase1_foundation
Create Date: 2026-09-07
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_phase_b_documents"
down_revision: Union[str, None] = "0001_phase1_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE document_visibility AS ENUM ('private', 'org', 'selected')"
    )
    op.execute(
        "CREATE TYPE document_status AS ENUM ('pending', 'processing', 'ready', 'failed', 'deleted')"
    )
    op.execute(
        "CREATE TYPE connection_status AS ENUM "
        "('available', 'connected', 'disconnected', 'syncing', 'sync_failed', 'not_configured')"
    )
    op.execute(
        "CREATE TYPE sync_job_status AS ENUM ('queued', 'running', 'succeeded', 'failed', 'dead')"
    )
    op.execute("CREATE TYPE sync_job_type AS ENUM ('ingest')")

    op.create_table(
        "connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("connector_type", sa.Text(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="connection_status", create_type=False),
            nullable=False,
            server_default="connected",
        ),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "connector_type", name="connections_tenant_type_uidx"),
    )
    op.create_index("connections_tenant_idx", "connections", ["tenant_id"])

    op.create_table(
        "documents",
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
            sa.ForeignKey("connections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default="file_upload"),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.Column(
            "visibility",
            postgresql.ENUM(name="document_visibility", create_type=False),
            nullable=False,
            server_default="private",
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="document_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "uploaded_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("documents_tenant_idx", "documents", ["tenant_id"])
    op.create_index("documents_tenant_status_idx", "documents", ["tenant_id", "status"])
    op.create_index("documents_uploaded_by_idx", "documents", ["uploaded_by_user_id"])

    op.create_table(
        "document_grants",
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
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("document_id", "user_id", name="document_grants_doc_user_uidx"),
    )
    op.create_index("document_grants_tenant_idx", "document_grants", ["tenant_id"])
    op.create_index("document_grants_user_idx", "document_grants", ["user_id"])

    op.create_table(
        "document_versions",
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
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checksum_sha256", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("document_id", "version_number", name="document_versions_doc_ver_uidx"),
    )
    op.create_index("document_versions_tenant_idx", "document_versions", ["tenant_id"])

    op.create_table(
        "chunks",
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
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("version_id", "chunk_index", name="chunks_version_index_uidx"),
    )
    op.create_index("chunks_tenant_idx", "chunks", ["tenant_id"])
    op.create_index("chunks_document_idx", "chunks", ["document_id"])

    op.create_table(
        "embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chunks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("opensearch_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("chunk_id", name="embeddings_chunk_uidx"),
    )
    op.create_index("embeddings_tenant_idx", "embeddings", ["tenant_id"])
    op.create_index("embeddings_document_idx", "embeddings", ["document_id"])

    op.create_table(
        "sync_jobs",
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
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "job_type",
            postgresql.ENUM(name="sync_job_type", create_type=False),
            nullable=False,
            server_default="ingest",
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="sync_job_status", create_type=False),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("sqs_message_id", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("sync_jobs_tenant_idx", "sync_jobs", ["tenant_id"])
    op.create_index("sync_jobs_document_idx", "sync_jobs", ["document_id"])
    op.create_index("sync_jobs_status_idx", "sync_jobs", ["status"])


def downgrade() -> None:
    op.drop_table("sync_jobs")
    op.drop_table("embeddings")
    op.drop_table("chunks")
    op.drop_table("document_versions")
    op.drop_table("document_grants")
    op.drop_table("documents")
    op.drop_table("connections")
    op.execute("DROP TYPE sync_job_type")
    op.execute("DROP TYPE sync_job_status")
    op.execute("DROP TYPE connection_status")
    op.execute("DROP TYPE document_status")
    op.execute("DROP TYPE document_visibility")
