"""
CivikIndia Announcements Notice Board Tests
Tests for Announcement CRUD operations, public notices board, homepage widget, and audit logging.
"""
import pytest
import json
from datetime import datetime, timedelta
from app import create_app, db
from app.models import User, Announcement, AuditLog
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

def login_admin(client):
    """Helper to login as admin."""
    return client.post('/auth/login', data={
        'username': 'adminuser',
        'password': 'adminpass123'
    }, follow_redirects=True)

class TestAnnouncementsFlow:
    """Tests for Announcements Notice Board admin management and public visibility."""

    def test_routes_require_admin_role(self, client):
        """Management routes should redirect or deny access for anonymous users."""
        response = client.get('/admin/announcements', follow_redirects=True)
        assert b'Please log in to access this page' in response.data

        response = client.post('/admin/announcements/new', data={
            'title': 'Unauthorized announcement title alert',
            'body': 'Some body text of announcement.'
        }, follow_redirects=True)
        assert b'Please log in to access this page' in response.data

    def test_admin_announcements_crud(self, client, app):
        """Admin can create, edit, toggle, pin, and delete announcements successfully."""
        login_admin(client)

        # 1. Create Announcement
        response = client.post('/admin/announcements/new', data={
            'title': 'System maintenance this Friday night',
            'body': 'We will be conducting critical server updates starting at 10 PM IST on Friday.',
            'category': 'Maintenance',
            'priority': 'warning',
            'is_pinned': '1',
            'show_on_home': '1'
        }, follow_redirects=True)

        assert response.status_code == 200
        assert b'created' in response.data

        with app.app_context():
            item = Announcement.query.filter_by(category='Maintenance').first()
            assert item is not None
            assert item.title == 'System maintenance this Friday night'
            assert item.priority == 'warning'
            assert item.is_pinned is True
            assert item.show_on_home is True
            assert item.is_active is True

            # Verify audit log creation
            audit = AuditLog.query.filter_by(action='ANNOUNCEMENT_CREATED').first()
            assert audit is not None
            details = json.loads(audit.details)
            assert details['id'] == item.id
            assert details['title'] == item.title

        # 2. Toggle Announcement (Deactivate)
        response = client.post(f'/admin/announcements/{item.id}/toggle', follow_redirects=True)
        assert response.status_code == 200
        assert b'deactivated' in response.data

        with app.app_context():
            item = db.session.get(Announcement, item.id)
            assert item.is_active is False

            # Verify audit log toggle
            audit = AuditLog.query.filter_by(action='ANNOUNCEMENT_TOGGLED').first()
            assert audit is not None
            details = json.loads(audit.details)
            assert details['id'] == item.id
            assert details['is_active'] is False

        # 3. Pin/Unpin Announcement (Toggle pin)
        response = client.post(f'/admin/announcements/{item.id}/pin', follow_redirects=True)
        assert response.status_code == 200
        assert b'unpinned' in response.data

        with app.app_context():
            item = db.session.get(Announcement, item.id)
            assert item.is_pinned is False

            # Verify audit log pin
            audit = AuditLog.query.filter_by(action='ANNOUNCEMENT_PINNED').first()
            assert audit is not None
            details = json.loads(audit.details)
            assert details['id'] == item.id
            assert details['is_pinned'] is False

        # 4. Edit Announcement
        response = client.post(f'/admin/announcements/{item.id}/edit', data={
            'title': 'New Updated Title for Maintenance',
            'body': 'This is the new body text containing at least ten characters.',
            'category': 'General',
            'priority': 'info',
            'is_pinned': '1',
            'show_on_home': '1',
            'is_active': '1'
        }, follow_redirects=True)

        assert response.status_code == 200
        assert b'updated' in response.data

        with app.app_context():
            item = db.session.get(Announcement, item.id)
            assert item.title == 'New Updated Title for Maintenance'
            assert item.category == 'General'
            assert item.priority == 'info'
            assert item.is_pinned is True
            assert item.is_active is True

            # Verify audit log update
            audit = AuditLog.query.filter_by(action='ANNOUNCEMENT_UPDATED').first()
            assert audit is not None
            details = json.loads(audit.details)
            assert details['id'] == item.id
            assert details['title'] == 'New Updated Title for Maintenance'

        # 5. Delete Announcement
        response = client.post(f'/admin/announcements/{item.id}/delete', follow_redirects=True)
        assert response.status_code == 200
        assert b'deleted' in response.data

        with app.app_context():
            item_deleted = db.session.get(Announcement, item.id)
            assert item_deleted is None

            # Verify audit log delete
            audit = AuditLog.query.filter_by(action='ANNOUNCEMENT_DELETED').first()
            assert audit is not None
            details = json.loads(audit.details)
            assert details['id'] == item.id
            assert details['title'] == 'New Updated Title for Maintenance'

    def test_public_announcements_visibility(self, client, app):
        """Active non-expired notices appear in notice board, scheduled/expired/inactive ones do not."""
        now = utc_now()
        
        with app.app_context():
            # 1. Active notice
            n_active = Announcement(
                title='Active Notice Title',
                body='This notice is fully active and visible to public.',
                category='Alert',
                priority='critical',
                is_active=True,
                published_at=now - timedelta(hours=1)
            )
            # 2. Inactive notice
            n_inactive = Announcement(
                title='Inactive Hidden Notice',
                body='This notice is inactive and should not show up.',
                category='General',
                is_active=False
            )
            # 3. Scheduled future notice
            n_scheduled = Announcement(
                title='Future Scheduled Notice',
                body='This notice will publish in the future and should not show up.',
                category='Policy Update',
                is_active=True,
                published_at=now + timedelta(days=2)
            )
            # 4. Expired notice
            n_expired = Announcement(
                title='Expired Past Notice',
                body='This notice has expired and should not show up.',
                category='Event',
                is_active=True,
                published_at=now - timedelta(days=5),
                expires_at=now - timedelta(hours=1)
            )
            db.session.add_all([n_active, n_inactive, n_scheduled, n_expired])
            db.session.commit()

        # Check public Notice Board rendering
        response = client.get('/notices')
        assert response.status_code == 200
        assert b'Active Notice Title' in response.data
        assert b'Inactive Hidden Notice' not in response.data
        assert b'Future Scheduled Notice' not in response.data
        assert b'Expired Past Notice' not in response.data

    def test_category_filtering(self, client, app):
        """Notices can be filtered by category on the notice board."""
        now = utc_now()
        with app.app_context():
            n1 = Announcement(
                title='Alert Announcement',
                body='This is an alert announcement.',
                category='Alert',
                is_active=True,
                published_at=now - timedelta(hours=1)
            )
            n2 = Announcement(
                title='Helpline Announcement',
                body='This is a helpline announcement.',
                category='Helpline',
                is_active=True,
                published_at=now - timedelta(hours=1)
            )
            db.session.add_all([n1, n2])
            db.session.commit()

        # Get all
        response = client.get('/notices')
        assert b'Alert Announcement' in response.data
        assert b'Helpline Announcement' in response.data

        # Filter by Alert
        response = client.get('/notices?category=Alert')
        assert b'Alert Announcement' in response.data
        assert b'Helpline Announcement' not in response.data

        # Filter by Helpline
        response = client.get('/notices?category=Helpline')
        assert b'Alert Announcement' not in response.data
        assert b'Helpline Announcement' in response.data

    def test_homepage_widget(self, client, app):
        """Homepage widget displays only active show_on_home notices up to limit 3."""
        now = utc_now()
        with app.app_context():
            n1 = Announcement(
                title='Home Announcement 1',
                body='Visible on home page notice widget.',
                is_active=True,
                show_on_home=True,
                published_at=now - timedelta(minutes=5)
            )
            n2 = Announcement(
                title='Home Announcement 2',
                body='Visible on home page notice widget.',
                is_active=True,
                show_on_home=True,
                published_at=now - timedelta(minutes=10)
            )
            n3 = Announcement(
                title='Notice Board Only',
                body='Only visible on notice board, not homepage widget.',
                is_active=True,
                show_on_home=False,
                published_at=now - timedelta(minutes=15)
            )
            db.session.add_all([n1, n2, n3])
            db.session.commit()

        response = client.get('/')
        assert response.status_code == 200
        assert b'Home Announcement 1' in response.data
        assert b'Home Announcement 2' in response.data
        assert b'Notice Board Only' not in response.data
