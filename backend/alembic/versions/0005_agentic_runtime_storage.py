"""agentic runtime sql storage

Revision ID: 0005_agentic_runtime_storage
Revises: 0004_perf_indexes
Create Date: 2026-03-21
"""

from alembic import op
import sqlalchemy as sa

revision = '0005_agentic_runtime_storage'
down_revision = '0004_perf_indexes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'agent_runtime_sessions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('run_id', sa.Integer(), sa.ForeignKey('analysis_runs.id'), nullable=False),
        sa.Column('session_key', sa.String(length=255), nullable=False),
        sa.Column('parent_session_key', sa.String(length=255), nullable=True),
        sa.Column('label', sa.String(length=120), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('mode', sa.String(length=30), nullable=False),
        sa.Column('depth', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=30), nullable=False),
        sa.Column('can_spawn', sa.Boolean(), nullable=False),
        sa.Column('control_scope', sa.String(length=30), nullable=False),
        sa.Column('turn', sa.Integer(), nullable=False),
        sa.Column('current_phase', sa.String(length=50), nullable=False),
        sa.Column('resume_count', sa.Integer(), nullable=False),
        sa.Column('source_tool', sa.String(length=120), nullable=True),
        sa.Column('objective', sa.JSON(), nullable=False),
        sa.Column('summary', sa.JSON(), nullable=False),
        sa.Column('metadata', sa.JSON(), nullable=False),
        sa.Column('completed_tools', sa.JSON(), nullable=False),
        sa.Column('state_snapshot', sa.JSON(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
        sa.Column('last_resumed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('run_id', 'session_key', name='uq_agent_runtime_sessions_run_session_key'),
    )
    op.create_index(op.f('ix_agent_runtime_sessions_id'), 'agent_runtime_sessions', ['id'], unique=False)
    op.create_index(op.f('ix_agent_runtime_sessions_run_id'), 'agent_runtime_sessions', ['run_id'], unique=False)
    op.create_index(op.f('ix_agent_runtime_sessions_session_key'), 'agent_runtime_sessions', ['session_key'], unique=False)
    op.create_index(
        'ix_agent_runtime_sessions_parent_session_key',
        'agent_runtime_sessions',
        ['parent_session_key'],
        unique=False,
    )
    op.create_index(op.f('ix_agent_runtime_sessions_status'), 'agent_runtime_sessions', ['status'], unique=False)

    op.create_table(
        'agent_runtime_messages',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('run_id', sa.Integer(), sa.ForeignKey('analysis_runs.id'), nullable=False),
        sa.Column('session_key', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=30), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('sender_session_key', sa.String(length=255), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index(op.f('ix_agent_runtime_messages_id'), 'agent_runtime_messages', ['id'], unique=False)
    op.create_index(op.f('ix_agent_runtime_messages_run_id'), 'agent_runtime_messages', ['run_id'], unique=False)
    op.create_index(op.f('ix_agent_runtime_messages_session_key'), 'agent_runtime_messages', ['session_key'], unique=False)
    op.create_index(op.f('ix_agent_runtime_messages_created_at'), 'agent_runtime_messages', ['created_at'], unique=False)
    op.create_index(
        'ix_agent_runtime_messages_run_session_id',
        'agent_runtime_messages',
        ['run_id', 'session_key', 'id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_agent_runtime_messages_run_session_id', table_name='agent_runtime_messages')
    op.drop_index(op.f('ix_agent_runtime_messages_created_at'), table_name='agent_runtime_messages')
    op.drop_index(op.f('ix_agent_runtime_messages_session_key'), table_name='agent_runtime_messages')
    op.drop_index(op.f('ix_agent_runtime_messages_run_id'), table_name='agent_runtime_messages')
    op.drop_index(op.f('ix_agent_runtime_messages_id'), table_name='agent_runtime_messages')
    op.drop_table('agent_runtime_messages')

    op.drop_index(op.f('ix_agent_runtime_sessions_status'), table_name='agent_runtime_sessions')
    op.drop_index('ix_agent_runtime_sessions_parent_session_key', table_name='agent_runtime_sessions')
    op.drop_index(op.f('ix_agent_runtime_sessions_session_key'), table_name='agent_runtime_sessions')
    op.drop_index(op.f('ix_agent_runtime_sessions_run_id'), table_name='agent_runtime_sessions')
    op.drop_index(op.f('ix_agent_runtime_sessions_id'), table_name='agent_runtime_sessions')
    op.drop_table('agent_runtime_sessions')
