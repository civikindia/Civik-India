"""
Full-site smoke tests for local grading readiness.
"""
import pytest
from datetime import timedelta

from app import create_app, db
from app.clock import utc_now
from app.models import AuditLog, Complaint, Department, EscalationContact, Service, User


@pytest.fixture
def app():
    app = create_app('testing')
    app.config['OPENAI_API_KEY'] = None

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

        admin = User(
            username='admin',
            email='admin@civikindia.gov.in',
            role='admin',
            is_active=True
        )
        admin.set_password('Admin@1234')

        officer = User(
            username='officer_water',
            email='officer_water@civikindia.gov.in',
            role='officer',
            department_id=department.id,
            is_active=True
        )
        officer.set_password('Officer@1234')
        db.session.add_all([admin, officer])
        db.session.flush()

        now = utc_now()
        complaints = [
            Complaint(
                tracking_id='MIBSMK0001',
                service_id=service.id,
                department_id=department.id,
                description='A detailed pending water complaint for smoke coverage.',
                status='Pending',
                submitted_at=now - timedelta(days=2),
                updated_at=now - timedelta(days=2),
                state='Maharashtra',
                district='Mumbai Suburban',
                city='Mumbai',
                location_lat=19.076,
                location_lng=72.8777,
            ),
            Complaint(
                tracking_id='MIBSMK0002',
                service_id=service.id,
                department_id=department.id,
                description='A detailed assigned water complaint for officer pages.',
                status='Under Review',
                assigned_to=officer.id,
                submitted_at=now - timedelta(days=3),
                updated_at=now - timedelta(days=1),
                state='Maharashtra',
                district='Mumbai Suburban',
                city='Mumbai',
                location_lat=19.082,
                location_lng=72.881,
            ),
            Complaint(
                tracking_id='MIBSMK0003',
                service_id=service.id,
                department_id=department.id,
                description='A detailed closed complaint for reports and feedback screens.',
                status='Closed',
                assigned_to=officer.id,
                submitted_at=now - timedelta(days=10),
                updated_at=now - timedelta(days=1),
                resolved_at=now - timedelta(days=1),
                citizen_rating=4,
                state='Maharashtra',
                district='Mumbai Suburban',
                city='Mumbai',
                location_lat=19.071,
                location_lng=72.872,
            ),
        ]
        for complaint in complaints:
            complaint.initialize_sla_due()
            db.session.add(complaint)

        db.session.commit()
        AuditLog.create_entry(
            username='system',
            role='system',
            action='SMOKE_FIXTURE_READY',
            details='Full-site smoke fixture created.'
        )

        yield app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _assert_ok(response):
    assert response.status_code == 200
    assert len(response.get_data()) > 0


class TestFullSiteSmoke:
    def test_public_pages_and_apis(self, client):
        public_pages = [
            '/',
            '/about',
            '/how-it-works',
            '/submit',
            '/track',
            '/dashboard',
            '/geo-heatmap',
            '/confirmation/MIBSMK0001',
        ]
        for path in public_pages:
            _assert_ok(client.get(path))

        favicon = client.get('/favicon.ico')
        assert favicon.status_code in {200, 302, 304}

        tracked = client.post('/track', data={'tracking_id': 'MIBSMK0001'})
        _assert_ok(tracked)
        assert b'MIBSMK0001' in tracked.data

        json_endpoints = [
            '/health',
            '/api/stats',
            '/api/dashboard/overview',
            '/api/chart/monthly',
            '/api/chart/dept',
            '/api/chart/status',
            '/api/chart/resolution-time',
            '/api/chart/sla-compliance',
            '/api/public/data',
            '/api/geo/heatmap',
        ]
        for path in json_endpoints:
            response = client.get(path)
            _assert_ok(response)
            assert response.is_json

        csv_response = client.get('/api/public/export/monthly.csv')
        _assert_ok(csv_response)
        assert 'text/csv' in csv_response.content_type

        assist = client.post(
            '/api/ai/assist',
            json={'message': 'How do I submit a complaint?', 'assistant': 'homepage'}
        )
        _assert_ok(assist)
        assert assist.get_json()['fallback'] is True

        classify = client.post(
            '/api/ai/classify',
            json={'description': 'Water leakage is damaging the road and needs urgent repair.'}
        )
        _assert_ok(classify)
        assert 'department_id' in classify.get_json()

    def test_admin_pages_after_login(self, client):
        login = client.post(
            '/auth/login',
            data={'username': 'admin', 'password': 'Admin@1234'},
            follow_redirects=False
        )
        assert login.status_code == 302
        assert '/admin/dashboard' in login.location

        admin_pages = [
            ('/admin/dashboard', []),
            ('/admin/complaints', []),
            ('/admin/complaint/MIBSMK0001', []),
            ('/admin/officers', []),
            ('/admin/departments', []),
            ('/admin/escalation-contacts', []),
            ('/admin/audit-logs', [b'verifyProgressBar', b'verifyResultIconContainer', b'Chain Integrity Analysis']),
            ('/auth/profile', []),
        ]
        for path, expected_substrings in admin_pages:
            res = client.get(path)
            _assert_ok(res)
            for substring in expected_substrings:
                assert substring in res.data

        admin_json = [
            '/admin/audit-logs/verify',
            '/admin/api/system-stats',
            '/admin/api/analytics/sentiment',
            '/admin/api/analytics/service-trends',
            '/admin/api/analytics/officer-performance',
        ]
        for path in admin_json:
            response = client.get(path)
            _assert_ok(response)
            assert response.is_json

        export_response = client.get('/admin/export/complaints.csv')
        _assert_ok(export_response)
        assert 'text/csv' in export_response.content_type

        created = client.post('/admin/escalation-contacts/create', data={
            'department_id': 1,
            'level': 2,
            'name': 'Department Head',
            'designation': 'Executive Engineer',
            'email': 'head@civikindia.gov.in',
        }, follow_redirects=True)
        _assert_ok(created)
        assert b'Escalation contact created successfully' in created.data
        assert EscalationContact.query.filter_by(name='Department Head').count() == 1

    def test_officer_pages_after_login(self, client):
        login = client.post(
            '/auth/login',
            data={'username': 'officer_water', 'password': 'Officer@1234'},
            follow_redirects=False
        )
        assert login.status_code == 302
        assert '/officer/dashboard' in login.location

        officer_pages = [
            ('/officer/dashboard', [b'SLA Compliance:']),
            ('/officer/complaint/MIBSMK0002', [
                b'AI-Assisted Triaging Analysis',
                b'Dispatch via WhatsApp',
                b'whatsappDispatchModal'
            ]),
            ('/auth/profile', []),
        ]
        for path, expected_substrings in officer_pages:
            res = client.get(path)
            _assert_ok(res)
            for substring in expected_substrings:
                assert substring in res.data

        complaints_redirect = client.get('/officer/complaints')
        assert complaints_redirect.status_code == 302
        assert '/officer/dashboard' in complaints_redirect.location

        stats_response = client.get('/officer/api/my-stats')
        _assert_ok(stats_response)
        assert stats_response.is_json


def test_bootstrap_creates_default_staff_accounts(app):
    from deploy.bootstrap import ensure_admin, ensure_default_officer, ensure_lookup_data

    with app.app_context():
        AuditLog.query.delete()
        Complaint.query.delete()
        Service.query.delete()
        User.query.delete()
        Department.query.delete()
        db.session.commit()

        ensure_lookup_data()
        ensure_admin()
        ensure_default_officer()
        db.session.commit()

        admin = User.query.filter_by(username='admin').first()
        officer = User.query.filter_by(username='officer_water').first()

        assert admin is not None
        assert admin.check_password('Admin@1234')
        assert officer is not None
        assert officer.check_password('Officer@1234')
        assert officer.department is not None
        assert officer.department.name == 'Water Supply'
