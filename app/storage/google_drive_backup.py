"""Private Google Drive archive backup for evidence files."""
import io
import json

from flask import current_app

from app import db
from app.models import EvidenceFile
from app.storage import get_storage


class DriveBackupError(RuntimeError):
    """Raised when Google Drive backup cannot complete."""


class GoogleDriveBackup:
    """Upload private evidence objects to a private Google Drive folder."""

    def __init__(self, service=None, folder_id=None):
        self.folder_id = folder_id or current_app.config.get('GOOGLE_DRIVE_FOLDER_ID')
        if not self.folder_id:
            raise DriveBackupError('GOOGLE_DRIVE_FOLDER_ID is required.')
        self.service = service or self._build_service()

    def _build_service(self):
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise DriveBackupError('google-api-python-client and google-auth are required.') from exc

        raw_json = current_app.config.get('GOOGLE_SERVICE_ACCOUNT_JSON')
        credential_path = current_app.config.get('GOOGLE_APPLICATION_CREDENTIALS')
        scopes = ['https://www.googleapis.com/auth/drive.file']

        if raw_json:
            info = json.loads(raw_json)
            credentials = service_account.Credentials.from_service_account_info(info, scopes=scopes)
        elif credential_path:
            credentials = service_account.Credentials.from_service_account_file(credential_path, scopes=scopes)
        else:
            raise DriveBackupError('Google Drive service account credentials are not configured.')

        return build('drive', 'v3', credentials=credentials, cache_discovery=False)

    def _backup_name(self, evidence_file):
        key = evidence_file.storage_key or evidence_file.storage_path or evidence_file.filename
        safe_id = evidence_file.id or 'pending'
        return f'evidence-{safe_id}-{str(key).replace("/", "_")}'

    def _find_existing(self, name):
        escaped_name = name.replace("'", "\\'")
        query = (
            f"name = '{escaped_name}' and '{self.folder_id}' in parents "
            "and trashed = false"
        )
        result = self.service.files().list(
            q=query,
            fields='files(id, name)',
            pageSize=1,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files = result.get('files', [])
        return files[0]['id'] if files else None

    def backup_evidence_file(self, evidence_file):
        """Back up one EvidenceFile record idempotently."""
        if evidence_file.drive_backup_status == 'success' and evidence_file.drive_backup_file_id:
            return evidence_file.drive_backup_file_id

        name = self._backup_name(evidence_file)
        existing_id = self._find_existing(name)
        if existing_id:
            evidence_file.drive_backup_file_id = existing_id
            evidence_file.drive_backup_status = 'success'
            db.session.add(evidence_file)
            return existing_id

        try:
            from googleapiclient.http import MediaIoBaseUpload
        except ImportError as exc:
            raise DriveBackupError('google-api-python-client is required.') from exc

        storage = get_storage(evidence_file.storage_provider or 'local')
        key = evidence_file.storage_key or evidence_file.storage_path
        with storage.open_file(key) as source:
            payload = source.read()

        # Back up the private stored object as-is. If app-level encryption is enabled,
        # the Drive archive receives the encrypted bytes, not the decrypted evidence.
        media = MediaIoBaseUpload(
            io.BytesIO(payload),
            mimetype='application/octet-stream',
            resumable=False,
        )
        metadata = {
            'name': name,
            'parents': [self.folder_id],
            'appProperties': {
                'civikindia_evidence_id': str(evidence_file.id),
                'storage_key': str(key),
                'encrypted': str(bool(evidence_file.encrypted)).lower(),
            },
        }
        uploaded = self.service.files().create(
            body=metadata,
            media_body=media,
            fields='id',
            supportsAllDrives=True,
        ).execute()
        file_id = uploaded['id']
        evidence_file.drive_backup_file_id = file_id
        evidence_file.drive_backup_status = 'success'
        db.session.add(evidence_file)
        return file_id


def backup_pending_evidence(limit=100):
    """Back up pending/failed evidence records to Google Drive."""
    if not current_app.config.get('GOOGLE_DRIVE_BACKUP_ENABLED'):
        return {'enabled': False, 'processed': 0, 'success': 0, 'failed': 0}

    records = EvidenceFile.query.filter(
        EvidenceFile.deleted_at.is_(None),
        EvidenceFile.drive_backup_status.in_(['pending', 'failed'])
    ).order_by(EvidenceFile.created_at.asc()).limit(limit).all()

    backup = GoogleDriveBackup()
    summary = {'enabled': True, 'processed': 0, 'success': 0, 'failed': 0}
    for evidence_file in records:
        summary['processed'] += 1
        try:
            backup.backup_evidence_file(evidence_file)
            summary['success'] += 1
        except Exception as exc:
            current_app.logger.exception(
                'Evidence Drive backup failed for evidence_file_id=%s: %s',
                evidence_file.id,
                exc,
            )
            evidence_file.drive_backup_status = 'failed'
            db.session.add(evidence_file)
            summary['failed'] += 1
        db.session.commit()
    return summary
