"""add announcements table

Revision ID: 13af01984925
Revises: 20260524_0001
Create Date: 2026-05-28 14:40:16.094682

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '13af01984925'
down_revision = '20260524_0001'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'announcements' not in inspector.get_table_names():
        op.create_table('announcements',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('title', sa.String(length=200), nullable=False),
            sa.Column('body', sa.Text(), nullable=False),
            sa.Column('category', sa.String(length=50), nullable=False, default='General'),
            sa.Column('priority', sa.String(length=20), nullable=False, default='info'),
            sa.Column('is_active', sa.Boolean(), nullable=False, default=True),
            sa.Column('is_pinned', sa.Boolean(), nullable=False, default=False),
            sa.Column('show_on_home', sa.Boolean(), nullable=False, default=False),
            sa.Column('expires_at', sa.DateTime(), nullable=True),
            sa.Column('published_at', sa.DateTime(), nullable=True),
            sa.Column('created_by_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
            sa.PrimaryKeyConstraint('id')
        )



def downgrade():
    op.drop_table('announcements')

