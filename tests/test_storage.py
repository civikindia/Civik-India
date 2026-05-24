import io
import sys
import types

import pytest

from app import create_app, db
from app.models import Complaint, Department, EvidenceFile, Service, User
from app.storage import generate_evidence_storage_key
from app.storage.r2_storage import R2Storage
from app.storage.google_drive_backup import GoogleDriveBackup
from app.utils import save_uploaded_file


@pytest.fixture
def app():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def complaint(app):
    dept = Department(name='Water Supply', description='Water services')
    db.session.add(dept)
    db.session.flush()
    service = Service(name='Leak Repair', department_id=dept.id)
    db.session.add(service)
    db.session.flush()
    complaint = Complaint(
        tracking_id='CIVIK/2026/05/STORAGE1',
        service_id=service.id,
        department_id=dept.id,
        description='Storage test complaint with enough detail for evidence checks.',
    )
    db.session.add(complaint)
    db.session.commit()
    return complaint


def _store_evidence(complaint, content=b'%PDF-1.4 private evidence'):
    success, result = save_uploaded_file(
        type('Upload', (), {
            'filename': 'evidence.pdf',
            'read': lambda self: content,
        })(),
        complaint_id=complaint.id,
    )
    assert success, result
    evidence = EvidenceFile(
        complaint_id=complaint.id,
        filename=result['filename'],
        original_filename=result['original_filename'],
        safe_filename=result['safe_filename'],
        mime_type=result['mime_type'],
        file_size=result['file_size'],
        byte_size=result['byte_size'],
        file_extension=result['file_extension'],
        encryption_iv=result['encryption_iv'],
        file_hash_sha256=result['file_hash_sha256'],
        sha256_hash=result['sha256_hash'],
        storage_path=result['relative_path'],
        storage_provider=result['storage_provider'],
        storage_bucket=result['storage_bucket'],
        storage_key=result['storage_key'],
        drive_backup_status=result['drive_backup_status'],
        encrypted=result['encrypted'],
    )
    complaint.evidence_path = evidence.storage_key
    db.session.add(evidence)
    db.session.commit()
    return evidence


def test_storage_key_generation_is_private_and_random():
    key = generate_evidence_storage_key(42, 'pdf', encrypted=True)
    assert key.startswith('evidence/42/')
    assert key.endswith('.pdf.enc')
    assert '..' not in key


def test_upload_rejects_blocked_extension(app, complaint):
    success, result = save_uploaded_file(
        type('Upload', (), {
            'filename': 'malware.exe',
            'read': lambda self: b'MZ executable',
        })(),
        complaint_id=complaint.id,
    )
    assert success is False
    assert 'not allowed' in result.lower() or 'invalid file extension' in result.lower()


def test_upload_rejects_size_over_limit(app, complaint):
    app.config['MAX_CONTENT_LENGTH'] = 4
    success, result = save_uploaded_file(
        type('Upload', (), {
            'filename': 'evidence.pdf',
            'read': lambda self: b'%PDF-1.4 too large',
        })(),
        complaint_id=complaint.id,
    )
    assert success is False
    assert 'maximum allowed size' in result


def test_evidence_metadata_created_for_private_upload(app, complaint):
    evidence = _store_evidence(complaint)
    assert evidence.storage_provider == 'local'
    assert evidence.storage_key.startswith(f'evidence/{complaint.id}/')
    assert evidence.sha256_hash == evidence.file_hash_sha256
    assert evidence.encrypted is True
    assert evidence.drive_backup_status == 'disabled'


def test_r2_storage_mock_upload(app):
    captured = {}

    class FakeClient:
        def upload_fileobj(self, stream, bucket, key, ExtraArgs=None):
            captured['bucket'] = bucket
            captured['key'] = key
            captured['data'] = stream.read()
            captured['extra'] = ExtraArgs

    storage = R2Storage(client=FakeClient(), bucket_name='private-evidence')
    storage.save_file(io.BytesIO(b'private'), 'evidence/1/file.pdf.enc', 'application/pdf', {'sha256': 'abc'})

    assert captured['bucket'] == 'private-evidence'
    assert captured['key'] == 'evidence/1/file.pdf.enc'
    assert captured['data'] == b'private'
    assert captured['extra']['Metadata']['sha256'] == 'abc'


def test_google_drive_backup_mock_flow(app, complaint, monkeypatch):
    evidence = _store_evidence(complaint)
    app.config['GOOGLE_DRIVE_FOLDER_ID'] = 'folder123'

    class FakeFiles:
        def list(self, **kwargs):
            return type('Call', (), {'execute': lambda self: {'files': []}})()

        def create(self, **kwargs):
            return type('Call', (), {'execute': lambda self: {'id': 'drive-file-1'}})()

    class FakeService:
        def files(self):
            return FakeFiles()

    fake_http = types.ModuleType('googleapiclient.http')
    fake_http.MediaIoBaseUpload = lambda stream, mimetype=None, resumable=False: stream
    monkeypatch.setitem(sys.modules, 'googleapiclient', types.ModuleType('googleapiclient'))
    monkeypatch.setitem(sys.modules, 'googleapiclient.http', fake_http)

    backup = GoogleDriveBackup(service=FakeService(), folder_id='folder123')
    drive_id = backup.backup_evidence_file(evidence)

    assert drive_id == 'drive-file-1'
    assert evidence.drive_backup_status == 'success'
    assert evidence.drive_backup_file_id == 'drive-file-1'


def test_unauthorized_officer_evidence_download_blocked(client, app, complaint):
    _store_evidence(complaint)
    other_dept = Department(name='Roads', description='Road services')
    db.session.add(other_dept)
    db.session.flush()
    officer = User(username='other_officer', role='officer', department_id=other_dept.id, is_active=True)
    officer.set_password('Officer@1234')
    db.session.add(officer)
    db.session.commit()

    with client.session_transaction() as sess:
        sess['user_id'] = officer.id
        sess['username'] = officer.username
        sess['role'] = officer.role

    response = client.get(f'/officer/complaint/{complaint.tracking_id}/evidence')
    assert response.status_code in (302, 303)
    assert '/officer/' in response.headers['Location']


def test_authorized_admin_evidence_download_allowed(client, app, complaint):
    evidence = _store_evidence(complaint, content=b'%PDF-1.4 private evidence body')
    admin = User(username='admin', role='admin', is_active=True)
    admin.set_password('Admin@1234')
    db.session.add(admin)
    db.session.commit()

    with client.session_transaction() as sess:
        sess['user_id'] = admin.id
        sess['username'] = admin.username
        sess['role'] = admin.role

    response = client.get(f'/admin/complaint/{complaint.tracking_id}/evidence')
    assert response.status_code == 200
    assert response.data == b'%PDF-1.4 private evidence body'
    assert response.headers['Content-Type'].startswith(evidence.mime_type)
    assert 'no-store' in response.headers['Cache-Control']
