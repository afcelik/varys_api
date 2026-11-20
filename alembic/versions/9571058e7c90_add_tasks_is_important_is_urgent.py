"""add tasks is_important is_urgent

Revision ID: 9571058e7c90
Revises: 7c140291163a
Create Date: 2026-11-20 19:15:19.917174

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9571058e7c90'
down_revision: str | Sequence[str] | None = '7c140291163a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Batch mode (same pattern as 8b2febf88e21). server_default '0' fills existing rows
    # with false/false. alembic/env.py does not enable PRAGMA foreign_keys, so recreating
    # tasks does not fire task_events' ON DELETE CASCADE. The unrelated daily_plans
    # plan_date unique-constraint drift reported by autogenerate is intentionally left out.
    with op.batch_alter_table('tasks') as batch_op:
        batch_op.add_column(
            sa.Column('is_important', sa.Boolean(), server_default=sa.text('0'), nullable=False)
        )
        batch_op.add_column(
            sa.Column('is_urgent', sa.Boolean(), server_default=sa.text('0'), nullable=False)
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('tasks') as batch_op:
        batch_op.drop_column('is_urgent')
        batch_op.drop_column('is_important')
