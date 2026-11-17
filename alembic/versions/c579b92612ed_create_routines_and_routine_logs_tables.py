"""create routines and routine_logs tables

Revision ID: c579b92612ed
Revises: 5f49e6ec74e2
Create Date: 2026-11-17 18:14:30.894208

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c579b92612ed'
down_revision: str | Sequence[str] | None = '5f49e6ec74e2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'routines',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('description', sa.String(length=2000), nullable=True),
        sa.Column(
            'recurrence_type',
            sa.Enum('daily', 'weekly', 'monthly', 'interval', name='recurrencetype', native_enum=False),
            nullable=False,
        ),
        sa.Column('days_of_week', sa.JSON(), nullable=True),
        sa.Column('day_of_month', sa.Integer(), nullable=True),
        sa.Column('interval_days', sa.Integer(), nullable=True),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'routine_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('routine_id', sa.Integer(), nullable=False),
        sa.Column('log_date', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['routine_id'], ['routines.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('routine_id', 'log_date', name='uq_routine_logs_routine_id_log_date'),
    )
    op.create_index(op.f('ix_routine_logs_routine_id'), 'routine_logs', ['routine_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_routine_logs_routine_id'), table_name='routine_logs')
    op.drop_table('routine_logs')
    op.drop_table('routines')
