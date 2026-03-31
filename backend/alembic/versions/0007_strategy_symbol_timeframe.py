"""Add symbol and timeframe columns to strategies table

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-31
"""
from alembic import op
import sqlalchemy as sa

revision = '0007_strategy_symbol_timeframe'
down_revision = '0006_agentic_runtime_events'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('strategies', sa.Column('symbol', sa.String(30), nullable=False, server_default='EURUSD.PRO'))
    op.add_column('strategies', sa.Column('timeframe', sa.String(10), nullable=False, server_default='H1'))


def downgrade() -> None:
    op.drop_column('strategies', 'timeframe')
    op.drop_column('strategies', 'symbol')
