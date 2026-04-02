"""Add portfolio_snapshots table

Revision ID: 0009_portfolio_snapshots
Revises: 0008_strategy_monitoring
Create Date: 2026-04-01
"""
from alembic import op
import sqlalchemy as sa

revision = '0009_portfolio_snapshots'
down_revision = '0008_strategy_monitoring'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'portfolio_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('account_id', sa.String(120), nullable=False, index=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False, index=True),
        sa.Column('balance', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('equity', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('free_margin', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('used_margin', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('open_position_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('open_risk_total_pct', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('daily_realized_pnl', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('daily_high_equity', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('snapshot_type', sa.String(20), nullable=False, server_default='periodic'),
    )


def downgrade() -> None:
    op.drop_table('portfolio_snapshots')
