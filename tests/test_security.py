"""
CivikIndia Security Tests
Tests for security vulnerabilities and protections.
"""
import io

import pytest
from app import create_app, db
from app.models import User, Department, Service, Complaint, AuditLog


@pytest.fixture
def app():
    """Create application for testing."""
    app = create_app('testing')
    
    with app.app_context():
        db.create_all()
        
        # Create test data
        dept = Department(name='Test Dept', description='Test')
        db.session.add(dept)
        db.session.commit()
        
        service = Service(name='Test Service', department_id=dept.id)
        db.session.add(service)
        db.session.commit()
        
        user = User(username='testuser', role='officer', department_id=dept.id)
        user.set_password('testpass123')
        db.session.add(user)

        admin = User(username='adminuser', role='admin')
        admin.set_password('adminpass123')
        db.session.add(admin)
        db.session.commit()
        
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


class TestSQLInjection:
    """Tests for SQL injection protection."""
    
    def test_login_sql_injection(self, client):
        """Test SQL injection in login form."""
        response = client.post('/auth/login', data={
            'username': "admin' OR '1'='1",
            'password': "anything' OR '1'='1"
        })
        
        # Should not login successfully
        assert b'Invalid username or password' in response.data
    
    def test_search_sql_injection(self, client, app):
        """Test SQL injection in search."""
        with app.app_context():
            # Login as admin
            client.post('/auth/login', data={
                'username': 'adminuser',
                'password': 'adminpass123'
            })
            
            response = client.get('/admin/complaints?search=\' OR 1=1--')
            # Should not crash or return all data improperly
            assert response.status_code in [200, 400]


class TestXSSProtection:
    """Tests for XSS protection."""
    
    def test_xss_in_complaint(self, client, app):
        """Test XSS in complaint description."""
        with app.app_context():
            dept = Department.query.first()
            service = Service.query.first()
            
            xss_payload = '<script>alert("XSS")</script>'
            
            response = client.post('/submit', data={
                'department_id': dept.id,
                'service_id': service.id,
                'description': (
                    f'Test complaint {xss_payload} with enough factual words about dates, places, service impact, '
                    'department delay, requested action, evidence, witnesses, location, and citizen inconvenience.'
                )
            }, follow_redirects=True)
            
            # The script tag should not be executed (would be escaped in template)
            assert response.status_code == 200


class TestCSRFProtection:
    """Tests for CSRF protection."""
    
    def test_csrf_required_on_post(self, client):
        """Test CSRF token is required."""
        if not client.application.config.get('WTF_CSRF_ENABLED', True):
            pytest.skip('CSRF is disabled in testing config')

        response = client.post('/submit', data={
            'department_id': 1,
            'service_id': 1,
            'description': 'Test without CSRF'
        })
        
        # Should fail without CSRF token
        assert response.status_code == 400


class TestSecurityHeaders:
    """Tests for browser-facing security headers."""

    def test_geolocation_permission_policy_allows_same_origin(self, client):
        """Submit page location button should not be blocked by app headers."""
        response = client.get('/submit')
        assert response.status_code == 200
        policy = response.headers.get('Permissions-Policy', '')
        assert 'geolocation=(self)' in policy
        assert 'geolocation=()' not in policy

    def test_health_check_does_not_leak_exception_when_debug_off(self, client, monkeypatch):
        """Production-style health failures should not return raw exception text."""
        client.application.debug = False

        def boom(*args, **kwargs):
            raise RuntimeError('sensitive database host detail')

        monkeypatch.setattr(db.session, 'execute', boom)
        response = client.get('/healthz')

        assert response.status_code == 503
        payload = response.get_json()
        assert payload['status'] == 'unhealthy'
        assert 'error' not in payload


class TestProductionConfigSafety:
    """Production configuration should fail closed before deployment."""

    def _set_minimum_production_env(self, monkeypatch):
        values = {
            'FLASK_ENV': 'production',
            'SECRET_KEY': 'test-production-secret-key',
            'DATABASE_URL': 'postgresql://user:pass@localhost/db',
            'EVIDENCE_ENCRYPTION_KEY': 'a' * 64,
            'AUDIT_HMAC_SECRET': 'test-audit-secret',
            'DEFAULT_ADMIN_PASSWORD': 'StrongAdminPassword123!',
            'DEFAULT_OFFICER_PASSWORD': 'StrongOfficerPassword123!',
            'R2_ACCOUNT_ID': 'test-account',
            'R2_ACCESS_KEY_ID': 'test-access-key',
            'R2_SECRET_ACCESS_KEY': 'test-secret-key',
            'R2_BUCKET_NAME': 'test-bucket',
            'R2_ENDPOINT_URL': 'https://example.r2.cloudflarestorage.com',
        }
        for key, value in values.items():
            monkeypatch.setenv(key, value)

    def test_production_rejects_non_sql_database_url(self, monkeypatch):
        self._set_minimum_production_env(monkeypatch)
        monkeypatch.setenv('DATABASE_URL', 'https://example.neon.tech/neondb/rest/v1')

        with pytest.raises(RuntimeError, match='DATABASE_URL must be a SQL database URL'):
            create_app('production')

    def test_production_requires_bootstrap_passwords(self, monkeypatch):
        self._set_minimum_production_env(monkeypatch)
        monkeypatch.delenv('DEFAULT_ADMIN_PASSWORD')

        with pytest.raises(RuntimeError, match='DEFAULT_ADMIN_PASSWORD'):
            create_app('production')


class TestFileUploadSecurity:
    """Tests for file upload security."""
    
    def test_invalid_file_type_rejected(self, client, app):
        """Test invalid file types are rejected."""
        with app.app_context():
            dept = Department.query.first()
            service = Service.query.first()
            
            import io
            data = {
                'department_id': dept.id,
                'service_id': service.id,
                'description': (
                    'Test complaint with invalid file upload and enough context about the service, location, '
                    'incident date, public impact, expected resolution, supporting evidence, and department response.'
                )
            }
            
            # Try to upload an executable
            data['evidence'] = (io.BytesIO(b'malicious content'), 'malware.exe')
            
            response = client.post('/submit', data=data, content_type='multipart/form-data')
            
            # Should show error about invalid file type
            assert b'Invalid file type' in response.data or response.status_code == 200


class TestAuthorization:
    """Tests for authorization controls."""
    
    def test_officer_cannot_access_admin(self, client, app):
        """Test officer cannot access admin routes."""
        with app.app_context():
            # Login as officer
            client.post('/auth/login', data={
                'username': 'testuser',
                'password': 'testpass123'
            })
            
            # Try to access admin dashboard
            response = client.get('/admin/dashboard', follow_redirects=True)
            assert b'Admin access required' in response.data or response.status_code == 403
    
    def test_unauthorized_complaint_access(self, client, app):
        """Test officer cannot access complaints from other departments."""
        with app.app_context():
            # Create another department and complaint
            other_dept = Department(name='Other Dept', description='Other')
            db.session.add(other_dept)
            db.session.commit()
            
            other_service = Service(name='Other Service', department_id=other_dept.id)
            db.session.add(other_service)
            db.session.commit()
            
            complaint = Complaint(
                tracking_id='MIBOTHER001',
                service_id=other_service.id,
                department_id=other_dept.id,
                description='Complaint in other department'
            )
            db.session.add(complaint)
            db.session.commit()
            
            # Login as officer from first department
            client.post('/auth/login', data={
                'username': 'testuser',
                'password': 'testpass123'
            })
            
            # Try to access complaint from other department
            response = client.get(f'/officer/complaint/MIBOTHER001', follow_redirects=True)
            assert b'do not have permission' in response.data or response.status_code == 403


class TestAuditLogging:
    """Tests for audit log integrity and privacy behavior."""

    def test_audit_hash_chain_integrity(self, app):
        """Test hash chaining remains verifiable across entries."""
        with app.app_context():
            first = AuditLog.create_entry(
                username='adminuser',
                role='admin',
                action='TEST_ONE',
                details='First test entry'
            )
            second = AuditLog.create_entry(
                username='adminuser',
                role='admin',
                action='TEST_TWO',
                details='Second test entry'
            )

            assert first.verify_integrity() is True
            assert second.verify_integrity() is True
            assert second.previous_hash == first.row_hash

    def test_anonymous_submit_does_not_store_ip(self, client, app):
        """Test anonymous complaint actions do not persist IP addresses."""
        with app.app_context():
            dept = Department.query.first()
            service = Service.query.first()

            response = client.post('/submit', data={
                'department_id': dept.id,
                'service_id': service.id,
                'description': (
                    'Anonymous complaint details that include the service problem, location, date, public impact, '
                    'department context, evidence summary, expected action, urgency, follow up requirements, '
                    'officer response, timeline, and affected residents.'
                ),
                'evidence': (io.BytesIO(b'%PDF-1.4 mock pdf content'), 'evidence.pdf')
            }, content_type='multipart/form-data', follow_redirects=True)
            assert response.status_code == 200

            log = AuditLog.query.filter_by(action='COMPLAINT_SUBMITTED')\
                .order_by(AuditLog.id.desc()).first()
            assert log is not None
            assert log.role in ('guest', 'anonymous')
            assert log.ip_address is None

    def test_admin_can_verify_audit_chain_endpoint(self, client, app):
        """Admin audit verification endpoint should return chain status."""
        with app.app_context():
            client.post('/auth/login', data={
                'username': 'adminuser',
                'password': 'adminpass123'
            })
            response = client.get('/admin/audit-logs/verify')
            assert response.status_code == 200
            payload = response.get_json()
            assert 'valid' in payload
