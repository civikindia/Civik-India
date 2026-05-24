"""
GIGW branding and shared layout smoke tests.
"""
import pytest

from app import create_app, db


@pytest.fixture
def app():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def test_gigw_header_and_footer_render_on_public_page(client):
    response = client.get('/about')

    assert response.status_code == 200
    assert b'breadcrumb' in response.data
    assert b'About' in response.data
    assert b'Government of India' in response.data
    assert 'भारत सरकार'.encode('utf-8') in response.data
    assert 'सत्यमेव जयते'.encode('utf-8') in response.data
    assert b'CivikIndia' in response.data or b'Municipal Governance Department' in response.data
    assert b'Anti-Corruption Helpline' in response.data
    assert b'1064' in response.data
    assert b'1800-11-0180' in response.data
    assert b'Last Updated' in response.data
    assert b'Visitor Counter' in response.data
    assert b'Accessibility Statement' in response.data
    assert b'Screen Reader Access' in response.data
    assert b'Website Policies' in response.data
    assert b'Sitemap' in response.data


def test_gigw_static_information_pages_load(client):
    pages = [
        '/sitemap',
        '/disclaimer',
        '/accessibility',
        '/screen-reader-access',
        '/website-policies',
        '/privacy',
        '/terms',
        '/help',
        '/contact',
    ]

    for path in pages:
        response = client.get(path)
        assert response.status_code == 200
        assert b'Last Updated' in response.data
