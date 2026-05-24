"""
CivikIndia Admin Review Queue Tests
Tests for admin review, approval, and rejection workflow.
"""
import pytest
import json
from app import create_app, db
from app.models import User, Department, Service, Complaint, AuditLog, SLAPolicy
from app.clock import utc_now

@pytest.fixture
def app():
    """Create application for testing."""
    app = create_app('testing')
    
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
        db.session.add(officer)
        
        # Seed SLA policy for Water Supply + Normal priority
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

def login_admin(client):
    """Helper to login as admin."""
    return client.post('/auth/login', data={
        'username': 'adminuser',
        'password': 'adminpass123'
    }, follow_redirects=True)

class TestAdminReviewFlow:
    """Tests for the Admin Review and Approval Queue."""
    
    def test_admin_inbox_requires_admin_role(self, client):
        """Inbox route should redirect to login or deny non-admins."""
        response = client.get('/admin/inbox', follow_redirects=True)
        assert b'Please log in to access this page' in response.data
        
    def test_admin_inbox_view(self, client):
        """Admin can view the review inbox with pending review complaints."""
        login_admin(client)
        
        # Create a complaint Awaiting Review
        dept = Department.query.first()
        service = Service.query.first()
        complaint = Complaint(
            tracking_id='MIBREV001',
            department_id=dept.id,
            service_id=service.id,
            description='Test review queue complaint description long enough to satisfy constraints.',
            status='Awaiting Review'
        )
        db.session.add(complaint)
        db.session.commit()
        
        response = client.get('/admin/inbox')
        assert response.status_code == 200
        assert b'MIBREV001' in response.data
        assert b'Approve' in response.data
        assert b'Reject' in response.data

    def test_approve_complaint_success(self, client):
        """Admin can approve complaint: sets status Pending, assigns officer, and logs event."""
        login_admin(client)
        
        dept = Department.query.first()
        service = Service.query.first()
        complaint = Complaint(
            tracking_id='MIBREV002',
            department_id=dept.id,
            service_id=service.id,
            description='Another test complaint under administrative review queue.',
            status='Awaiting Review'
        )
        db.session.add(complaint)
        db.session.commit()
        
        response = client.post(f'/admin/complaint/{complaint.id}/approve', data={
            'priority': 'Normal',
            'admin_notes': 'Valid complaint. Forwarding to Water Supply.'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        assert b'approved and dispatched successfully' in response.data
        
        # Verify db changes
        c = Complaint.query.filter_by(tracking_id='MIBREV002').first()
        assert c.status == 'Pending'
        assert c.admin_notes == 'Valid complaint. Forwarding to Water Supply.'
        assert c.assigned_to is not None
        assert c.reviewed_at is not None
        assert c.reviewed_by_id is not None
        assert c.sla_due_at is not None
        
        # Verify audit log
        audit = AuditLog.query.filter_by(action='COMPLAINT_APPROVED').first()
        assert audit is not None
        details = json.loads(audit.details)
        assert details['complaint_id'] == c.id
        assert details['tracking_id'] == 'MIBREV002'

    def test_reject_complaint_invalid_reason_length(self, client):
        """Admin rejection fails if reason is less than 20 characters."""
        login_admin(client)
        
        dept = Department.query.first()
        service = Service.query.first()
        complaint = Complaint(
            tracking_id='MIBREV003',
            department_id=dept.id,
            service_id=service.id,
            description='Test complaint that will fail rejection due to short reason.',
            status='Awaiting Review'
        )
        db.session.add(complaint)
        db.session.commit()
        
        response = client.post(f'/admin/complaint/{complaint.id}/reject', data={
            'rejection_reason': 'Too short',
            'admin_notes': 'Short notes'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        assert b'Rejection reason must be at least 20 characters long' in response.data
        
        # Verify status is unchanged
        c = Complaint.query.filter_by(tracking_id='MIBREV003').first()
        assert c.status == 'Awaiting Review'

    def test_reject_complaint_success(self, client):
        """Admin can reject complaint with valid reason: status becomes Rejected."""
        login_admin(client)
        
        dept = Department.query.first()
        service = Service.query.first()
        complaint = Complaint(
            tracking_id='MIBREV004',
            department_id=dept.id,
            service_id=service.id,
            description='Test complaint for successful administrative rejection.',
            status='Awaiting Review'
        )
        db.session.add(complaint)
        db.session.commit()
        
        response = client.post(f'/admin/complaint/{complaint.id}/reject', data={
            'rejection_reason': 'This submission does not contain a valid municipal integrity complaint.',
            'admin_notes': 'Spam submission rejected by admin.'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        assert b'has been rejected' in response.data
        
        # Verify db changes
        c = Complaint.query.filter_by(tracking_id='MIBREV004').first()
        assert c.status == 'Rejected'
        assert c.rejection_reason == 'This submission does not contain a valid municipal integrity complaint.'
        assert c.admin_notes == 'Spam submission rejected by admin.'
        assert c.reviewed_at is not None
        assert c.reviewed_by_id is not None
        assert c.assigned_to is None
        
        # Verify audit log
        audit = AuditLog.query.filter_by(action='COMPLAINT_REJECTED').first()
        assert audit is not None
        details = json.loads(audit.details)
        assert details['complaint_id'] == c.id
        assert details['tracking_id'] == 'MIBREV004'
        assert details['reason'] == 'This submission does not contain a valid municipal integrity complaint.'
