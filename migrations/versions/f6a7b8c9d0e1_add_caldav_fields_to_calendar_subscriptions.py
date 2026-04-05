"""add caldav fields to calendar subscriptions

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('calendar_subscriptions', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'subscription_type',
                sa.String(length=16),
                nullable=False,
                server_default='ics',
            )
        )
        batch_op.add_column(
            sa.Column('caldav_username', sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column('caldav_password_enc', sa.Text(), nullable=True)
        )


def downgrade():
    with op.batch_alter_table('calendar_subscriptions', schema=None) as batch_op:
        batch_op.drop_column('caldav_password_enc')
        batch_op.drop_column('caldav_username')
        batch_op.drop_column('subscription_type')
