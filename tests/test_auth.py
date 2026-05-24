"""
CivikIndia Authentication Tests
Tests for login, logout, and session management.
"""
import hashlib
import re

import pytest
from app import create_app, db
from app.clock import utc_epoch_seconds
from app.models import User


@pytest.fixture
def app():
    """Create application for testing."""
    app = create_app('testing')
    
    with app.app_context():
        db.create_all()
        # Create test user
        user = User(username='testuser', role='officer')
        user.set_password('testpass123')
        db.session.add(user)
        admin = User(username='adminuser', role='admin')
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


class TestLogin:
    """Tests for login functionality."""
    
    def test_login_page_loads(self, client):
        """Test login page loads."""
        response = client.get('/auth/login')
        assert response.status_code == 200
        assert b'Staff Login' in response.data
    
    def test_login_success(self, client):
        """Test successful login."""
        response = client.post('/auth/login', data={
            'username': 'testuser',
            'password': 'testpass123'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        assert b'Welcome back' in response.data
    
    def test_login_wrong_password(self, client):
        """Test login with wrong password."""
        response = client.post('/auth/login', data={
            'username': 'testuser',
            'password': 'wrongpassword'
        })
        
        assert b'Invalid username or password' in response.data
    
    def test_login_nonexistent_user(self, client):
        """Test login with non-existent user."""
        response = client.post('/auth/login', data={
            'username': 'nonexistent',
            'password': 'somepassword'
        })
        
        assert b'Invalid username or password' in response.data

    def test_login_blocks_external_next_redirect(self, client):
        """Test login does not redirect to external domains."""
        response = client.post(
            '/auth/login?next=https://evil.example/steal',
            data={'username': 'testuser', 'password': 'testpass123'},
            follow_redirects=False
        )
        assert response.status_code == 302
        assert '/officer/dashboard' in response.headers.get('Location', '')

    def test_login_keeps_next_after_failed_attempt(self, client):
        """Next redirect target should remain in the form after validation errors."""
        response = client.post(
            '/auth/login?next=/admin/dashboard',
            data={
                'username': 'testuser',
                'password': 'wrong-pass'
            }
        )
        assert response.status_code == 200
        assert b'name="next" value="/admin/dashboard"' in response.data

    def test_login_locks_after_repeated_failures(self, client):
        """Test brute force lockout after repeated failed attempts."""
        for _ in range(5):
            client.post('/auth/login', data={
                'username': 'testuser',
                'password': 'wrongpassword'
            })

        response = client.post('/auth/login', data={
            'username': 'testuser',
            'password': 'testpass123'
        })
        assert b'temporarily locked' in response.data

    def test_admin_login_redirects_to_otp_when_enabled(self, client, app, monkeypatch):
        """Admin login should require OTP verification when feature is enabled."""
        app.config['ADMIN_EMAIL_2FA_ENABLED'] = True

        monkeypatch.setattr(
            'app.routes.auth.send_system_email',
            lambda subject, body, recipients: (True, None)
        )

        response = client.post('/auth/login', data={
            'username': 'adminuser',
            'password': 'adminpass123'
        }, follow_redirects=False)

        assert response.status_code == 302
        assert '/auth/verify-otp' in response.headers.get('Location', '')


class TestLogout:
    """Tests for logout functionality."""
    
    def test_logout(self, client):
        """Test logout functionality."""
        # Login first
        client.post('/auth/login', data={
            'username': 'testuser',
            'password': 'testpass123'
        })
        
        # Then logout
        response = client.post('/auth/logout', follow_redirects=True)
        assert response.status_code == 200
        assert b'logged out' in response.data

    def test_logout_get_not_allowed(self, client):
        """Logout should not be allowed via GET."""
        response = client.get('/auth/logout')
        assert response.status_code == 405


class TestSessionProtection:
    """Tests for session security."""
    
    def test_protected_route_redirects(self, client):
        """Test protected route redirects when not logged in."""
        response = client.get('/officer/dashboard', follow_redirects=True)
        assert b'Please log in' in response.data
    
    def test_session_persists(self, client):
        """Test session persists across requests."""
        # Login
        client.post('/auth/login', data={
            'username': 'testuser',
            'password': 'testpass123'
        })
        
        # Access protected page
        response = client.get('/officer/dashboard')
        assert response.status_code == 200

    def test_profile_page_loads_after_login(self, client):
        """Test profile page is available to logged-in users."""
        client.post('/auth/login', data={
            'username': 'testuser',
            'password': 'testpass123'
        })

        response = client.get('/auth/profile')
        assert response.status_code == 200
        assert b'My Profile' in response.data

    def test_admin_profile_shows_password_change_form(self, client):
        """Admin profile should show change password form."""
        client.post('/auth/login', data={
            'username': 'adminuser',
            'password': 'adminpass123'
        })

        response = client.get('/auth/profile')
        assert response.status_code == 200
        assert b'Change Password' in response.data

    def test_admin_can_change_password_from_profile(self, client):
        """Admin should be able to change password with current password check."""
        client.post('/auth/login', data={
            'username': 'adminuser',
            'password': 'adminpass123'
        })

        response = client.post('/auth/profile/change-password', data={
            'current_password': 'adminpass123',
            'new_password': 'AdminNew@123',
            'confirm_password': 'AdminNew@123'
        }, follow_redirects=True)

        assert response.status_code == 200
        assert b'Password updated successfully' in response.data

        client.post('/auth/logout')
        relogin = client.post('/auth/login', data={
            'username': 'adminuser',
            'password': 'AdminNew@123'
        }, follow_redirects=True)
        assert relogin.status_code == 200
        assert b'Welcome back' in relogin.data

    def test_admin_password_change_rejects_wrong_current_password(self, client):
        """Admin password change should fail when current password is incorrect."""
        client.post('/auth/login', data={
            'username': 'adminuser',
            'password': 'adminpass123'
        })

        response = client.post('/auth/profile/change-password', data={
            'current_password': 'wrongpass123',
            'new_password': 'adminnew123',
            'confirm_password': 'adminnew123'
        }, follow_redirects=True)

        assert response.status_code == 200
        assert b'Current password is incorrect' in response.data

    def test_officer_can_change_own_password_from_profile(self, client):
        """Officer users should be able to change their own password."""
        client.post('/auth/login', data={
            'username': 'testuser',
            'password': 'testpass123'
        })

        response = client.post('/auth/profile/change-password', data={
            'current_password': 'testpass123',
            'new_password': 'OfficerNew@1234',
            'confirm_password': 'OfficerNew@1234'
        }, follow_redirects=True)

        assert response.status_code == 200
        assert b'Password updated successfully' in response.data

        client.post('/auth/logout')
        relogin = client.post('/auth/login', data={
            'username': 'testuser',
            'password': 'OfficerNew@1234'
        }, follow_redirects=True)
        assert relogin.status_code == 200
        assert b'Welcome back' in relogin.data

    def test_password_change_rejects_weak_password(self, client):
        """Profile password change should enforce the strong password policy."""
        client.post('/auth/login', data={
            'username': 'testuser',
            'password': 'testpass123'
        })

        response = client.post('/auth/profile/change-password', data={
            'current_password': 'testpass123',
            'new_password': 'weakpass123',
            'confirm_password': 'weakpass123'
        }, follow_redirects=True)

        assert response.status_code == 200
        assert b'at least 12 characters' in response.data


class TestPasswordReset:
    """Tests for forgot-password and reset-password flows."""

    def test_forgot_password_sends_hashed_reset_token(self, client, app, monkeypatch):
        """Password reset request stores a digest and emails the raw token link."""
        sent_messages = []

        def fake_send(subject, body, recipients):
            sent_messages.append((subject, body, recipients))
            return True, None

        monkeypatch.setattr('app.routes.auth.send_system_email', fake_send)

        response = client.post('/auth/forgot-password', data={
            'identifier': 'adminuser'
        }, follow_redirects=True)

        assert response.status_code == 200
        assert b'reset instructions have been sent' in response.data
        assert sent_messages

        body = sent_messages[0][1]
        match = re.search(r'/auth/reset-password/([A-Za-z0-9_-]+)', body)
        assert match
        raw_token = match.group(1)

        with app.app_context():
            admin = User.query.filter_by(username='adminuser').first()
            assert admin.reset_token
            assert admin.reset_token != raw_token
            assert admin.reset_token == hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
            assert admin.reset_token_expires_at > utc_epoch_seconds()

    def test_forgot_password_response_is_enumeration_safe(self, client, app, monkeypatch):
        """Unknown accounts receive the same public response and no token is created."""
        monkeypatch.setattr(
            'app.routes.auth.send_system_email',
            lambda subject, body, recipients: (True, None)
        )

        response = client.post('/auth/forgot-password', data={
            'identifier': 'unknown-user'
        }, follow_redirects=True)

        assert response.status_code == 200
        assert b'reset instructions have been sent' in response.data
        with app.app_context():
            assert User.query.filter_by(reset_token=None).count() == User.query.count()

    def test_reset_password_rejects_invalid_or_expired_token(self, client, app):
        """Invalid and expired reset links should not render the password form."""
        invalid = client.get('/auth/reset-password/not-a-real-token', follow_redirects=True)
        assert invalid.status_code == 200
        assert b'invalid or has expired' in invalid.data

        raw_token = 'expired-token'
        with app.app_context():
            admin = User.query.filter_by(username='adminuser').first()
            admin.reset_token = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
            admin.reset_token_expires_at = utc_epoch_seconds() - 1
            db.session.commit()

        expired = client.get(f'/auth/reset-password/{raw_token}', follow_redirects=True)
        assert expired.status_code == 200
        assert b'invalid or has expired' in expired.data

        with app.app_context():
            admin = User.query.filter_by(username='adminuser').first()
            assert admin.reset_token is None
            assert admin.reset_token_expires_at is None

    def test_reset_password_updates_password_and_clears_token(self, client, app):
        """A valid reset token should set the new password and clear reset fields."""
        raw_token = 'valid-reset-token'
        with app.app_context():
            admin = User.query.filter_by(username='adminuser').first()
            admin.reset_token = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
            admin.reset_token_expires_at = utc_epoch_seconds() + 3600
            db.session.commit()

        response = client.post(f'/auth/reset-password/{raw_token}', data={
            'new_password': 'ResetNew@1234',
            'confirm_password': 'ResetNew@1234'
        }, follow_redirects=True)

        assert response.status_code == 200
        assert b'Password reset successfully' in response.data

        with app.app_context():
            admin = User.query.filter_by(username='adminuser').first()
            assert admin.reset_token is None
            assert admin.reset_token_expires_at is None

        relogin = client.post('/auth/login', data={
            'username': 'adminuser',
            'password': 'ResetNew@1234'
        }, follow_redirects=True)
        assert relogin.status_code == 200
        assert b'Welcome back' in relogin.data
