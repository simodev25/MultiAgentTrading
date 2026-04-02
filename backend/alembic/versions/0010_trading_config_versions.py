"""Add trading_config_versions table

Revision ID: 0010_trading_config_versions
Revises: 0009_portfolio_snapshots
Create Date: 2026-04-01
"""
from alembic import op
import sqlalchemy as sa

revision = '0010_trading_config_versions'
down_revision = '0009_portfolio_snapshots'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'trading_config_versions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('version', sa.Integer(), nullable=False, index=True),
        sa.Column('changed_by', sa.String(255), nullable=False, server_default='admin'),
        sa.Column('changed_at', sa.DateTime(), nullable=False, index=True),
        sa.Column('decision_mode', sa.String(20), nullable=False, server_default='balanced'),
        sa.Column('settings_snapshot', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('changes_summary', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('trading_config_versions')
