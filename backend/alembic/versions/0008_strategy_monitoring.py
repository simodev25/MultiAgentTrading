"""Add monitoring columns to strategies table

Revision ID: 0008_strategy_monitoring
Revises: 0007_strategy_symbol_timeframe
Create Date: 2026-03-31
"""
from alembic import op
import sqlalchemy as sa

revision = '0008_strategy_monitoring'
down_revision = '0007_strategy_symbol_timeframe'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('strategies', sa.Column('is_monitoring', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('strategies', sa.Column('monitoring_mode', sa.String(20), nullable=False, server_default='simulation'))
    op.add_column('strategies', sa.Column('monitoring_risk_percent', sa.Float(), nullable=False, server_default='1.0'))
    op.add_column('strategies', sa.Column('last_signal_key', sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column('strategies', 'last_signal_key')
    op.drop_column('strategies', 'monitoring_risk_percent')
    op.drop_column('strategies', 'monitoring_mode')
    op.drop_column('strategies', 'is_monitoring')
