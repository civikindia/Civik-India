"""
CivikIndia Celery Tasks Tests
"""
import pytest
import json
from unittest.mock import patch, MagicMock
from app import create_app, db
from app.models import User, Department, Service, Complaint, EvidenceFile, AuditLog
from app.tasks import generate_daily_report, cleanup_old_uploads
from app.clock import utc_now

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

class TestCeleryTasks:
    """Tests for daily report generation and upload cleanup Celery tasks."""

    @patch('app.tasks.send_system_email')
    def test_generate_daily_report_success(self, mock_send_email, app):
        """Test daily report task fetches stats, renders template, and creates audit log."""
        mock_send_email.return_value = (True, None)

        # Add a dummy department, service, and complaint
        dept = Department(name='Sanitation', description='Cleanup')
        db.session.add(dept)
        db.session.flush()

        service = Service(name='Trash Overflow', department_id=dept.id)
        db.session.add(service)
        db.session.flush()

        c = Complaint(
            tracking_id='CIVIK/2026/05/DAILY1',
            department_id=dept.id,
            service_id=service.id,
            description='Trash overflowing has created health hazard and bad smell.',
            status='Pending'
        )
        db.session.add(c)
        db.session.commit()

        result = generate_daily_report()
        assert result['success'] is True
        assert result['total'] == 1
        assert result['recipients'] == 1

        # Verify email was called
        mock_send_email.assert_called_once()
        args = mock_send_email.call_args[0]
        assert 'Daily Report' in args[0]  # subject
        assert 'Sanitation' in args[1]     # plain text body

        # Verify audit log entry
        audit = AuditLog.query.filter_by(action='DAILY_REPORT_SENT').first()
        assert audit is not None
        details = json.loads(audit.details)
        assert details['total'] == 1
        assert details['sent'] is True

    @patch('app.tasks._delete_local_file')
    @patch('app.tasks._delete_r2_object')
    def test_cleanup_old_uploads_purges_db_and_files(self, mock_delete_r2, mock_delete_local, app):
        """Test cleanup task purges both local and R2 files and removes DB rows."""
        from datetime import timedelta

        dept = Department(name='Sanitation', description='Cleanup')
        db.session.add(dept)
        db.session.flush()

        service = Service(name='Trash Overflow', department_id=dept.id)
        db.session.add(service)
        db.session.flush()

        c = Complaint(
            tracking_id='CIVIK/2026/05/DAILY1',
            department_id=dept.id,
            service_id=service.id,
            description='Trash overflowing has created health hazard and bad smell.',
            status='Pending'
        )
        db.session.add(c)
        db.session.commit()

        # Create two evidence files: one soft-deleted 31 days ago, one not soft-deleted
        old_time = utc_now() - timedelta(days=31)
        
        ef1 = EvidenceFile(
            complaint_id=c.id,
            filename='old_file.png',
            original_filename='old_file.png',
            safe_filename='old_file.png',
            mime_type='image/png',
            file_size='100KB',
            byte_size=102400,
            file_extension='png',
            storage_path='old_file.png',
            storage_provider='local',
            storage_key='old_file.png',
            deleted_at=old_time
        )
        
        ef2 = EvidenceFile(
            complaint_id=c.id,
            filename='active_file.png',
            original_filename='active_file.png',
            safe_filename='active_file.png',
            mime_type='image/png',
            file_size='100KB',
            byte_size=102400,
            file_extension='png',
            storage_path='active_file.png',
            storage_provider='r2',
            storage_key='active_file.png',
            deleted_at=None
        )

        db.session.add(ef1)
        db.session.add(ef2)
        db.session.commit()

        # Run with days=30
        result = cleanup_old_uploads(days=30)
        assert result['success'] is True
        assert result['purged'] == 1
        
        # Verify ef1 was physically deleted locally
        mock_delete_local.assert_called_once_with(ef1)
        mock_delete_r2.assert_not_called()

        # Verify ef1 is removed from DB, while ef2 remains
        assert db.session.get(EvidenceFile, ef1.id) is None
        assert db.session.get(EvidenceFile, ef2.id) is not None

        # Verify audit log entry
        audit = AuditLog.query.filter_by(action='EVIDENCE_CLEANUP_RUN').first()
        assert audit is not None
        details = json.loads(audit.details)
        assert details['purged'] == 1
        assert details['days_retention'] == 30
