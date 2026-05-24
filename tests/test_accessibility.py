"""
Rendered accessibility regression checks for WCAG/GIGW template basics.
"""
from datetime import timedelta
from html.parser import HTMLParser

import pytest

from app import create_app, db
from app.clock import utc_now
from app.models import AuditLog, Complaint, Department, Service, User


class AccessibilityParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.labels_for = set()
        self.controls = []
        self.tables = []
        self.landmarks = set()
        self.links = []
        self._table = None
        self._button_stack = []
        self.buttons = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'header', 'main', 'nav', 'footer'}:
            self.landmarks.add(tag)
        if tag == 'a':
            self.links.append(attrs)
        if tag == 'label' and attrs.get('for'):
            self.labels_for.add(attrs['for'])
        if tag in {'input', 'select', 'textarea'}:
            if attrs.get('type') not in {'hidden', 'submit', 'button', 'reset'}:
                self.controls.append((tag, attrs))
        if tag == 'table':
            self._table = {'caption': False, 'ths': []}
        if tag == 'caption' and self._table is not None:
            self._table['caption'] = True
        if tag == 'th' and self._table is not None:
            self._table['ths'].append(attrs)
        if tag == 'button':
            self._button_stack.append({'attrs': attrs, 'text': []})

    def handle_data(self, data):
        if self._button_stack:
            self._button_stack[-1]['text'].append(data.strip())

    def handle_endtag(self, tag):
        if tag == 'table' and self._table is not None:
            self.tables.append(self._table)
            self._table = None
        if tag == 'button' and self._button_stack:
            self.buttons.append(self._button_stack.pop())


@pytest.fixture
def app():
    app = create_app('testing')
    app.config['OPENAI_API_KEY'] = None

    with app.app_context():
        db.create_all()

        department = Department(name='Water Supply', description='Water department')
        db.session.add(department)
        db.session.flush()

        service = Service(
            name='Pipeline Leakage',
            department_id=department.id,
            description='Pipeline leak repair',
            sla_days=7,
        )
        db.session.add(service)
        db.session.flush()

        admin = User(username='admin', email='admin@civikindia.gov.in', role='admin', is_active=True)
        admin.set_password('Admin@1234')
        officer = User(
            username='officer_water',
            email='officer_water@civikindia.gov.in',
            role='officer',
            department_id=department.id,
            is_active=True,
        )
        officer.set_password('Officer@1234')
        db.session.add_all([admin, officer])
        db.session.flush()

        now = utc_now()
        complaints = [
            Complaint(
                tracking_id='MIBA11Y001',
                service_id=service.id,
                department_id=department.id,
                description='Closed complaint for accessibility form coverage.',
                status='Closed',
                assigned_to=officer.id,
                submitted_at=now - timedelta(days=5),
                updated_at=now,
                resolved_at=now,
            ),
            Complaint(
                tracking_id='MIBA11Y002',
                service_id=service.id,
                department_id=department.id,
                description='Assigned complaint for officer accessibility coverage.',
                status='Under Review',
                assigned_to=officer.id,
                submitted_at=now - timedelta(days=2),
                updated_at=now,
            ),
        ]
        for complaint in complaints:
            complaint.initialize_sla_due()
            db.session.add(complaint)
        db.session.commit()

        AuditLog.create_entry(
            username='admin',
            role='admin',
            action='A11Y_FIXTURE_READY',
            details='Accessibility fixture created.',
        )

        yield app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _parse(html):
    parser = AccessibilityParser()
    parser.feed(html)
    return parser


def _assert_accessible_basics(html, path):
    parser = _parse(html)

    assert {'header', 'nav', 'main', 'footer'}.issubset(parser.landmarks), path
    assert any(link.get('href') == '#main-content' for link in parser.links), path

    unlabeled_controls = []
    for tag, attrs in parser.controls:
        control_id = attrs.get('id')
        if not (
            attrs.get('aria-label')
            or attrs.get('aria-labelledby')
            or (control_id and control_id in parser.labels_for)
        ):
            unlabeled_controls.append((tag, attrs.get('name'), control_id))
    assert not unlabeled_controls, f"{path} unlabeled controls: {unlabeled_controls}"

    unnamed_buttons = []
    for button in parser.buttons:
        attrs = button['attrs']
        text = ' '.join(part for part in button['text'] if part).strip()
        if not (text or attrs.get('aria-label') or attrs.get('aria-labelledby') or attrs.get('title')):
            unnamed_buttons.append(attrs)
    assert not unnamed_buttons, f"{path} unnamed buttons: {unnamed_buttons}"

    table_issues = []
    for index, table in enumerate(parser.tables, start=1):
        if not table['caption']:
            table_issues.append(f'table {index} missing caption')
        if any(th.get('scope') != 'col' for th in table['ths']):
            table_issues.append(f'table {index} has unscoped headers')
    assert not table_issues, f"{path} table issues: {table_issues}"


def test_public_pages_have_accessibility_basics(client):
    public_paths = [
        '/',
        '/about',
        '/submit',
        '/track?tracking_id=MIBA11Y001',
        '/dashboard',
        '/geo-heatmap',
        '/sitemap',
    ]

    for path in public_paths:
        response = client.get(path)
        assert response.status_code == 200
        _assert_accessible_basics(response.get_data(as_text=True), path)


def test_admin_pages_have_accessibility_basics(client):
    login = client.post(
        '/auth/login',
        data={'username': 'admin', 'password': 'Admin@1234'},
        follow_redirects=False,
    )
    assert login.status_code == 302

    admin_paths = [
        '/admin/dashboard',
        '/admin/complaints',
        '/admin/complaint/MIBA11Y001',
        '/admin/officers',
        '/admin/departments',
        '/admin/audit-logs',
        '/auth/profile',
    ]

    for path in admin_paths:
        response = client.get(path)
        assert response.status_code == 200
        _assert_accessible_basics(response.get_data(as_text=True), path)


def test_officer_pages_have_accessibility_basics(app):
    client = app.test_client()
    login = client.post(
        '/auth/login',
        data={'username': 'officer_water', 'password': 'Officer@1234'},
        follow_redirects=False,
    )
    assert login.status_code == 302

    officer_paths = [
        '/officer/dashboard',
        '/officer/complaint/MIBA11Y002',
        '/auth/profile',
    ]

    for path in officer_paths:
        response = client.get(path)
        assert response.status_code == 200
        _assert_accessible_basics(response.get_data(as_text=True), path)


def test_accessibility_controls_present_in_header(client):
    """Test that the GIGW compliance accessibility controls (font-scale and contrast toggles) render in header."""
    response = client.get('/')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'gov-accessibility-controls' in html
    assert 'data-scale="small"' in html
    assert 'data-scale="medium"' in html
    assert 'data-scale="large"' in html
    assert 'data-scale="xlarge"' in html
    assert 'id="contrastToggleBtn"' in html
