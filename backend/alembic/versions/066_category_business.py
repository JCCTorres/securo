"""Explicit default-off Business category read-model policy.

Revision ID: 066
Revises: 065
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "066"
down_revision: Union[str, None] = "065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("categories", sa.Column("is_business", sa.Boolean(),
                                        nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("categories", "is_business")
