"""add caldav refresh metadata fields

Revision ID: b7c8d9e0f1a2
Revises: f6a7b8c9d0e1
Create Date: 2026-04-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7c8d9e0f1a2'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('calendar_subscriptions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_item_count_retrieved', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('last_item_count_parsed', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('last_http_status', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('last_dav_method', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('last_refresh_detail', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('calendar_subscriptions', schema=None) as batch_op:
        batch_op.drop_column('last_refresh_detail')
        batch_op.drop_column('last_dav_method')
        batch_op.drop_column('last_http_status')
        batch_op.drop_column('last_item_count_parsed')
        batch_op.drop_column('last_item_count_retrieved')
