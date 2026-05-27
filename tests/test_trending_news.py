"""
CivikIndia Trending News Ticker Tests
Tests for Trending News CRUD operations, public visibility, and audit logging.
"""
import pytest
import json
from app import create_app, db
from app.models import User, TrendingNews, AuditLog

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

class TestTrendingNewsFlow:
    """Tests for Trending News ticker admin management and public visibility."""

    def test_routes_require_admin_role(self, client):
        """Management routes should redirect or deny access for anonymous users."""
        response = client.get('/admin/trending-news', follow_redirects=True)
        assert b'Please log in to access this page' in response.data

        response = client.post('/admin/trending-news/create', data={
            'headline': 'Unauthorized news article alert'
        }, follow_redirects=True)
        assert b'Please log in to access this page' in response.data

    def test_admin_trending_news_crud(self, client, app):
        """Admin can create, edit, toggle, and delete trending news items successfully."""
        login_admin(client)

        # 1. Create News Item
        response = client.post('/admin/trending-news/create', data={
            'headline': 'Breaking: New Complaint Portal Live in Maharashtra!',
            'badge_label': 'BREAKING',
            'display_order': 5,
            'link_url': 'https://civikindia.online/dashboard'
        }, follow_redirects=True)

        assert response.status_code == 200
        assert b'Trending news item created successfully' in response.data

        with app.app_context():
            item = TrendingNews.query.filter_by(badge_label='BREAKING').first()
            assert item is not None
            assert item.headline == 'Breaking: New Complaint Portal Live in Maharashtra!'
            assert item.display_order == 5
            assert item.link_url == 'https://civikindia.online/dashboard'
            assert item.is_active is True

            # Verify audit log creation
            audit = AuditLog.query.filter_by(action='TRENDING_NEWS_CREATED').first()
            assert audit is not None
            details = json.loads(audit.details)
            assert details['id'] == item.id
            assert details['headline'] == item.headline

        # 2. Toggle News Item (Deactivate)
        response = client.post(f'/admin/trending-news/{item.id}/toggle', follow_redirects=True)
        assert response.status_code == 200
        assert b'News item deactivated' in response.data

        with app.app_context():
            item = db.session.get(TrendingNews, item.id)
            assert item.is_active is False

            # Verify audit log toggle
            audit = AuditLog.query.filter_by(action='TRENDING_NEWS_TOGGLED').first()
            assert audit is not None
            details = json.loads(audit.details)
            assert details['id'] == item.id
            assert details['is_active'] is False

        # 3. Edit News Item
        response = client.post(f'/admin/trending-news/{item.id}/edit', data={
            'headline': 'Updated Headline for Portal Launch',
            'badge_label': 'UPDATE',
            'display_order': 2,
            'link_url': 'https://civikindia.online/about'
        }, follow_redirects=True)

        assert response.status_code == 200
        assert b'News item updated' in response.data

        with app.app_context():
            item = db.session.get(TrendingNews, item.id)
            assert item.headline == 'Updated Headline for Portal Launch'
            assert item.badge_label == 'UPDATE'
            assert item.display_order == 2
            assert item.link_url == 'https://civikindia.online/about'

            # Verify audit log update
            audit = AuditLog.query.filter_by(action='TRENDING_NEWS_UPDATED').first()
            assert audit is not None
            details = json.loads(audit.details)
            assert details['id'] == item.id
            assert details['new_headline'] == 'Updated Headline for Portal Launch'

        # 4. Delete News Item
        response = client.post(f'/admin/trending-news/{item.id}/delete', follow_redirects=True)
        assert response.status_code == 200
        assert b'News item deleted' in response.data

        with app.app_context():
            item_deleted = db.session.get(TrendingNews, item.id)
            assert item_deleted is None

            # Verify audit log delete
            audit = AuditLog.query.filter_by(action='TRENDING_NEWS_DELETED').first()
            assert audit is not None
            details = json.loads(audit.details)
            assert details['id'] == item.id
            assert details['headline'] == 'Updated Headline for Portal Launch'

    def test_public_trending_news_visibility(self, client, app):
        """Active news items appear in public views, inactive ones are hidden."""
        with app.app_context():
            # Seed an active news item
            item_active = TrendingNews(
                headline='Active Public Headline Alert',
                badge_label='ALERT',
                is_active=True,
                display_order=1
            )
            # Seed an inactive news item
            item_inactive = TrendingNews(
                headline='Hidden Inactive Draft Headline',
                badge_label='DRAFT',
                is_active=False,
                display_order=2
            )
            db.session.add_all([item_active, item_inactive])
            db.session.commit()

        # Check public JSON API endpoint
        response = client.get('/admin/api/trending-news')
        assert response.status_code == 200
        data = response.get_json()
        headlines = [x['headline'] for x in data]
        assert 'Active Public Headline Alert' in headlines
        assert 'Hidden Inactive Draft Headline' not in headlines

        # Check public homepage rendering
        response = client.get('/')
        assert response.status_code == 200
        assert b'Active Public Headline Alert' in response.data
        assert b'Hidden Inactive Draft Headline' not in response.data

        # Check public dashboard rendering
        response = client.get('/dashboard')
        assert response.status_code == 200
        assert b'Active Public Headline Alert' in response.data
        assert b'Hidden Inactive Draft Headline' not in response.data
