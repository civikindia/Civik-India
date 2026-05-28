"""
CivikIndia Flask Application Factory
Creates and configures the Flask application with all extensions.
"""
import importlib
import hashlib
from sqlalchemy import text, inspect
from urllib.parse import urljoin, urlparse
from flask import Flask, render_template, redirect, url_for, request, session, current_app, has_app_context
from flask_babel import Babel, get_locale
from werkzeug.middleware.proxy_fix import ProxyFix
from celery import Celery
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_mail import Mail
from flask_wtf.csrf import CSRFProtect
from config import config
from app.clock import utc_now

# Initialize extensions (no app yet)
db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()
babel = Babel()
celery = Celery(__name__)
mail = Mail()


def select_locale():
    """Resolve the active UI locale from session, then browser preferences."""
    supported = current_app.config.get('BABEL_SUPPORTED_LOCALES', ['en'])
    selected = session.get('lang')
    if selected in supported:
        return selected
    return request.accept_languages.best_match(supported) or current_app.config.get(
        'BABEL_DEFAULT_LOCALE', 'en'
    )


def is_safe_redirect_url(target):
    """Allow redirects only to the current host."""
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in {'http', 'https'} and ref_url.netloc == test_url.netloc


def create_app(config_name=None):
    """
    Application factory pattern - creates and configures Flask app.
    
    Args:
        config_name: Configuration environment (development, testing, production)
    
    Returns:
        Configured Flask application instance
    """
    if config_name is None:
        import os
        config_name = os.environ.get('FLASK_ENV', 'default')
    
    app = Flask(__name__, 
                instance_relative_config=True,
                template_folder='templates',
                static_folder='static')
    
    # Load configuration
    app.config.from_object(config[config_name])
    config[config_name].init_app(app)
    if app.config.get('TRUST_PROXY_HEADERS'):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
    
    # Initialize extensions with app
    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    babel.init_app(app, locale_selector=select_locale)
    mail.init_app(app)
    init_celery(app)
    
    # Register blueprints
    from app.routes.public import public_bp
    from app.routes.auth import auth_bp
    from app.routes.officer import officer_bp
    from app.routes.admin import admin_bp
    
    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(officer_bp, url_prefix='/officer')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    importlib.import_module('app.tasks')  # register Celery task functions
    
    # Register error handlers
    register_error_handlers(app)
    register_cli(app)
    
    # Register template filters
    register_template_filters(app)
    app.extensions.setdefault('visitor_counter', {
        'count': int(app.config.get('VISITOR_COUNTER_BASE', 100000))
    })

    @app.before_request
    def track_public_visit():
        """Maintain a lightweight visitor count for the public footer."""
        if (
            request.method == 'GET'
            and request.endpoint
            and not request.endpoint.startswith('static')
        ):
            app.extensions['visitor_counter']['count'] += 1

    @app.before_request
    def bind_session_security():
        """Bind session to IP and User-Agent."""
        if 'user_id' in session:
            client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
            user_agent = request.user_agent.string
            
            if 'session_ip' not in session:
                session['session_ip'] = client_ip
                session['session_ua'] = user_agent
            else:
                # Soft binding for IP (alert on mismatch but don't force logout for mobile IP changes)
                # Hard binding for User-Agent
                if session['session_ua'] != user_agent:
                    session.clear()
                    flash('Session expired due to security policy (User-Agent changed). Please log in again.', 'warning')
                    return redirect(url_for('auth.login'))

    @app.after_request
    def add_security_headers(response):
        """Add security headers including CSP, framing, MIME sniffing, referrer, and permissions."""
        if 'X-Frame-Options' not in response.headers:
            response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=(self)'
        # CSP: allows Bootstrap/Chart.js/Leaflet from CDN, reCAPTCHA v3, OSM tiles, Nominatim reverse geocode.
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' "
                "https://cdn.jsdelivr.net "
                "https://unpkg.com "
                "https://www.google.com "
                "https://www.gstatic.com; "
            "style-src 'self' 'unsafe-inline' "
                "https://cdn.jsdelivr.net "
                "https://unpkg.com "
                "https://fonts.googleapis.com "
                "https://cdnjs.cloudflare.com; "
            "img-src 'self' data: blob: "
                "https://*.basemaps.cartocdn.com "
                "https://*.stadiamaps.com "
                "https://*.tile.openstreetmap.org "
                "https://tile.openstreetmap.org; "
            "font-src 'self' "
                "https://fonts.gstatic.com "
                "https://cdnjs.cloudflare.com "
                "https://cdn.jsdelivr.net "
                "https://unpkg.com; "
            "connect-src 'self' "
                "https://nominatim.openstreetmap.org; "
            "frame-src https://www.google.com; "
            "object-src 'none'; "
            "base-uri 'self';"
        )
        response.headers['Content-Security-Policy'] = csp
        return response

    @app.route('/admin/auth/login')
    def legacy_admin_auth_login():
        """Compatibility route for /admin/auth/login bookmarks."""
        return redirect(url_for('auth.login', next=request.args.get('next', '')))

    @app.route('/internal/backup/evidence', methods=['POST'])
    @csrf.exempt
    def internal_backup_evidence():
        """Protected cron endpoint for private Google Drive evidence backups."""
        expected = app.config.get('BACKUP_CRON_TOKEN')
        provided = request.headers.get('Authorization', '')
        if not expected or provided != f'Bearer {expected}':
            return {'error': 'unauthorized'}, 401
        from app.storage.google_drive_backup import backup_pending_evidence
        return backup_pending_evidence(), 200

    @app.route('/language/<locale>')
    def set_language(locale):
        """Switch interface language without changing route URLs."""
        supported = app.config.get('BABEL_SUPPORTED_LOCALES', ['en'])
        if locale in supported:
            session['lang'] = locale
        next_url = request.args.get('next') or request.referrer
        if not is_safe_redirect_url(next_url):
            next_url = url_for('public.index')
        return redirect(next_url)

    @app.context_processor
    def inject_i18n_context():
        active_locale = str(get_locale() or app.config.get('BABEL_DEFAULT_LOCALE', 'en'))
        return {
            'current_locale': active_locale,
            'supported_languages': app.config.get('LANGUAGES', {}),
            'site_last_updated': utc_now().strftime('%d/%m/%Y'),
            'visitor_counter': app.extensions['visitor_counter']['count'],
            'utc_now': utc_now,
            'recaptcha_site_key': app.config.get('RECAPTCHA_SITE_KEY', ''),
        }
    
    @app.context_processor
    def inject_pending_review_count():
        from app.models import Complaint
        # We only count Awaiting Review complaints in the database
        count = 0
        try:
            # Safely check if db and Complaint table exist (prevents errors during initial migration)
            count = Complaint.query.filter_by(status='Awaiting Review').count()
        except Exception:
            pass
        return {'pending_review_count': count}
    
    # Keep DB schema aligned with current model fields across environments.
    # Production instances may have legacy schemas from older releases.
    with app.app_context():
        ensure_schema_compatibility(app, run_create_all=(config_name in ('development', 'testing')))
    
    return app


def init_celery(app):
    """Configure Celery workers and beat with Flask app context."""
    celery.conf.update(
        broker_url=app.config.get('CELERY_BROKER_URL'),
        result_backend=app.config.get('CELERY_RESULT_BACKEND'),
        timezone=app.config.get('CELERY_TIMEZONE', 'Asia/Kolkata'),
        enable_utc=True,
        beat_schedule={
            'check-sla-breaches-every-15-minutes': {
                'task': 'app.tasks.check_sla_breaches',
                'schedule': 15 * 60,
            },
            'cleanup-old-uploads-daily': {
                'task': 'app.tasks.cleanup_old_uploads',
                'schedule': 24 * 60 * 60,
                'args': (30,),
            },
            'generate-daily-report': {
                'task': 'app.tasks.generate_daily_report',
                'schedule': 24 * 60 * 60,
            },
        },
    )

    class FlaskContextTask(celery.Task):
        abstract = True

        def __call__(self, *args, **kwargs):
            if has_app_context():
                return self.run(*args, **kwargs)
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = FlaskContextTask


def register_cli(app):
    """Register operational Flask CLI commands."""
    import click

    @app.cli.command('backup-evidence-to-drive')
    @click.option('--limit', default=100, show_default=True, help='Maximum records to process.')
    def backup_evidence_to_drive(limit):
        """Back up pending/failed private evidence objects to Google Drive."""
        from app.storage.google_drive_backup import backup_pending_evidence
        summary = backup_pending_evidence(limit=limit)
        click.echo(
            'Evidence backup: '
            f"enabled={summary.get('enabled')} "
            f"processed={summary.get('processed')} "
            f"success={summary.get('success')} "
            f"failed={summary.get('failed')}"
        )


def _ensure_new_tables(app, existing_tables):
    """
    Create brand-new tables that don't exist yet in the live schema.
    Called from ensure_schema_compatibility() after ALTER TABLE column patches.
    Uses CREATE TABLE IF NOT EXISTS so it is safe to run on every startup.
    """
    dialect = db.engine.url.get_backend_name()
    new_table_sqls = {
        'complaint_status_history': (
            # SQLite / MySQL / PostgreSQL compatible DDL
            "CREATE TABLE IF NOT EXISTS complaint_status_history ("
            "  id INTEGER PRIMARY KEY {autoincrement},"
            "  complaint_id INTEGER NOT NULL REFERENCES complaints(id),"
            "  from_status VARCHAR(30),"
            "  to_status VARCHAR(30) NOT NULL,"
            "  notes TEXT,"
            "  changed_by VARCHAR(64) NOT NULL DEFAULT 'system',"
            "  changed_at TIMESTAMP NOT NULL"
            ")"
        ),
    }
    for table_name, sql_template in new_table_sqls.items():
        if table_name in existing_tables:
            continue
        autoincrement = 'AUTOINCREMENT' if dialect == 'sqlite' else (
            'AUTO_INCREMENT' if dialect == 'mysql' else ''
        )
        sql = sql_template.format(autoincrement=autoincrement)
        try:
            db.session.execute(text(sql))
            db.session.commit()
            app.logger.warning('Created new table: %s', table_name)
        except Exception as exc:
            db.session.rollback()
            error_text = str(exc).lower()
            if 'already exists' in error_text or 'duplicate' in error_text:
                app.logger.info('Table already present: %s', table_name)
            else:
                app.logger.exception('Failed to create table %s: %s', table_name, exc)


def ensure_schema_compatibility(app, run_create_all=False):
    """
    Auto-upgrade database schemas when migrations are not available.
    Adds missing columns using ALTER TABLE in supported databases.
    """
    try:
        if run_create_all:
            db.create_all()
    except Exception:
        return

    schema_patches = {
        'services': {
            'sla_days': "INTEGER NOT NULL DEFAULT 7",
        },
        'users': {
            'failed_login_attempts': "INTEGER NOT NULL DEFAULT 0",
            'locked_until': "TIMESTAMP",
            # Module 3: 2FA fields
            'totp_secret': "VARCHAR(64)",
            'two_fa_enabled': "BOOLEAN NOT NULL DEFAULT FALSE",
            'backup_codes': "TEXT",
            'reset_token': "VARCHAR(64)",
            'reset_token_expires_at': "INTEGER",
        },
        'complaints': {
            'escalation_level': "INTEGER NOT NULL DEFAULT 0",
            'sla_due_at': "TIMESTAMP",
            'delayed_at': "TIMESTAMP",
            'reopen_count': "INTEGER NOT NULL DEFAULT 0",
            'citizen_rating': "INTEGER",
            'citizen_feedback': "TEXT",
            'feedback_submitted_at': "TIMESTAMP",
            'priority': "VARCHAR(20) NOT NULL DEFAULT 'Normal'",
            'ai_category': "VARCHAR(80)",
            'ai_sentiment': "VARCHAR(20) NOT NULL DEFAULT 'neutral'",
            'ai_urgent': "BOOLEAN NOT NULL DEFAULT FALSE",
            'state': "VARCHAR(80)",
            'district': "VARCHAR(120)",
            'city': "VARCHAR(120)",
            'location_lat': "FLOAT",
            'location_lng': "FLOAT",
            # Module 6: Form enhancements
            'complaint_category': "VARCHAR(80)",
            'ward_locality': "VARCHAR(120)",
            'incident_date': "DATE",
            'officer_name_alleged': "VARCHAR(120)",
            'witness_available': "BOOLEAN",
            'contact_preference': "VARCHAR(50)",
            'voluntary_id': "VARCHAR(256)",
            'rejection_reason': "TEXT",
            'admin_notes': "TEXT",
            'reviewed_at': "TIMESTAMP",
            'reviewed_by_id': "INTEGER REFERENCES users(id)",
        },
        'evidence_files': {
            'file_hash_sha256': "VARCHAR(64)",
            'uploaded_by_user_id': "INTEGER",
            'safe_filename': "VARCHAR(255)",
            'file_extension': "VARCHAR(20)",
            'byte_size': "INTEGER",
            'sha256_hash': "VARCHAR(64)",
            'storage_provider': "VARCHAR(40) NOT NULL DEFAULT 'local'",
            'storage_bucket': "VARCHAR(255)",
            'storage_key': "VARCHAR(512)",
            'drive_backup_file_id': "VARCHAR(255)",
            'drive_backup_status': "VARCHAR(20) NOT NULL DEFAULT 'disabled'",
            'encrypted': "BOOLEAN NOT NULL DEFAULT FALSE",
            'created_at': "TIMESTAMP",
            'updated_at': "TIMESTAMP",
            'deleted_at': "TIMESTAMP",
        },
    }
    index_patches = [
        (
            'ix_complaints_department_status_submitted',
            'complaints',
            'CREATE INDEX IF NOT EXISTS ix_complaints_department_status_submitted '
            'ON complaints (department_id, status, submitted_at)'
        ),
        (
            'ix_complaints_resolved_status',
            'complaints',
            'CREATE INDEX IF NOT EXISTS ix_complaints_resolved_status '
            'ON complaints (resolved_at, status)'
        ),
        (
            'ix_complaints_submitted_geo',
            'complaints',
            'CREATE INDEX IF NOT EXISTS ix_complaints_submitted_geo '
            'ON complaints (submitted_at, location_lat, location_lng)'
        ),
        (
            'ix_complaints_geo_filter_sort',
            'complaints',
            'CREATE INDEX IF NOT EXISTS ix_complaints_geo_filter_sort '
            'ON complaints (department_id, status, priority, state, district, city, submitted_at)'
        ),
        (
            'ix_users_reset_token',
            'users',
            'CREATE INDEX IF NOT EXISTS ix_users_reset_token '
            'ON users (reset_token)'
        ),
        (
            'ix_evidence_files_storage_key',
            'evidence_files',
            'CREATE INDEX IF NOT EXISTS ix_evidence_files_storage_key '
            'ON evidence_files (storage_key)'
        ),
        (
            'ix_evidence_files_backup_status',
            'evidence_files',
            'CREATE INDEX IF NOT EXISTS ix_evidence_files_backup_status '
            'ON evidence_files (drive_backup_status, created_at)'
        ),
    ]

    try:
        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())
    except Exception:
        app.logger.exception('Unable to inspect database schema.')
        return

    dialect = db.engine.url.get_backend_name()
    for table_name, columns in schema_patches.items():
        if table_name not in existing_tables:
            continue

        try:
            existing_columns = {
                column['name']
                for column in inspector.get_columns(table_name)
            }
        except Exception:
            app.logger.exception('Unable to inspect columns for table %s', table_name)
            continue

        for column_name, column_def in columns.items():
            if column_name in existing_columns:
                continue
            alter_sql = f'ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}'
            if dialect == 'postgresql':
                # Postgres supports IF NOT EXISTS for column additions.
                alter_sql = f'ALTER TABLE "{table_name}" ADD COLUMN IF NOT EXISTS {column_name} {column_def}'
            try:
                db.session.execute(text(alter_sql))
                existing_columns.add(column_name)
                app.logger.warning("Applied schema patch: %s.%s", table_name, column_name)
            except Exception as exc:
                db.session.rollback()
                error_text = str(exc).lower()
                if (
                    'duplicate column' in error_text
                    or 'already exists' in error_text
                ):
                    app.logger.info("Schema patch already present: %s.%s", table_name, column_name)
                    continue
                app.logger.exception(
                    "Schema patch failed for %s.%s", table_name, column_name
                )
                raise

    for index_name, table_name, index_sql in index_patches:
        if table_name not in existing_tables:
            continue
        try:
            db.session.execute(text(index_sql))
            app.logger.info('Schema index ensured: %s', index_name)
        except Exception as exc:
            db.session.rollback()
            error_text = str(exc).lower()
            if 'already exists' in error_text or 'duplicate key name' in error_text:
                app.logger.info('Schema index already present: %s', index_name)
                continue
            app.logger.exception('Schema index patch failed: %s', index_name)
            raise

    # Create any brand-new tables (e.g. complaint_status_history)
    _ensure_new_tables(app, existing_tables)

    db.session.commit()


def register_error_handlers(app):
    """Register custom error handlers."""
    
    @app.errorhandler(403)
    def forbidden(error):
        return render_template('errors/403.html'), 403
    
    @app.errorhandler(404)
    def not_found(error):
        return render_template('errors/404.html'), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        return render_template('errors/500.html'), 500
    
    @app.errorhandler(413)
    def too_large(error):
        return render_template('errors/413.html'), 413


def register_template_filters(app):
    """Register custom Jinja2 template filters."""
    
    @app.template_filter('format_datetime')
    def format_datetime(value, format='%d %b %Y, %I:%M %p'):
        """Format datetime for display."""
        if value is None:
            return 'N/A'
        return value.strftime(format)

    @app.template_filter('public_reference')
    def public_reference(value):
        """Return a non-trackable public reference for aggregate dashboards."""
        if not value:
            return 'Public record'
        digest = hashlib.sha256(str(value).encode('utf-8')).hexdigest()[:8].upper()
        return f'Ref {digest}'
    
    @app.template_filter('status_badge')
    def status_badge(status):
        """Return Bootstrap badge class for status."""
        badges = {
            'Pending': 'badge-pending',
            'Under Review': 'badge-review',
            'Action Taken': 'badge-action',
            'Delayed': 'badge-delayed',
            'Reopened': 'badge-reopened',
            'Closed': 'badge-closed'
        }
        return badges.get(status, 'badge-secondary')

    @app.template_filter('status_icon')
    def status_icon(status):
        """Return FontAwesome icon name for complaint status."""
        icons = {
            'Pending': 'clock',
            'Under Review': 'search',
            'Action Taken': 'tools',
            'Delayed': 'triangle-exclamation',
            'Reopened': 'rotate-left',
            'Closed': 'check-circle'
        }
        return icons.get(status, 'circle')
