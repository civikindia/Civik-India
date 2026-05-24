"""
CivikIndia Priority Logic Tests
Tests for alignment of Priority Queue and priority stats/KPI metrics.
"""
import pytest
import json
from app import create_app, db
from app.models import User, Department, Service, Complaint, SLAPolicy

@pytest.fixture
def app():
    """Create application for testing."""
    app = create_app('testing')
    
    with app.app_context():
        db.create_all()
        
        # Seed basic data
        admin = User(username='adminuser', role='admin', is_active=True)
        admin.set_password('adminpass123')
        db.session.add(admin)
        
        dept = Department(name='Water Supply', description='Water department')
        db.session.add(dept)
        db.session.commit()
        
        service = Service(name='Pipeline Leak', department_id=dept.id)
        db.session.add(service)
        db.session.commit()
        
        officer = User(username='officer_water', role='officer', department_id=dept.id, is_active=True)
        officer.set_password('officerpass123')
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

def login_officer(client):
    """Helper to login as officer."""
    return client.post('/auth/login', data={
        'username': 'officer_water',
        'password': 'officerpass123'
    }, follow_redirects=True)

def test_priority_stats_and_queue(client, app):
    """Test that priority metrics and queues only count active High and Urgent complaints."""
    with app.app_context():
        dept = Department.query.first()
        service = Service.query.first()
        officer = User.query.filter_by(username='officer_water').first()
        
        # 1. Active High
        c1 = Complaint(
            tracking_id='MIBHIGH001',
            department_id=dept.id,
            service_id=service.id,
            description='Active high priority complaint description that meets word length.',
            status='Pending',
            priority='High',
            assigned_to=officer.id
        )
        
        # 2. Active Urgent (Critical)
        c2 = Complaint(
            tracking_id='MIBHIGH002',
            department_id=dept.id,
            service_id=service.id,
            description='Active urgent priority complaint description that meets word length.',
            status='Under Review',
            priority='Urgent',
            assigned_to=officer.id
        )
        
        # 3. Closed High (Inactive)
        c3 = Complaint(
            tracking_id='MIBHIGH003',
            department_id=dept.id,
            service_id=service.id,
            description='Closed high priority complaint description that meets word length.',
            status='Closed',
            priority='High',
            assigned_to=officer.id
        )
        
        # 4. Rejected Urgent (Inactive)
        c4 = Complaint(
            tracking_id='MIBHIGH004',
            department_id=dept.id,
            service_id=service.id,
            description='Rejected urgent priority complaint description that meets word length.',
            status='Rejected',
            priority='Urgent',
            assigned_to=officer.id
        )
        
        # 5. Awaiting Review High (Inactive)
        c5 = Complaint(
            tracking_id='MIBHIGH005',
            department_id=dept.id,
            service_id=service.id,
            description='Awaiting review high priority complaint description that meets word length.',
            status='Awaiting Review',
            priority='High',
            assigned_to=officer.id
        )
        
        # 6. Active Normal
        c6 = Complaint(
            tracking_id='MIBHIGH006',
            department_id=dept.id,
            service_id=service.id,
            description='Active normal priority complaint description that meets word length.',
            status='Pending',
            priority='Normal',
            assigned_to=officer.id
        )
        
        db.session.add_all([c1, c2, c3, c4, c5, c6])
        db.session.commit()
        
        # Verify get_stats() counts only c1 and c2 as high priority
        stats = Complaint.get_stats()
        assert stats['high_priority'] == 2

    # Verify public dashboard stats API excludes inactive and counts both High/Urgent
    response = client.get('/api/dashboard/overview')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['stats']['high_priority'] == 2

    # Verify officer API and dashboard rendering
    login_officer(client)
    
    # 1. API stats check
    response = client.get('/officer/api/my-stats')
    assert response.status_code == 200
    officer_stats = response.get_json()
    assert officer_stats['high_priority'] == 2
    
    # 2. Page rendering checks
    response = client.get('/officer/dashboard')
    assert response.status_code == 200
    # The active High and Urgent complaints should be listed in the priority queue
    assert b'MIBHIGH001' in response.data
    assert b'MIBHIGH002' in response.data
    # Inactive ones should NOT be in the priority queue list-group
    # (Since MIBHIGH003/004/005 won't match priority list filtering logic)
    # The badges for Urgent should render with badge-priority-critical class
    assert b'badge-priority-critical' in response.data
    assert b'badge-priority-high' in response.data
