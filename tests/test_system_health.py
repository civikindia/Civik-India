"""
CivikIndia System Health Page Tests
"""
import pytest
import json
from app import create_app, db
from app.models import User, AuditLog

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

class TestSystemHealthFlow:
    """Tests for System Health dashboard and API summary."""

    def test_unauthorized_health_page_blocked(self, client):
        """Anonymous users must be blocked from system health dashboard and API."""
        # 1. Page access -> redirect to login
        response = client.get('/admin/health', follow_redirects=True)
        assert b'Please log in to access this page' in response.data or response.status_code == 302

        # 2. Summary API access -> redirect/blocked
        response = client.get('/admin/api/health-summary', follow_redirects=True)
        assert b'Please log in to access this page' in response.data or response.status_code == 302

        # 3. Check API access -> redirect/blocked
        response = client.get('/admin/api/health-check/database', follow_redirects=True)
        assert b'Please log in to access this page' in response.data or response.status_code == 302

    def test_authorized_admin_health_page_success(self, client):
        """Admins can view health dashboard page shell."""
        # Log in as admin
        admin = User.query.filter_by(role='admin').first()
        with client.session_transaction() as sess:
            sess['user_id'] = admin.id
            sess['username'] = admin.username
            sess['role'] = admin.role

        response = client.get('/admin/health')
        assert response.status_code == 200
        assert b'System Health' in response.data
        assert b'Database' in response.data
        assert b'Redis' in response.data
        assert b'Celery Workers' in response.data
        assert b'Object Storage' in response.data
        assert b'Email / SMTP' in response.data
        assert b'Disk / Storage' in response.data
        assert b'Complaint Pipeline' in response.data
        assert b'Environment Variables' in response.data

        # Audit log verification
        audit = AuditLog.query.filter_by(action='SYSTEM_HEALTH_VIEWED').first()
        assert audit is not None
        details = json.loads(audit.details)
        assert details.get('mode') == 'asynchronous'

    def test_authorized_admin_health_summary_api_success(self, client):
        """Admins can query the lightweight health summary endpoint."""
        admin = User.query.filter_by(role='admin').first()
        with client.session_transaction() as sess:
            sess['user_id'] = admin.id
            sess['username'] = admin.username
            sess['role'] = admin.role

        response = client.get('/admin/api/health-summary')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'db' in data
        assert 'redis' in data
        assert 'awaiting_review' in data
        assert 'delayed' in data
        assert 'total' in data
        assert 'checked_at' in data
        assert 'response_ms' in data

    def test_authorized_admin_health_check_api_success(self, client):
        """Admins can query individual diagnostics endpoints."""
        admin = User.query.filter_by(role='admin').first()
        with client.session_transaction() as sess:
            sess['user_id'] = admin.id
            sess['username'] = admin.username
            sess['role'] = admin.role

        # 1. Database check
        response = client.get('/admin/api/health-check/database')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'status' in data
        assert 'latency_ms' in data

        # 2. Redis check
        response = client.get('/admin/api/health-check/redis')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'status' in data

        # 3. Invalid component
        response = client.get('/admin/api/health-check/invalid')
        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'error' in data
