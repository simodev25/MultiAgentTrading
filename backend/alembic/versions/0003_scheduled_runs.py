"""scheduled runs for automated analysis

Revision ID: 0003_scheduled_runs
Revises: 0002_v11_extensions
Create Date: 2026-03-16
"""

from alembic import op
import sqlalchemy as sa

revision = '0003_scheduled_runs'
down_revision = '0002_v11_extensions'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'scheduled_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('pair', sa.String(length=20), nullable=False),
        sa.Column('timeframe', sa.String(length=10), nullable=False),
        sa.Column('mode', sa.String(length=20), nullable=False),
        sa.Column('risk_percent', sa.Float(), nullable=False),
        sa.Column('metaapi_account_ref', sa.Integer(), sa.ForeignKey('metaapi_accounts.id'), nullable=True),
        sa.Column('cron_expression', sa.String(length=120), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('last_run_at', sa.DateTime(), nullable=True),
        sa.Column('next_run_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index(op.f('ix_scheduled_runs_id'), 'scheduled_runs', ['id'], unique=False)
    op.create_index(op.f('ix_scheduled_runs_pair'), 'scheduled_runs', ['pair'], unique=False)
    op.create_index(op.f('ix_scheduled_runs_is_active'), 'scheduled_runs', ['is_active'], unique=False)
    op.create_index(op.f('ix_scheduled_runs_next_run_at'), 'scheduled_runs', ['next_run_at'], unique=False)
    op.create_index(op.f('ix_scheduled_runs_created_by_id'), 'scheduled_runs', ['created_by_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_scheduled_runs_created_by_id'), table_name='scheduled_runs')
    op.drop_index(op.f('ix_scheduled_runs_next_run_at'), table_name='scheduled_runs')
    op.drop_index(op.f('ix_scheduled_runs_is_active'), table_name='scheduled_runs')
    op.drop_index(op.f('ix_scheduled_runs_pair'), table_name='scheduled_runs')
    op.drop_index(op.f('ix_scheduled_runs_id'), table_name='scheduled_runs')
    op.drop_table('scheduled_runs')
