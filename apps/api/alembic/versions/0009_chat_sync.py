"""piece 4B chat connector

Google Chat reuses the existing Connection/Document/DocumentGrant schema
exactly like Gmail did in 0005. The only schema change is a new
sync_jobs.job_type enum value.

Revision ID: 0009_chat_sync
Revises: 0008_deleted_reason
Create Date: 2026-09-22
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0009_chat_sync"
down_revision: Union[str, None] = "0008_deleted_reason"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Same constraint as 0005/0007: only add the label in this transaction,
    # never use it in the same migration.
    op.execute("ALTER TYPE sync_job_type ADD VALUE IF NOT EXISTS 'chat_sync'")


def downgrade() -> None:
    # No-op by design: PostgreSQL cannot remove an enum value.
    pass
