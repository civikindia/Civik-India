"""
Tests for Print-Optimized Tracking Receipt (HTML and PDF).
"""
import pytest
from app import create_app, db
from app.models import Department, Service, Complaint


@pytest.fixture
def app():
    """Create application for testing receipts."""
    app = create_app('testing')
    app.config.update(
        SERVER_NAME='civikindia.test',
        APPLICATION_ROOT='/',
        PREFERRED_URL_SCHEME='http'
    )
    
    with app.app_context():
        db.create_all()
        
        dept = Department(name='Water Supply', description='Water department')
        db.session.add(dept)
        db.session.commit()
        
        service = Service(name='Pipeline Leak', department_id=dept.id)
        db.session.add(service)
        db.session.commit()
        
        complaint = Complaint(
            tracking_id='CIVIK/2026/05/TEST1234',
            department_id=dept.id,
            service_id=service.id,
            description='Test complaint for receipt verification.',
            status='Awaiting Review'
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


def test_public_html_receipt_route(client, app):
    """Test the /receipt/<tracking_id> HTML page generation."""
    # Test valid tracking ID
    response = client.get('/receipt/CIVIK/2026/05/TEST1234')
    assert response.status_code == 200
    assert b'Receipt' in response.data
    assert b'CIVIK/2026/05/TEST1234' in response.data
    assert b'Water Supply' in response.data
    assert b'Pipeline Leak' in response.data
    assert b'Government Helplines' in response.data
    assert b'1064' in response.data
    assert b'data:image/png;base64,' in response.data  # Base64 QR code

    # Test non-existent tracking ID returns 404
    response_404 = client.get('/receipt/CIVIK/2026/05/NONEXIST')
    assert response_404.status_code == 404

    # Test invalid format tracking ID returns 404
    response_invalid = client.get('/receipt/invalid-id-format')
    assert response_invalid.status_code == 404


def test_pdf_receipt_download(client, app):
    """Test the /confirmation/<tracking_id>/receipt.pdf generation."""
    response = client.get('/confirmation/CIVIK/2026/05/TEST1234/receipt.pdf')
    assert response.status_code == 200
    assert response.headers['Content-Disposition'].startswith('attachment; filename=Civik_India_Receipt_')
    assert response.content_type == 'application/pdf'
    assert response.data.startswith(b'%PDF')  # PDF signature
