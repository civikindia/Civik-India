"""
Civik India Public Routes Tests
Tests for citizen-facing functionality.
"""
import io

import pytest
from datetime import timedelta
from app import create_app, db
from app.clock import utc_now
from app.models import Department, Service, Complaint, User


@pytest.fixture
def app():
    """Create application for testing."""
    app = create_app('testing')
    
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def sample_data(app):
    """Create sample data for tests."""
    with app.app_context():
        dept = Department(name='Test Department', description='Test')
        db.session.add(dept)
        db.session.commit()
        
        service = Service(name='Test Service', department_id=dept.id)
        db.session.add(service)
        db.session.commit()
        
        return {'department_id': dept.id, 'service_id': service.id}


class TestHomepage:
    """Tests for homepage."""
    
    def test_homepage_loads(self, client):
        """Test homepage loads successfully."""
        response = client.get('/')
        assert response.status_code == 200
        assert b'Civik India' in response.data
    
    def test_homepage_shows_stats(self, client):
        """Test homepage shows statistics."""
        response = client.get('/')
        assert response.status_code == 200
        assert b'Total Complaints' in response.data

    def test_homepage_shows_ai_chatbot(self, client):
        """Homepage should include AI helper chatbot section."""
        response = client.get('/')
        assert response.status_code == 200
        assert b'Need Help Using The Portal?' in response.data
        assert b'id="homeAiForm"' in response.data

    def test_about_page_loads(self, client):
        """Test about page loads successfully."""
        response = client.get('/about')
        assert response.status_code == 200
        assert b'Civik India' in response.data
        assert b'breadcrumb' in response.data

    def test_nav_includes_information_links(self, client):
        """Primary nav should expose About and Help."""
        response = client.get('/')
        assert response.status_code == 200
        assert b'Information' in response.data
        assert b'/about' in response.data
        assert b'/help' in response.data

    def test_geo_heatmap_page_has_map_container(self, client):
        """Geo heatmap page should render the map mount point."""
        response = client.get('/geo-heatmap')
        assert response.status_code == 200
        assert b'id="geoHeatmapContainer"' in response.data

    def test_geo_heatmap_page_has_local_india_basemap_fallback(self, client):
        """Geo heatmap should include a local India basemap for offline tile failures."""
        response = client.get('/geo-heatmap')
        assert response.status_code == 200
        assert b'createLocalIndiaBasemap' in response.data
        assert b'India Local Basemap' in response.data
        assert b'Basemap: Local India fallback' in response.data

    def test_how_it_works_page_loads(self, client):
        """Test how-it-works page loads successfully."""
        response = client.get('/how-it-works')
        assert response.status_code == 200
        assert b'How The Portal Works' in response.data

    def test_favicon_route_exists(self, client):
        """Favicon route should resolve through static redirect."""
        response = client.get('/favicon.ico')
        assert response.status_code in (200, 301, 302, 308)


class TestComplaintSubmission:
    """Tests for complaint submission."""
    
    def test_submit_page_loads(self, client):
        """Test submit page loads."""
        response = client.get('/submit')
        assert response.status_code == 200
        assert b'Submit Complaint' in response.data
    
    def test_submit_complaint_success(self, client, sample_data):
        """Test successful complaint submission."""
        response = client.post('/submit', data={
            'department_id': sample_data['department_id'],
            'service_id': sample_data['service_id'],
            'description': (
                'This is a detailed test complaint describing a municipal service delay with dates, '
                'location context, department impact, evidence details, and the action expected from officers.'
            ),
            'state': 'Maharashtra',
            'district': 'Mumbai Suburban',
            'city': 'Mumbai',
            'evidence': (io.BytesIO(b'%PDF-1.4 mock pdf content'), 'evidence.pdf')
        }, content_type='multipart/form-data', follow_redirects=True)
        
        assert response.status_code == 200
        assert b'Complaint submitted successfully' in response.data
        
        # Verify complaint was created
        complaint = Complaint.query.first()
        assert complaint is not None
        assert complaint.tracking_id.startswith('CIV') or complaint.tracking_id.startswith('MIB')
        assert complaint.state == 'Maharashtra'
        assert complaint.district == 'Mumbai Suburban'
        assert complaint.city == 'Mumbai'
        assert complaint.status == 'Awaiting Review'

    def test_confirmation_page_shows_submission_summary(self, client, sample_data):
        """Confirmation page should show submitted timestamp and summary."""
        complaint = Complaint(
            tracking_id='MIBCONFIRM01',
            service_id=sample_data['service_id'],
            department_id=sample_data['department_id'],
            description=(
                'A detailed confirmation-page test complaint describing municipal service delay, '
                'location context, and expected officer action for grading verification.'
            ),
            submitted_at=utc_now(),
            priority='Normal',
        )
        db.session.add(complaint)
        db.session.commit()

        response = client.get('/confirmation/MIBCONFIRM01')
        assert response.status_code == 200
        assert b'Submission Summary' in response.data
        assert b'Submitted' in response.data
        assert b'Test Department' in response.data
    
    def test_submit_complaint_validation(self, client, sample_data):
        """Test complaint submission validation."""
        # Too short description
        response = client.post('/submit', data={
            'department_id': sample_data['department_id'],
            'service_id': sample_data['service_id'],
            'description': 'Short',
            'evidence': (io.BytesIO(b'%PDF-1.4 mock pdf content'), 'evidence.pdf')
        }, content_type='multipart/form-data')
        
        assert b'Description must be at least 25 characters' in response.data

    def test_submit_complaint_requires_evidence(self, client, sample_data):
        """Complaint submission should require an evidence upload."""
        response = client.post('/submit', data={
            'department_id': sample_data['department_id'],
            'service_id': sample_data['service_id'],
            'description': 'This description is longer than twenty-five characters.'
        })

        assert response.status_code == 200
        assert b'Evidence upload is required' in response.data
        assert Complaint.query.count() == 0


class TestComplaintTracking:
    """Tests for complaint tracking."""
    
    def test_track_page_loads(self, client):
        """Test track page loads."""
        response = client.get('/track')
        assert response.status_code == 200
        assert b'Track Your Complaint' in response.data
    
    def test_track_complaint_found(self, client, sample_data):
        """Test tracking a valid complaint."""
        # Create a complaint first
        complaint = Complaint(
            tracking_id='MIBTEST1234',
            service_id=sample_data['service_id'],
            department_id=sample_data['department_id'],
            description='Test complaint for tracking'
        )
        db.session.add(complaint)
        db.session.commit()
        
        response = client.post('/track', data={
            'tracking_id': 'MIBTEST1234'
        })
        
        assert response.status_code == 200
        assert b'MIBTEST1234' in response.data
        assert b'Your Submission' in response.data

    def test_track_generated_slash_tracking_id_found(self, client, sample_data):
        """Generated slash-format tracking IDs should work on the public tracker."""
        complaint = Complaint(
            tracking_id='MIBSP/2026/05/ABC123XY',
            service_id=sample_data['service_id'],
            department_id=sample_data['department_id'],
            description='Slash-format tracking ID lookup regression.'
        )
        db.session.add(complaint)
        db.session.commit()

        response = client.post('/track', data={
            'tracking_id': ' mibsp / 2026 / 05 / abc123xy '
        })

        assert response.status_code == 200
        assert b'MIBSP/2026/05/ABC123XY' in response.data
        assert b'Your Submission' in response.data

    def test_admin_complaints_mobile_submitted_markup(self, client, sample_data):
        """Complaints table should render submitted datetime in mobile-friendly cells."""
        admin = User(username='admin', email='admin@test.local', role='admin', is_active=True)
        admin.set_password('Admin@1234')
        db.session.add(admin)

        complaint = Complaint(
            tracking_id='MIBADM0001',
            service_id=sample_data['service_id'],
            department_id=sample_data['department_id'],
            description='Admin complaints list submitted-date regression coverage.',
            submitted_at=utc_now(),
        )
        db.session.add(complaint)
        db.session.commit()

        login = client.post(
            '/auth/login',
            data={'username': 'admin', 'password': 'Admin@1234'},
            follow_redirects=True,
        )
        assert login.status_code == 200

        response = client.get('/admin/complaints')
        assert response.status_code == 200
        assert b'data-label="Submitted"' in response.data
        assert b'MIBADM0001' in response.data
        assert b'N/A' not in response.data.split(b'data-label="Submitted"')[1][:80]
    
    def test_track_complaint_not_found(self, client):
        """Test tracking non-existent complaint."""
        response = client.post('/track', data={
            'tracking_id': 'MIBINVALID1'
        })
        
        assert b'Complaint not found' in response.data

    def test_reopen_closed_complaint(self, client, sample_data):
        """Citizen can reopen a closed complaint."""
        complaint = Complaint(
            tracking_id='MIBCLOSE123',
            service_id=sample_data['service_id'],
            department_id=sample_data['department_id'],
            description='This complaint was marked closed but issue still persists.',
            status='Closed',
            resolved_at=utc_now()
        )
        db.session.add(complaint)
        db.session.commit()

        response = client.post(
            '/complaint/MIBCLOSE123/reopen',
            data={'reopen_reason': 'Issue still unresolved and requires further action.'},
            follow_redirects=True
        )

        assert response.status_code == 200
        updated = Complaint.query.filter_by(tracking_id='MIBCLOSE123').first()
        assert updated.status == 'Reopened'
        assert updated.reopen_count == 1

    def test_feedback_submission_for_closed_complaint(self, client, sample_data):
        """Citizen can submit anonymous feedback on closed complaint."""
        complaint = Complaint(
            tracking_id='MIBFEED1234',
            service_id=sample_data['service_id'],
            department_id=sample_data['department_id'],
            description='Closed complaint for feedback test.',
            status='Closed',
            resolved_at=utc_now()
        )
        db.session.add(complaint)
        db.session.commit()

        response = client.post(
            '/complaint/MIBFEED1234/feedback',
            data={'rating': '4', 'feedback': 'Handled reasonably well.'},
            follow_redirects=True
        )
        assert response.status_code == 200

        updated = Complaint.query.filter_by(tracking_id='MIBFEED1234').first()
        assert updated.citizen_rating == 4
        assert updated.citizen_feedback == 'Handled reasonably well.'


class TestPublicDashboard:
    """Tests for public dashboard."""
    
    def test_dashboard_loads(self, client):
        """Test dashboard loads successfully."""
        response = client.get('/dashboard')
        assert response.status_code == 200
        assert b'Public Transparency Dashboard' in response.data

    def test_geo_heatmap_page_loads(self, client):
        """Test geo heatmap page loads successfully."""
        response = client.get('/geo-heatmap')
        assert response.status_code == 200
        assert b'Complaint Geo Heatmap' in response.data
        assert b'Reset Filters' in response.data
        assert b'All States' in response.data
        assert b'All Districts' in response.data
        assert b'All Cities' in response.data


class TestAPIEndpoints:
    """Tests for public API endpoints."""
    
    def test_health_check(self, client):
        """Test health check endpoint."""
        response = client.get('/health')
        assert response.status_code == 200
        assert b'healthy' in response.data
    
    def test_get_stats(self, client):
        """Test stats API endpoint."""
        response = client.get('/api/stats')
        assert response.status_code == 200
        data = response.get_json()
        assert 'total' in data
        assert 'pending' in data

    def test_dashboard_overview_api(self, client, sample_data):
        """Dashboard overview API should return aggregate payload for live filters."""
        complaint = Complaint(
            tracking_id='MIBOVR12345',
            service_id=sample_data['service_id'],
            department_id=sample_data['department_id'],
            description='Dashboard overview sample complaint for payload checks.',
            status='Pending'
        )
        db.session.add(complaint)
        db.session.commit()

        response = client.get('/api/dashboard/overview')
        assert response.status_code == 200
        payload = response.get_json()
        assert 'stats' in payload
        assert 'dept_stats' in payload
        assert 'recent_complaints' in payload
        assert payload['recent_complaints']
        assert 'tracking_id' not in payload['recent_complaints'][0]
        assert payload['recent_complaints'][0]['public_reference'].startswith('Ref ')

    def test_dashboard_overview_rejects_invalid_month(self, client):
        """Dashboard overview should reject malformed month filters."""
        response = client.get('/api/dashboard/overview?from_month=2026-13')
        assert response.status_code == 400
    
    def test_get_services(self, client, sample_data):
        """Test services API endpoint."""
        dept_id = sample_data['department_id']
        response = client.get(f'/api/services/{dept_id}')
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)

    def test_chart_dept_supports_department_filter(self, client, sample_data):
        """Department chart should support department-level filtering."""
        response = client.get(f'/api/chart/dept?department_id={sample_data["department_id"]}')
        assert response.status_code == 200
        payload = response.get_json()
        assert isinstance(payload.get('labels'), list)
        assert isinstance(payload.get('data'), list)
        assert len(payload['labels']) == 1

    def test_ai_assist_requires_json(self, client):
        """AI endpoint should require JSON payloads."""
        response = client.post('/api/ai/assist', data={'message': 'help me'})
        assert response.status_code == 400

    def test_ai_assist_unconfigured_returns_fallback(self, client):
        """AI endpoint should return local fallback when API key is not configured."""
        response = client.post('/api/ai/assist', json={'message': 'help me draft complaint'})
        assert response.status_code == 200
        data = response.get_json()
        assert data.get('fallback') is True
        assert isinstance(data.get('reply'), str)
        assert data.get('reply')

    def test_ai_assist_homepage_mode_unconfigured_returns_fallback(self, client, app):
        """Homepage mode should return local fallback when API key is not configured."""
        app.config['AI_RATE_MIN_INTERVAL_SECONDS'] = 0
        response = client.post('/api/ai/assist', json={
            'assistant': 'homepage',
            'message': 'How do I submit a complaint?'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data.get('fallback') is True
        assert isinstance(data.get('reply'), str)
        assert data.get('reply')

    def test_ai_assist_supports_openai_compatible_base_url(self, client, app, monkeypatch):
        """AI endpoint should pass custom base URL for providers such as Mistral."""
        import openai

        captured = {}

        class FakeCompletions:
            def create(self, **kwargs):
                captured['request'] = kwargs
                message = type('Message', (), {'content': 'Draft guidance from provider.'})()
                choice = type('Choice', (), {'message': message})()
                return type('Completion', (), {'choices': [choice]})()

        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured['client_kwargs'] = kwargs
                self.chat = type('Chat', (), {'completions': FakeCompletions()})()

        monkeypatch.setattr(openai, 'OpenAI', FakeOpenAI)
        app.config['AI_RATE_MIN_INTERVAL_SECONDS'] = 0
        app.config['OPENAI_API_KEY'] = 'test-provider-key'
        app.config['OPENAI_BASE_URL'] = 'https://api.mistral.ai/v1'
        app.config['OPENAI_MODEL'] = 'mistral-medium-latest'

        response = client.post('/api/ai/assist', json={'message': 'help me draft complaint'})

        assert response.status_code == 200
        data = response.get_json()
        assert data.get('reply') == 'Draft guidance from provider.'
        assert 'fallback' not in data
        assert captured['client_kwargs'] == {
            'api_key': 'test-provider-key',
            'base_url': 'https://api.mistral.ai/v1',
        }
        assert captured['request']['model'] == 'mistral-medium-latest'

    def test_ai_classify_endpoint_returns_prediction(self, client):
        """AI classify endpoint should return category and service suggestion payload."""
        dept = Department(name='Water Supply', description='Water services')
        db.session.add(dept)
        db.session.commit()

        service = Service(
            name='Water Leakage Repair',
            description='Leakage and broken pipeline repair',
            department_id=dept.id
        )
        db.session.add(service)
        db.session.commit()

        response = client.post('/api/ai/classify', json={
            'description': 'There is a serious water leakage from the municipal pipeline for two days.'
        })
        assert response.status_code == 200
        payload = response.get_json()
        assert payload.get('priority') in ['Normal', 'High']
        assert 'sentiment' in payload
        assert payload.get('service_name') == 'Water Leakage Repair'
        assert payload.get('department_name') == 'Water Supply'

    def test_ai_classify_requires_minimum_description(self, client):
        """AI classify endpoint should reject very short descriptions."""
        response = client.post('/api/ai/classify', json={'description': 'too short'})
        assert response.status_code == 400

    def test_sla_escalation_runs_on_stats_api(self, client, sample_data):
        """Stats API should trigger SLA escalation for overdue complaints."""
        complaint = Complaint(
            tracking_id='MIBSLA12345',
            service_id=sample_data['service_id'],
            department_id=sample_data['department_id'],
            description='Overdue complaint should auto-escalate.',
            status='Pending',
            sla_due_at=utc_now() - timedelta(days=1)
        )
        db.session.add(complaint)
        db.session.commit()

        response = client.get('/api/stats')
        assert response.status_code == 200

        updated = Complaint.query.filter_by(tracking_id='MIBSLA12345').first()
        assert updated.status == 'Delayed'
        assert updated.escalation_level >= 1

    def test_public_csv_export(self, client):
        """Public monthly CSV export endpoint should return CSV."""
        response = client.get('/api/public/export/monthly.csv')
        assert response.status_code == 200
        assert 'text/csv' in response.content_type

    def test_geo_heatmap_api_returns_points(self, client, sample_data):
        """Geo heatmap API should include geotagged complaints."""
        complaint = Complaint(
            tracking_id='MIBGEO12345',
            service_id=sample_data['service_id'],
            department_id=sample_data['department_id'],
            description='Geo complaint sample for map tests.',
            state='Maharashtra',
            district='Mumbai Suburban',
            city='Mumbai',
            location_lat=12.9716,
            location_lng=77.5946
        )
        db.session.add(complaint)
        db.session.commit()

        response = client.get('/api/geo/heatmap')
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)
        record = next(item for item in data if item.get('city') == 'Mumbai')
        assert 'tracking_id' not in record
        assert record.get('public_reference', '').startswith('Ref ')
        assert record['state'] == 'Maharashtra'
        assert record['district'] == 'Mumbai Suburban'
        assert record['city'] == 'Mumbai'

    def test_geo_heatmap_api_treats_all_filters_as_unfiltered(self, client, sample_data):
        """Geo heatmap API should not treat all-select values as literal filters."""
        complaint = Complaint(
            tracking_id='MIBGEOALL01',
            service_id=sample_data['service_id'],
            department_id=sample_data['department_id'],
            description='Geo complaint sample for all filter tests.',
            state='Maharashtra',
            district='Mumbai Suburban',
            city='Mumbai',
            location_lat=19.0760,
            location_lng=72.8777
        )
        db.session.add(complaint)
        db.session.commit()

        response = client.get('/api/geo/heatmap?status=all&priority=all&state=all&district=all&city=all')
        assert response.status_code == 200
        data = response.get_json()
        assert any(item.get('city') == 'Mumbai' for item in data)
        assert all('tracking_id' not in item for item in data)

    def test_geo_heatmap_api_department_filter(self, client, sample_data):
        """Geo heatmap API should filter by department_id."""
        c1 = Complaint(
            tracking_id='MIBGEODEP01',
            service_id=sample_data['service_id'],
            department_id=sample_data['department_id'],
            description='Geo complaint department 1',
            state='Maharashtra',
            district='Mumbai Suburban',
            city='Mumbai',
            location_lat=19.0760,
            location_lng=72.8777
        )
        other_dept = Department(name='Forest Dept')
        db.session.add(other_dept)
        db.session.commit()
        
        c2 = Complaint(
            tracking_id='MIBGEODEP02',
            service_id=sample_data['service_id'],
            department_id=other_dept.id,
            description='Geo complaint department 2',
            state='Maharashtra',
            district='Mumbai Suburban',
            city='Mumbai',
            location_lat=19.0800,
            location_lng=72.8800
        )
        db.session.add(c1)
        db.session.add(c2)
        db.session.commit()

        response = client.get(f'/api/geo/heatmap?department_id={sample_data["department_id"]}')
        assert response.status_code == 200
        data = response.get_json()
        assert any(item.get('department_id') == sample_data['department_id'] for item in data)
        assert not any(item.get('department_id') == other_dept.id for item in data)
        assert all('tracking_id' not in item for item in data)

    def test_geo_heatmap_api_invalid_filter_characters(self, client):
        """Geo heatmap API should reject invalid characters in state/district/city."""
        response = client.get('/api/geo/heatmap?state=Mahar%3B%20DROP%20TABLE%20Complaints%3B--')
        assert response.status_code == 400
        assert b'Invalid characters in State' in response.data

    def test_geo_heatmap_api_oversized_filter_length(self, client):
        """Geo heatmap API should reject state/district/city values that exceed 100 characters."""
        long_val = 'A' * 105
        response = client.get(f'/api/geo/heatmap?city={long_val}')
        assert response.status_code == 400
        assert b'City must be under 100 characters' in response.data

    def test_geo_heatmap_api_rate_limiter(self, client, app):
        """Geo heatmap API rate limiter should return 429 when limits are exceeded outside of testing mode."""
        original_testing = app.testing
        original_config_testing = app.config.get('TESTING')
        original_cache_seconds = app.config.get('PUBLIC_API_CACHE_SECONDS')
        app.testing = False
        app.config['TESTING'] = False
        app.config['PUBLIC_API_CACHE_SECONDS'] = 0

        try:
            client.get('/api/geo/heatmap')
            response2 = client.get('/api/geo/heatmap')
            assert response2.status_code == 429
            assert b'Too many map queries' in response2.data or b'Please wait' in response2.data
        finally:
            app.testing = original_testing
            app.config['TESTING'] = original_config_testing
            app.config['PUBLIC_API_CACHE_SECONDS'] = original_cache_seconds

    def test_reopen_closed_complaint_oversized_reason(self, client, sample_data):
        """Test that a reopen reason > 1000 characters is rejected."""
        complaint = Complaint(
            tracking_id='MIBCLOSE999',
            service_id=sample_data['service_id'],
            department_id=sample_data['department_id'],
            description='This complaint was marked closed but issue still persists.',
            status='Closed',
            resolved_at=utc_now()
        )
        db.session.add(complaint)
        db.session.commit()

        long_reason = 'A' * 1005
        response = client.post(
            '/complaint/MIBCLOSE999/reopen',
            data={'reopen_reason': long_reason},
            follow_redirects=True
        )

        assert response.status_code == 200
        assert b'Reopen reason must be under 1000 characters.' in response.data
        updated = Complaint.query.filter_by(tracking_id='MIBCLOSE999').first()
        assert updated.status == 'Closed'

    def test_orphaned_upload_cleanup_on_db_error(self, client, sample_data, monkeypatch):
        """Test that uploaded files are cleaned up from disk if db commit fails."""
        def mock_commit():
            raise Exception("Mock DB Failure")
        monkeypatch.setattr(db.session, "commit", mock_commit)

        import io
        import os
        from flask import current_app

        data = {
            'department_id': sample_data['department_id'],
            'service_id': sample_data['service_id'],
            'description': (
                'This is a detailed test complaint describing a municipal service delay with dates, '
                'location context, department impact, evidence details, and the action expected from officers '
                'which contains more than enough words to easily bypass the minimum count check.'
            ),
            'state': 'Delhi',
            'district': 'New Delhi',
            'city': 'Delhi',
            'evidence': (io.BytesIO(b'%PDF-1.4 mock pdf content'), 'evidence.pdf')
        }

        written_paths = []
        from app.utils import save_uploaded_file
        original_save = save_uploaded_file

        def mock_save_uploaded_file(file):
            success, result = original_save(file)
            if success:
                written_paths.append(result['relative_path'])
            return success, result

        monkeypatch.setattr("app.routes.public.save_uploaded_file", mock_save_uploaded_file)

        response = client.post('/submit', data=data, content_type='multipart/form-data', follow_redirects=True)
        assert b'Error submitting complaint. Please try again.' in response.data

        assert len(written_paths) == 1
        full_path = os.path.join(current_app.config['UPLOAD_FOLDER'], written_paths[0])
        assert not os.path.exists(full_path)
