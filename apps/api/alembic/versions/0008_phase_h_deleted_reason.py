"""phase H documents.deleted_reason

Revision ID: 0008_deleted_reason
Revises: 0007_groups_permissions
Create Date: 2026-09-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_deleted_reason"
down_revision: Union[str, None] = "0007_groups_permissions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("deleted_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "deleted_reason")
