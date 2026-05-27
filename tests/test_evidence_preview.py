"""
CivikIndia Evidence Preview Tests
Tests for secure in-browser evidence previewing, role authorization, and audit logs.
"""
import pytest
import json
from app import create_app, db
from app.models import Complaint, Department, EvidenceFile, Service, User, AuditLog
from app.utils import save_uploaded_file

@pytest.fixture
def app():
    """Create application for testing."""
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        # Seed basic admin user
        admin = User(username='adminuser', role='admin', is_active=True)
        admin.set_password('adminpass123')
        admin.email = 'admin@example.com'
        db.session.add(admin)
        db.session.commit()
        
        yield app
        db.session.remove()
        db.drop_all()

@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()

@pytest.fixture
def complaint(app):
    """Create a test complaint."""
    dept = Department(name='Water Supply', description='Water services')
    db.session.add(dept)
    db.session.flush()
    service = Service(name='Leak Repair', department_id=dept.id)
    db.session.add(service)
    db.session.flush()
    complaint = Complaint(
        tracking_id='CIVIK/2026/05/PREVIEW1',
        service_id=service.id,
        department_id=dept.id,
        description='Test complaint with enough details for preview test.',
    )
    db.session.add(complaint)
    db.session.commit()
    return complaint

def _store_evidence(complaint, content=b'%PDF-1.4 private evidence content'):
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

class TestEvidencePreviewFlow:
    """Tests for Evidence Preview routing and access control."""

    def test_unauthorized_evidence_preview_blocked(self, client, app, complaint):
        """Anonymous or officer users must be blocked from previewing evidence."""
        _store_evidence(complaint)

        # 1. Anonymous access -> redirect to login
        response = client.get(f'/admin/complaint/{complaint.tracking_id}/evidence/preview', follow_redirects=True)
        assert b'Please log in to access this page' in response.data

        # 2. Officer access -> redirect or forbidden
        dept = Department.query.first()
        officer = User(username='officer_water', role='officer', department_id=dept.id, is_active=True)
        officer.set_password('officerpass123')
        db.session.add(officer)
        db.session.commit()

        with client.session_transaction() as sess:
            sess['user_id'] = officer.id
            sess['username'] = officer.username
            sess['role'] = officer.role

        response = client.get(f'/admin/complaint/{complaint.tracking_id}/evidence/preview')
        assert response.status_code == 403
        assert b'Access Denied' in response.data

    def test_authorized_admin_evidence_preview_success(self, client, app, complaint):
        """Admins can view preview with proper inline header and audit logging."""
        _store_evidence(complaint, content=b'%PDF-1.4 secret preview data')

        # Log in as admin
        admin = User.query.filter_by(role='admin').first()
        with client.session_transaction() as sess:
            sess['user_id'] = admin.id
            sess['username'] = admin.username
            sess['role'] = admin.role

        response = client.get(f'/admin/complaint/{complaint.tracking_id}/evidence/preview')
        assert response.status_code == 200
        assert response.data == b'%PDF-1.4 secret preview data'
        
        # Security headers
        assert response.headers['Content-Disposition'] == 'inline; filename="evidence.pdf"'
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
        assert 'no-store' in response.headers['Cache-Control']
        assert response.headers['X-Frame-Options'] == 'SAMEORIGIN'

        # Audit log verification
        audit = AuditLog.query.filter_by(action='EVIDENCE_PREVIEWED').first()
        assert audit is not None
        details = json.loads(audit.details)
        assert details['tracking_id'] == complaint.tracking_id
        assert details['filename'] == 'evidence.pdf'
        assert details['mime_type'] == 'application/pdf'
