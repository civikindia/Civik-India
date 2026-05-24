"""Storage backend selection and evidence key helpers."""
import uuid

from flask import current_app

from app.storage.base import StorageError
from app.storage.local_storage import LocalStorage


def get_storage(provider=None):
    """Return the configured evidence storage backend."""
    provider = (provider or current_app.config.get('EVIDENCE_STORAGE_PROVIDER') or 'local').lower()
    if provider == 'local':
        return LocalStorage()
    if provider == 'r2':
        from app.storage.r2_storage import R2Storage
        return R2Storage()
    raise StorageError(f'Unsupported evidence storage provider: {provider}')


def generate_evidence_storage_key(complaint_id, extension, encrypted=False):
    """Generate a private object key for an evidence upload."""
    safe_ext = (extension or 'bin').lower().lstrip('.')
    suffix = f'.{safe_ext}'
    if encrypted:
        suffix += '.enc'
    complaint_segment = str(complaint_id or 'pending')
    return f'evidence/{complaint_segment}/{uuid.uuid4().hex}{suffix}'
