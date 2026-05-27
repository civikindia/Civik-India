"""
Tests for Citizen Notification wiring on status changes (Email, SMS, WhatsApp)
and Admin Notification Logs.
"""
import pytest
import json
import urllib.request
import urllib.parse
from datetime import timedelta

from app import create_app, db
from app.models import User, Department, Service, Complaint, NotificationLog, SLAPolicy
from app.clock import utc_now
from app.tasks import _send_twilio_whatsapp


@pytest.fixture
def app():
    """Create application for testing notifications wiring."""
    app = create_app('testing')
    app.config.update(
        SERVER_NAME='civikindia.test',
        MAIL_SERVER='smtp.civikindia.test',
        MAIL_SUPPRESS_SEND=True,
        MAIL_DEFAULT_SENDER='no-reply@civikindia.test',
        NOTIFICATION_TO_EMAIL='ops@civikindia.test',
        SMS_ENABLED=True,
        SMS_PROVIDER='fast2sms',
        FAST2SMS_API_KEY='test-api-key',
        FAST2SMS_SENDER_ID='CIVIK',
        WHATSAPP_ENABLED=True,
        TWILIO_ACCOUNT_SID='ACtest_sid',
        TWILIO_AUTH_TOKEN='test_token',
        TWILIO_WHATSAPP_FROM='+14155238886'
    )
    
    with app.app_context():
        db.create_all()
        # Seed basic data
        admin = User(username='adminuser', role='admin', is_active=True)
        admin.set_password('adminpass123')
        admin.email = 'admin@example.com'
        db.session.add(admin)
        
        dept = Department(name='Water Supply', description='Water department')
        db.session.add(dept)
        db.session.commit()
        
        service = Service(name='Pipeline Leak', department_id=dept.id)
        db.session.add(service)
        db.session.commit()
        
        officer = User(username='officer_water', role='officer', department_id=dept.id, is_active=True)
        officer.set_password('officerpass123')
        officer.email = 'officer@example.com'
        db.session.add(officer)
        
        policy = SLAPolicy(
            department_id=dept.id,
            service_id=None,
            priority_level='Normal',
            resolution_hours=72,
            is_active=True
        )
        db.session.add(policy)
        db.session.commit()
        
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture(autouse=True)
def mock_twilio_opener(monkeypatch):
    calls = []
    class FakeResponse:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self):
            return b'{"sid": "SM123"}'
            
    class FakeOpener:
        def open(self, req, timeout=15):
            calls.append(req)
            return FakeResponse()
            
    monkeypatch.setattr('urllib.request.build_opener', lambda *args: FakeOpener())
    return calls


def login_admin(client):
    """Helper to login as admin."""
    return client.post('/auth/login', data={
        'username': 'adminuser',
        'password': 'adminpass123'
    }, follow_redirects=True)


def test_approve_complaint_wires_notification(client, app):
    """Verify that approving a complaint triggers notifications and creates logs."""
    with app.app_context():
        dept = Department.query.first()
        service = Service.query.first()
        complaint = Complaint(
            tracking_id='MIBNTF1001',
            department_id=dept.id,
            service_id=service.id,
            description='This is a detailed complaint that is awaiting review by administration.',
            status='Awaiting Review',
            contact_preference='whatsapp',
            voluntary_id='+919876543210'
        )
        db.session.add(complaint)
        db.session.commit()

        login_admin(client)
        
        # Approve the complaint
        response = client.post(f'/admin/complaint/{complaint.id}/approve', data={
            'priority': 'Normal',
            'admin_notes': 'Looks valid, dispatching to officer.'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        
        # Check that NotificationLog entries were created
        email_logs = NotificationLog.query.filter_by(channel='email', template_name='status_update').all()
        assert len(email_logs) > 0
        
        whatsapp_logs = NotificationLog.query.filter_by(channel='whatsapp', template_name='status_update').all()
        assert len(whatsapp_logs) == 1
        assert whatsapp_logs[0].status == 'sent'


def test_reject_complaint_wires_notification(client, app):
    """Verify that rejecting a complaint triggers notifications."""
    with app.app_context():
        dept = Department.query.first()
        service = Service.query.first()
        complaint = Complaint(
            tracking_id='MIBNTF1002',
            department_id=dept.id,
            service_id=service.id,
            description='This is a detailed complaint that is awaiting review by administration.',
            status='Awaiting Review',
            contact_preference='whatsapp',
            voluntary_id='+919876543210'
        )
        db.session.add(complaint)
        db.session.commit()

        login_admin(client)
        
        response = client.post(f'/admin/complaint/{complaint.id}/reject', data={
            'rejection_reason': 'This complaint is rejected because it is out of scope and does not contain valid evidence.',
            'admin_notes': 'Duplicate complaint.'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        
        # Check logs
        reject_logs = NotificationLog.query.filter_by(template_name='status_update').all()
        assert len(reject_logs) >= 2


def test_bulk_approve_complaints_wires_notifications(client, app):
    """Verify that bulk approving complaints triggers notifications for each."""
    with app.app_context():
        dept = Department.query.first()
        service = Service.query.first()
        c1 = Complaint(
            tracking_id='MIBNTF1003',
            department_id=dept.id,
            service_id=service.id,
            description='This is a detailed complaint that is awaiting review by administration.',
            status='Awaiting Review',
            contact_preference='email',
            voluntary_id='citizen1@example.com'
        )
        c2 = Complaint(
            tracking_id='MIBNTF1004',
            department_id=dept.id,
            service_id=service.id,
            description='This is a second detailed complaint that is awaiting review by administration.',
            status='Awaiting Review',
            contact_preference='email',
            voluntary_id='citizen2@example.com'
        )
        db.session.add_all([c1, c2])
        db.session.commit()

        login_admin(client)
        
        response = client.post('/admin/inbox/bulk-approve', data={
            'complaint_ids': f'{c1.id},{c2.id}'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        
        # Check logs for both tracking IDs
        for cid in ['MIBNTF1003', 'MIBNTF1004']:
            logs = NotificationLog.query.filter(NotificationLog.complaint.has(tracking_id=cid)).all()
            assert len(logs) > 0


def test_bulk_reject_complaints_wires_notifications(client, app):
    """Verify that bulk rejecting complaints triggers notifications for each."""
    with app.app_context():
        dept = Department.query.first()
        service = Service.query.first()
        c1 = Complaint(
            tracking_id='MIBNTF1005',
            department_id=dept.id,
            service_id=service.id,
            description='This is a detailed complaint that is awaiting review by administration.',
            status='Awaiting Review',
            contact_preference='email',
            voluntary_id='citizen1@example.com'
        )
        c2 = Complaint(
            tracking_id='MIBNTF1006',
            department_id=dept.id,
            service_id=service.id,
            description='This is a second detailed complaint that is awaiting review by administration.',
            status='Awaiting Review',
            contact_preference='email',
            voluntary_id='citizen2@example.com'
        )
        db.session.add_all([c1, c2])
        db.session.commit()

        login_admin(client)
        
        response = client.post('/admin/inbox/bulk-reject', data={
            'complaint_ids': f'{c1.id},{c2.id}',
            'rejection_reason': 'These complaints are rejected because they do not have sufficient details to proceed.'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        
        for cid in ['MIBNTF1005', 'MIBNTF1006']:
            logs = NotificationLog.query.filter(NotificationLog.complaint.has(tracking_id=cid)).all()
            assert len(logs) > 0


def test_admin_notification_logs_view(client, app):
    """Verify the /admin/notifications route returns logs and supports filters."""
    login_admin(client)
    
    with app.app_context():
        # Create some notification logs manually
        dept = Department.query.first()
        service = Service.query.first()
        complaint = Complaint(
            tracking_id='MIBNTF1007',
            department_id=dept.id,
            service_id=service.id,
            description='Some complaint details.',
            status='Pending'
        )
        db.session.add(complaint)
        db.session.commit()
        
        log1 = NotificationLog(
            complaint_id=complaint.id,
            channel='email',
            recipient_hash='hash1',
            template_name='status_update',
            status='sent',
            sent_at=utc_now()
        )
        log2 = NotificationLog(
            complaint_id=complaint.id,
            channel='whatsapp',
            recipient_hash='hash2',
            template_name='status_update',
            status='failed',
            error_message='Sandbox limitation'
        )
        db.session.add_all([log1, log2])
        db.session.commit()

    # Retrieve logs view
    response = client.get('/admin/notifications')
    assert response.status_code == 200
    assert b'Notification Logs' in response.data
    assert b'MIBNTF1007' in response.data
    assert b'Email' in response.data
    assert b'WhatsApp' in response.data
    assert b'Failed' in response.data
    assert b'Sandbox limitation' in response.data

    # Retrieve filtered view - email channel
    response_email = client.get('/admin/notifications?channel=email')
    assert response_email.status_code == 200
    assert b'Email' in response_email.data
    assert b'Sandbox limitation' not in response_email.data


def test_send_twilio_whatsapp_checks_configs(app):
    """Test _send_twilio_whatsapp functionality and configuration validation."""
    with app.app_context():
        # Test disabled
        app.config['WHATSAPP_ENABLED'] = False
        success, error = _send_twilio_whatsapp('Hello', ['+919876543210'])
        assert success is False
        assert 'disabled' in error

        # Test missing credential
        app.config['WHATSAPP_ENABLED'] = True
        app.config['TWILIO_ACCOUNT_SID'] = ''
        success, error = _send_twilio_whatsapp('Hello', ['+919876543210'])
        assert success is False
        assert 'incomplete' in error
