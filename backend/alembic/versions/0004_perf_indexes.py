"""performance indexes for hot paths

Revision ID: 0004_perf_indexes
Revises: 0003_scheduled_runs
Create Date: 2026-03-17
"""

from alembic import op

revision = '0004_perf_indexes'
down_revision = '0003_scheduled_runs'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index('ix_analysis_runs_created_at', 'analysis_runs', ['created_at'], unique=False)
    op.create_index('ix_execution_orders_created_at', 'execution_orders', ['created_at'], unique=False)
    op.create_index('ix_llm_call_logs_created_at', 'llm_call_logs', ['created_at'], unique=False)
    op.create_index('ix_llm_call_logs_status_created_at', 'llm_call_logs', ['status', 'created_at'], unique=False)
    op.create_index(
        'ix_scheduled_runs_is_active_next_run_at_id',
        'scheduled_runs',
        ['is_active', 'next_run_at', 'id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_scheduled_runs_is_active_next_run_at_id', table_name='scheduled_runs')
    op.drop_index('ix_llm_call_logs_status_created_at', table_name='llm_call_logs')
    op.drop_index('ix_llm_call_logs_created_at', table_name='llm_call_logs')
    op.drop_index('ix_execution_orders_created_at', table_name='execution_orders')
    op.drop_index('ix_analysis_runs_created_at', table_name='analysis_runs')
