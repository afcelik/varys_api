"""create schedule_blocks table

Revision ID: 6c3499a5c0a0
Revises: 9571058e7c90
Create Date: 2026-11-20 22:28:14.161249

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6c3499a5c0a0'
down_revision: str | Sequence[str] | None = '9571058e7c90'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # New table only, so no batch mode is needed. recurrence_type uses the same non-native
    # enum storage as routines. The unrelated daily_plans plan_date unique-constraint drift
    # reported by autogenerate is intentionally left out.
    op.create_table(
        'schedule_blocks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('description', sa.String(length=2000), nullable=True),
        sa.Column('start_time', sa.Time(), nullable=False),
        sa.Column('end_time', sa.Time(), nullable=False),
        sa.Column(
            'recurrence_type',
            sa.Enum(
                'daily', 'weekly', 'monthly', 'interval',
                name='recurrencetype', native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column('days_of_week', sa.JSON(), nullable=True),
        sa.Column('day_of_month', sa.Integer(), nullable=True),
        sa.Column('interval_days', sa.Integer(), nullable=True),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False,
        ),
        sa.CheckConstraint('end_time > start_time', name='ck_schedule_blocks_end_after_start'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('schedule_blocks')
