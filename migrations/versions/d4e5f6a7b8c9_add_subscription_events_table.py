"""add subscription_events table

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'subscription_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('subscription_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('external_id', sa.String(length=512), nullable=False),
        sa.Column('title', sa.String(length=512), nullable=False),
        sa.Column('start_at', sa.DateTime(), nullable=True),
        sa.Column('end_at', sa.DateTime(), nullable=True),
        sa.Column('location', sa.String(length=512), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('all_day', sa.Boolean(), nullable=False),
        sa.Column('source_name', sa.String(length=255), nullable=False),
        sa.Column('color', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['subscription_id'],
            ['calendar_subscriptions.id'],
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('subscription_id', 'external_id', name='uq_sub_events_external'),
    )
    with op.batch_alter_table('subscription_events', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_subscription_events_start_at'),
            ['start_at'],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_subscription_events_subscription_id'),
            ['subscription_id'],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_subscription_events_user_id'),
            ['user_id'],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table('subscription_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_subscription_events_user_id'))
        batch_op.drop_index(batch_op.f('ix_subscription_events_subscription_id'))
        batch_op.drop_index(batch_op.f('ix_subscription_events_start_at'))
    op.drop_table('subscription_events')

