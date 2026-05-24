"""
Officer dashboard tests.
"""
from datetime import timedelta

import pytest

from app import create_app, db
from app.clock import utc_now
from app.models import Complaint, Department, Service, User


@pytest.fixture
def app():
    app = create_app('testing')
    with app.app_context():
        db.create_all()

        department = Department(name='Water Supply')
        db.session.add(department)
        db.session.flush()
        service = Service(name='Pipeline Repair', department_id=department.id, sla_days=7)
        officer = User(
            username='officer',
            email='officer@example.com',
            role='officer',
            department_id=department.id,
            is_active=True
        )
        officer.set_password('officerpass123')
        db.session.add_all([service, officer])
        db.session.flush()

        now = utc_now()
        for index in range(14):
            db.session.add(Complaint(
                tracking_id=f'MIBPAGE{index:02d}',
                department_id=department.id,
                service_id=service.id,
                description='Pagination test complaint with enough detail for officer dashboard rendering.',
                status='Pending',
                assigned_to=officer.id,
                priority='High' if index == 0 else 'Normal',
                submitted_at=now - timedelta(minutes=index),
                sla_due_at=now + timedelta(days=2)
            ))

        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def login_officer(client):
    return client.post('/auth/login', data={
        'username': 'officer',
        'password': 'officerpass123'
    })


def test_officer_dashboard_paginates_assigned_complaints(client):
    login_officer(client)

    first_page = client.get('/officer/dashboard')
    assert first_page.status_code == 200
    assert b'MIBPAGE00' in first_page.data
    assert b'MIBPAGE09' in first_page.data
    assert b'MIBPAGE10' not in first_page.data
    assert b'Showing 1-10 of 14' in first_page.data
    assert b'data-count="14"' in first_page.data

    second_page = client.get('/officer/dashboard?page=2')
    assert second_page.status_code == 200
    assert b'MIBPAGE10' in second_page.data
    assert b'MIBPAGE13' in second_page.data
    assert b'Showing 11-14 of 14' in second_page.data


def test_officer_dashboard_priority_queue_remains_bounded(client):
    login_officer(client)

    response = client.get('/officer/dashboard')
    assert response.status_code == 200
    assert b'Priority Queue' in response.data
    assert b'MIBPAGE00' in response.data
