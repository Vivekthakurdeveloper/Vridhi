"""phase E gmail connector

Gmail reuses the Phase-D schema as new rows: `connections` /
`connection_credentials` key off a free-text `connector_type`, and
`documents` already carries generic `source` / `external_id` columns
(with the partial unique index added in 0004). The only schema change
needed is a new `sync_jobs.job_type` enum value.

Revision ID: 0005_phase_e_gmail
Revises: 0004_phase_d_drive
Create Date: 2026-09-09
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0005_phase_e_gmail"
down_revision: Union[str, None] = "0004_phase_d_drive"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL 12+ allows ALTER TYPE ... ADD VALUE inside a transaction block
    # (Alembic wraps each migration in one), but the new value may not be USED
    # in that same transaction. This migration therefore only adds the label —
    # it must never insert or cast a 'gmail_sync' row. Same constraint as 0004.
    op.execute("ALTER TYPE sync_job_type ADD VALUE IF NOT EXISTS 'gmail_sync'")


def downgrade() -> None:
    # No-op by design: PostgreSQL cannot remove a value from an enum type.
    # Reverting would require recreating sync_job_type and rewriting every
    # dependent column, which is not worth it for an additive label.
    pass
