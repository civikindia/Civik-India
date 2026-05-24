"""
Notification delivery tests for Phase 5 readiness.
"""
from datetime import timedelta

import pytest

from app import create_app, db
from app.clock import utc_now
from app.models import Complaint, Department, NotificationLog, Service, User
from app.tasks import (
    send_complaint_submission_notification,
    send_officer_welcome_notification,
    send_status_update_notification,
    send_system_sms,
)


@pytest.fixture
def app():
    app = create_app('testing')
    app.config.update(
        SERVER_NAME='civikindia.test',
        MAIL_SERVER='smtp.civikindia.test',
        MAIL_SUPPRESS_SEND=True,
        MAIL_DEFAULT_SENDER='no-reply@civikindia.test',
        NOTIFICATION_TO_EMAIL='ops@civikindia.test',
    )

    with app.app_context():
        db.create_all()

        department = Department(name='Water Supply', description='Water department')
        db.session.add(department)
        db.session.flush()

        service = Service(
            name='Pipeline Leakage',
            department_id=department.id,
            description='Pipeline leak repair',
            sla_days=7
        )
        db.session.add(service)
        db.session.flush()

        admin = User(username='admin', email='admin@civikindia.test', role='admin', is_active=True)
        admin.set_password('Admin@1234')
        officer = User(
            username='officer_water',
            email='officer@civikindia.test',
            role='officer',
            department_id=department.id,
            is_active=True
        )
        officer.set_password('Officer@1234')
        db.session.add_all([admin, officer])
        db.session.flush()

        complaint = Complaint(
            tracking_id='MIBNTF0001',
            service_id=service.id,
            department_id=department.id,
            description='A detailed complaint used for notification tests.',
            status='Pending',
            submitted_at=utc_now() - timedelta(hours=2),
            updated_at=utc_now() - timedelta(hours=2),
            priority='High',
            contact_preference='email',
            voluntary_id='citizen@example.test'
        )
        complaint.initialize_sla_due()
        db.session.add(complaint)
        db.session.commit()

        yield app

        db.session.remove()
        db.drop_all()


def test_submission_notification_uses_templates_and_hashed_logs(app):
    with app.app_context():
        result = send_complaint_submission_notification('MIBNTF0001')

        assert result['success'] is True
        assert result['email_sent'] is True

        logs = NotificationLog.query.filter_by(channel='email').all()
        templates = {log.template_name for log in logs}
        assert {'assignment_notification', 'complaint_acknowledgment'} <= templates
        assert all(log.status == 'sent' for log in logs)
        assert all('example.test' not in log.recipient_hash for log in logs)


def test_status_update_notifies_staff_and_voluntary_citizen(app):
    with app.app_context():
        result = send_status_update_notification('MIBNTF0001', 'Action Taken')

        assert result['success'] is True
        assert result['email_sent'] is True

        logs = NotificationLog.query.filter_by(template_name='status_update').all()
        assert len(logs) >= 2
        assert all(log.channel == 'email' for log in logs if log.status == 'sent')


def test_officer_welcome_notification_is_logged(app):
    with app.app_context():
        officer = User.query.filter_by(username='officer_water').first()
        result = send_officer_welcome_notification(officer.id, 'TempPass@1234')

        assert result['success'] is True
        log = NotificationLog.query.filter_by(template_name='officer_welcome').first()
        assert log is not None
        assert log.status == 'sent'
        assert 'officer@civikindia.test' not in log.recipient_hash


def test_fast2sms_provider_request_is_config_driven(app, monkeypatch):
    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout=15):
        calls.append((request.full_url, dict(request.header_items()), request.data, timeout))
        return FakeResponse()

    monkeypatch.setattr('app.tasks.urllib.request.urlopen', fake_urlopen)

    with app.app_context():
        app.config.update(
            SMS_ENABLED=True,
            SMS_PROVIDER='fast2sms',
            FAST2SMS_API_KEY='test-api-key',
            FAST2SMS_SENDER_ID='CIVIK'
        )
        sent, error = send_system_sms('CivikIndia test message', ['9876543210'])

    assert sent is True
    assert error is None
    assert calls
    assert calls[0][0] == 'https://www.fast2sms.com/dev/bulkV2'
    assert calls[0][1]['Authorization'] == 'test-api-key'
    assert b'9876543210' in calls[0][2]
