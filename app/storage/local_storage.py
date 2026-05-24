"""Local private file storage used for development and tests."""
import os
from pathlib import Path, PurePosixPath

from flask import current_app

from app.storage.base import EvidenceStorage, StorageError


def _safe_local_path(root, key):
    key = str(PurePosixPath(str(key).replace('\\', '/'))).lstrip('/')
    if key.startswith('../') or '/..' in key or key == '..':
        raise StorageError('Unsafe storage key.')
    root_path = Path(root).resolve()
    target = (root_path / key).resolve()
    if root_path not in target.parents and target != root_path:
        raise StorageError('Storage key escapes upload directory.')
    return target


class LocalStorage(EvidenceStorage):
    provider_name = 'local'

    def __init__(self, root_path=None):
        self.root_path = root_path or current_app.config['UPLOAD_FOLDER']

    def save_file(self, file_stream, key, content_type=None, metadata=None):
        target = _safe_local_path(self.root_path, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, 'wb') as handle:
            while True:
                chunk = file_stream.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
        return key

    def open_file(self, key):
        target = _safe_local_path(self.root_path, key)
        if not target.exists():
            raise FileNotFoundError(str(target))
        return open(target, 'rb')

    def delete_file(self, key):
        target = _safe_local_path(self.root_path, key)
        try:
            if target.exists():
                os.remove(target)
            return True
        except OSError as exc:
            raise StorageError('Could not delete local evidence file.') from exc

    def exists(self, key):
        return _safe_local_path(self.root_path, key).exists()
