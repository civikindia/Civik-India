"""
Internationalization tests for the Phase 1.1 multilingual foundation.
"""
import pytest

from app import create_app, db
from app.models import Department, Service


@pytest.fixture
def app():
    app = create_app('testing')

    with app.app_context():
        db.create_all()
        department = Department(name='Water Supply', description='Water department')
        db.session.add(department)
        db.session.flush()
        db.session.add(Service(name='Pipeline Leakage', department_id=department.id))
        db.session.commit()

        yield app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def test_language_switch_persists_hindi_locale(client):
    response = client.get('/language/hi?next=/', follow_redirects=True)

    assert response.status_code == 200
    assert 'lang="hi"' in response.get_data(as_text=True)
    assert 'भ्रष्टाचार की शिकायत करें।' in response.get_data(as_text=True)


def test_submit_and_track_pages_render_hindi_after_switch(client):
    client.get('/language/hi?next=/')

    submit = client.get('/submit')
    assert submit.status_code == 200
    assert 'सुरक्षित रूप से शिकायत दर्ज करें' in submit.get_data(as_text=True)
    assert 'विभाग चुनें' in submit.get_data(as_text=True)

    track = client.get('/track')
    assert track.status_code == 200
    assert 'अपनी शिकायत ट्रैक करें' in track.get_data(as_text=True)
    assert 'ट्रैकिंग आईडी' in track.get_data(as_text=True)


def test_unsupported_language_falls_back_safely(client):
    response = client.get('/language/xx?next=/submit', follow_redirects=True)

    assert response.status_code == 200
    assert 'lang="en"' in response.get_data(as_text=True)
    assert 'Submit Complaint Securely' in response.get_data(as_text=True)
