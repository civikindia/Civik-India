"""
CivikIndia Public Transparency Report Page Tests
"""
import pytest
from app import create_app, db
from app.models import Department, Service, Complaint

@pytest.fixture
def app():
    """Create application for testing."""
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        # Seed basic department
        dept = Department(name='Water Supply')
        db.session.add(dept)
        db.session.commit()
        
        # Seed basic service
        service = Service(name='Pipe Leakage Correction', department_id=dept.id)
        db.session.add(service)
        db.session.commit()
        
        # Seed a complaint under Water Supply and Service
        complaint = Complaint(
            tracking_id='MIB-12345678',
            description='Water pipe leakage on main road.',
            status='Pending',
            priority='Normal',
            department_id=dept.id,
            service_id=service.id
        )
        db.session.add(complaint)
        db.session.commit()
        
        yield app
        db.session.remove()
        db.drop_all()

@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()

class TestPublicStatsFlow:
    """Tests for public transparency /stats page."""

    def test_public_stats_page_success(self, client):
        """Verify the stats page resolves and renders correctly."""
        response = client.get('/stats')
        assert response.status_code == 200
        assert b'Transparency Report' in response.data or b'transparency' in response.data.lower()
        assert b'Total Complaints' in response.data
        assert b'Water Supply' in response.data
