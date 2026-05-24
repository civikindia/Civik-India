"""add private evidence storage metadata

Revision ID: 20260524_0001
Revises:
Create Date: 2026-05-24
"""
from alembic import op
import sqlalchemy as sa


revision = '20260524_0001'
down_revision = None
branch_labels = None
depends_on = None


def _has_table(inspector, table_name):
    return table_name in inspector.get_table_names()


def _columns(inspector, table_name):
    return {column['name'] for column in inspector.get_columns(table_name)}


def _add_missing(table_name, existing, column):
    if column.name not in existing:
        op.add_column(table_name, column)
        existing.add(column.name)


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _has_table(inspector, 'evidence_files'):
        return

    existing = _columns(inspector, 'evidence_files')
    _add_missing('evidence_files', existing, sa.Column('uploaded_by_user_id', sa.Integer(), nullable=True))
    _add_missing('evidence_files', existing, sa.Column('safe_filename', sa.String(length=255), nullable=True))
    _add_missing('evidence_files', existing, sa.Column('file_extension', sa.String(length=20), nullable=True))
    _add_missing('evidence_files', existing, sa.Column('byte_size', sa.Integer(), nullable=True))
    _add_missing('evidence_files', existing, sa.Column('sha256_hash', sa.String(length=64), nullable=True))
    _add_missing('evidence_files', existing, sa.Column('storage_provider', sa.String(length=40), nullable=False, server_default='local'))
    _add_missing('evidence_files', existing, sa.Column('storage_bucket', sa.String(length=255), nullable=True))
    _add_missing('evidence_files', existing, sa.Column('storage_key', sa.String(length=512), nullable=True))
    _add_missing('evidence_files', existing, sa.Column('drive_backup_file_id', sa.String(length=255), nullable=True))
    _add_missing('evidence_files', existing, sa.Column('drive_backup_status', sa.String(length=20), nullable=False, server_default='disabled'))
    _add_missing('evidence_files', existing, sa.Column('encrypted', sa.Boolean(), nullable=False, server_default=sa.false()))
    _add_missing('evidence_files', existing, sa.Column('created_at', sa.DateTime(), nullable=True))
    _add_missing('evidence_files', existing, sa.Column('updated_at', sa.DateTime(), nullable=True))
    _add_missing('evidence_files', existing, sa.Column('deleted_at', sa.DateTime(), nullable=True))

    try:
        op.create_index('ix_evidence_files_storage_key', 'evidence_files', ['storage_key'], unique=False)
    except Exception:
        pass
    try:
        op.create_index('ix_evidence_files_backup_status', 'evidence_files', ['drive_backup_status', 'created_at'], unique=False)
    except Exception:
        pass


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _has_table(inspector, 'evidence_files'):
        return

    for index_name in ('ix_evidence_files_backup_status', 'ix_evidence_files_storage_key'):
        try:
            op.drop_index(index_name, table_name='evidence_files')
        except Exception:
            pass

    existing = _columns(inspector, 'evidence_files')
    for column_name in (
        'deleted_at', 'updated_at', 'created_at', 'encrypted',
        'drive_backup_status', 'drive_backup_file_id', 'storage_key',
        'storage_bucket', 'storage_provider', 'sha256_hash', 'byte_size',
        'file_extension', 'safe_filename', 'uploaded_by_user_id',
    ):
        if column_name in existing:
            op.drop_column('evidence_files', column_name)
