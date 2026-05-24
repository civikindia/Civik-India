"""
CivikIndia Configuration Module
Supports: Development (SQLite), VPS Production (MySQL), Render (PostgreSQL)
"""
import os
import secrets
from datetime import timedelta
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))

DEFAULT_ALLOWED_UPLOAD_EXTENSIONS = {
    'jpg', 'jpeg', 'png', 'webp', 'pdf',
    'mp4', 'mov', 'mp3', 'wav', 'txt', 'doc', 'docx',
}

BLOCKED_UPLOAD_EXTENSIONS = {
    'exe', 'bat', 'cmd', 'sh', 'php', 'js', 'html', 'svg', 'jar', 'msi', 'dll',
}


def _csv_set(name, default):
    raw = os.environ.get(name)
    if not raw:
        return set(default)
    return {item.strip().lower().lstrip('.') for item in raw.split(',') if item.strip()}


def _env_bool(name, default='false'):
    return os.environ.get(name, default).lower() in ['true', 'on', '1', 'yes']


class Config:
    """Base configuration - shared across all environments."""
    
    # Application
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    
    # Evidence Security
    EVIDENCE_ENCRYPTION_KEY = os.environ.get('EVIDENCE_ENCRYPTION_KEY') or 'dev-evidence-encryption-key-persistent'
    AUDIT_HMAC_SECRET = os.environ.get('AUDIT_HMAC_SECRET') or 'dev-audit-hmac-secret-persistent-key'
    
    # Google reCAPTCHA v3
    RECAPTCHA_SITE_KEY = os.environ.get('RECAPTCHA_SITE_KEY', '')
    RECAPTCHA_SECRET_KEY = os.environ.get('RECAPTCHA_SECRET_KEY', '')
    
    # Audit log retention (7 years = 2555 days, government standard)
    AUDIT_RETENTION_DAYS = int(os.environ.get('AUDIT_RETENTION_DAYS', 2555))
    AUDIT_PURGE_ENABLED = os.environ.get('AUDIT_PURGE_ENABLED', 'false').lower() == 'true'
    
    # Database (will be overridden by subclasses)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 3600,
    }
    
    # File Uploads
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER') or os.path.join(basedir, 'uploads')
    MAX_UPLOAD_MB = int(os.environ.get('MAX_UPLOAD_MB', 16))
    MAX_CONTENT_LENGTH = int(os.environ.get('MAX_CONTENT_LENGTH', MAX_UPLOAD_MB * 1024 * 1024))
    ALLOWED_EXTENSIONS = _csv_set('ALLOWED_UPLOAD_EXTENSIONS', DEFAULT_ALLOWED_UPLOAD_EXTENSIONS)
    BLOCKED_UPLOAD_EXTENSIONS = set(BLOCKED_UPLOAD_EXTENSIONS)
    CLAMAV_ENABLED = _env_bool('CLAMAV_ENABLED')
    CLAMAV_SCANNER_PATH = os.environ.get('CLAMAV_SCANNER_PATH', 'clamscan')
    CLAMAV_SCAN_TIMEOUT_SECONDS = int(os.environ.get('CLAMAV_SCAN_TIMEOUT_SECONDS', 30))

    # Evidence storage: local for development/tests, private Cloudflare R2 for production.
    R2_ACCOUNT_ID = os.environ.get('R2_ACCOUNT_ID')
    R2_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID')
    R2_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY')
    R2_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME')
    R2_ENDPOINT_URL = os.environ.get('R2_ENDPOINT_URL')
    R2_PUBLIC_BASE_URL = os.environ.get('R2_PUBLIC_BASE_URL')
    EVIDENCE_STORAGE_PROVIDER = os.environ.get('EVIDENCE_STORAGE_PROVIDER') or (
        'r2' if R2_BUCKET_NAME and R2_ENDPOINT_URL else 'local'
    )

    # Google Drive archive backup. Drive is never used as live public storage.
    GOOGLE_DRIVE_BACKUP_ENABLED = _env_bool('GOOGLE_DRIVE_BACKUP_ENABLED')
    GOOGLE_DRIVE_FOLDER_ID = os.environ.get('GOOGLE_DRIVE_FOLDER_ID')
    GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
    GOOGLE_APPLICATION_CREDENTIALS = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    BACKUP_CRON_TOKEN = os.environ.get('BACKUP_CRON_TOKEN')

    # Hosted deployment process knobs.
    PORT = int(os.environ.get('PORT', 8000))
    WEB_CONCURRENCY = int(os.environ.get('WEB_CONCURRENCY', 1))
    GUNICORN_TIMEOUT = int(os.environ.get('GUNICORN_TIMEOUT', 120))
    TRUST_PROXY_HEADERS = _env_bool('TRUST_PROXY_HEADERS')
    
    # Session Security
    PERMANENT_SESSION_LIFETIME = timedelta(
        hours=int(os.environ.get('SESSION_LIFETIME_HOURS', 8))
    )
    SESSION_COOKIE_SECURE = False  # Override in production
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # Geo heatmap performance guard (API limit before progressive rendering)
    GEO_HEATMAP_MAX_POINTS = int(os.environ.get('GEO_HEATMAP_MAX_POINTS', 2500))
    PUBLIC_API_CACHE_SECONDS = int(os.environ.get('PUBLIC_API_CACHE_SECONDS', 15))
    SEND_FILE_MAX_AGE_DEFAULT = int(os.environ.get('SEND_FILE_MAX_AGE_DEFAULT', 3600))
    
    # CSRF
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600  # 1 hour
    
    # Pagination
    ITEMS_PER_PAGE = 20

    # Internationalization
    BABEL_DEFAULT_LOCALE = os.environ.get('BABEL_DEFAULT_LOCALE', 'en')
    BABEL_SUPPORTED_LOCALES = ['en', 'hi', 'mr', 'gu']
    BABEL_TRANSLATION_DIRECTORIES = os.path.join(basedir, 'translations')
    LANGUAGES = {
        'en': {'name': 'English', 'native_name': 'English'},
        'hi': {'name': 'Hindi', 'native_name': 'हिन्दी'},
        'mr': {'name': 'Marathi', 'native_name': 'मराठी'},
        'gu': {'name': 'Gujarati', 'native_name': 'ગુજરાતી'},
    }
    
    # Redis / Celery
    REDIS_URL = os.environ.get('REDIS_URL') or 'redis://localhost:6379/0'
    CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL') or 'redis://localhost:6379/0'
    CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND') or 'redis://localhost:6379/1'
    
    # Email (Optional)
    MAIL_SERVER = os.environ.get('MAIL_SERVER')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() in ['true', 'on', '1']
    MAIL_USE_SSL = os.environ.get('MAIL_USE_SSL', 'false').lower() in ['true', 'on', '1']
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER') or os.environ.get('MAIL_USERNAME') or 'no-reply@civikindia.gov.in'
    MAIL_SUPPRESS_SEND = os.environ.get('MAIL_SUPPRESS_SEND', 'false').lower() in ['true', 'on', '1']
    NOTIFICATION_TO_EMAIL = os.environ.get('NOTIFICATION_TO_EMAIL')

    # AI Assistant (Optional)
    OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
    OPENAI_MODEL = os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')
    OPENAI_BASE_URL = os.environ.get('OPENAI_BASE_URL')
    AI_RATE_MIN_INTERVAL_SECONDS = int(os.environ.get('AI_RATE_MIN_INTERVAL_SECONDS', 3))
    AI_RATE_WINDOW_SECONDS = int(os.environ.get('AI_RATE_WINDOW_SECONDS', 60))
    AI_RATE_MAX_REQUESTS_PER_WINDOW = int(os.environ.get('AI_RATE_MAX_REQUESTS_PER_WINDOW', 20))
    ADMIN_EMAIL_2FA_ENABLED = os.environ.get('ADMIN_EMAIL_2FA_ENABLED', 'false').lower() in ['true', 'on', '1']
    ADMIN_OTP_EXPIRY_MINUTES = int(os.environ.get('ADMIN_OTP_EXPIRY_MINUTES', 5))
    ADMIN_OTP_LENGTH = int(os.environ.get('ADMIN_OTP_LENGTH', 6))
    LOGIN_RATE_LIMIT_ENABLED = os.environ.get('LOGIN_RATE_LIMIT_ENABLED', 'true').lower() in ['true', 'on', '1']
    LOGIN_RATE_WINDOW_SECONDS = int(os.environ.get('LOGIN_RATE_WINDOW_SECONDS', 300))
    LOGIN_RATE_MAX_ATTEMPTS_PER_IP = int(os.environ.get('LOGIN_RATE_MAX_ATTEMPTS_PER_IP', 25))
    LOGIN_RATE_MIN_INTERVAL_SECONDS = int(os.environ.get('LOGIN_RATE_MIN_INTERVAL_SECONDS', 1))
    SUBMIT_RATE_LIMIT_ENABLED = _env_bool('SUBMIT_RATE_LIMIT_ENABLED', 'true')
    SUBMIT_RATE_WINDOW_SECONDS = int(os.environ.get('SUBMIT_RATE_WINDOW_SECONDS', 300))
    SUBMIT_RATE_MAX_ATTEMPTS_PER_IP = int(os.environ.get('SUBMIT_RATE_MAX_ATTEMPTS_PER_IP', 20))

    # Optional SMS notifications (MSG91, Fast2SMS, or Twilio REST)
    SMS_ENABLED = os.environ.get('SMS_ENABLED', 'false').lower() in ['true', 'on', '1']
    SMS_PROVIDER = os.environ.get('SMS_PROVIDER', 'msg91').strip().lower()
    MSG91_AUTH_KEY = os.environ.get('MSG91_AUTH_KEY')
    MSG91_SENDER_ID = os.environ.get('MSG91_SENDER_ID')
    MSG91_ROUTE = os.environ.get('MSG91_ROUTE', '4')
    MSG91_COUNTRY = os.environ.get('MSG91_COUNTRY', '91')
    FAST2SMS_API_KEY = os.environ.get('FAST2SMS_API_KEY')
    FAST2SMS_SENDER_ID = os.environ.get('FAST2SMS_SENDER_ID')
    TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
    TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
    TWILIO_FROM_NUMBER = os.environ.get('TWILIO_FROM_NUMBER')
    SMS_NOTIFICATION_TO = os.environ.get('SMS_NOTIFICATION_TO')
    SMS_TEMPLATE_COMPLAINT_ACK = os.environ.get('SMS_TEMPLATE_COMPLAINT_ACK')
    SMS_TEMPLATE_STATUS_UPDATE = os.environ.get('SMS_TEMPLATE_STATUS_UPDATE')
    SMS_TEMPLATE_ESCALATION = os.environ.get('SMS_TEMPLATE_ESCALATION')
    WHATSAPP_ENABLED = os.environ.get('WHATSAPP_ENABLED', 'false').lower() in ['true', 'on', '1']

    # Runtime performance guard for SLA recalculation on read-heavy pages
    SLA_CHECK_INTERVAL_SECONDS = int(os.environ.get('SLA_CHECK_INTERVAL_SECONDS', 20))
    
    @staticmethod
    def init_app(app):
        """Initialize application with this config."""
        # Ensure upload directory exists
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        
        # Ensure instance directory exists
        os.makedirs(app.instance_path, exist_ok=True)


class DevelopmentConfig(Config):
    """Development configuration with SQLite."""
    DEBUG = True
    
    SQLALCHEMY_DATABASE_URI = os.environ.get('DEV_DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'instance', 'civikindia_dev.db')
    
    @classmethod
    def init_app(cls, app):
        super().init_app(app)
        app.config['SESSION_COOKIE_SECURE'] = False


class TestingConfig(Config):
    """Testing configuration with in-memory SQLite."""
    TESTING = True
    DEBUG = True
    
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    WTF_CSRF_ENABLED = False  # Disable CSRF for testing
    SLA_CHECK_INTERVAL_SECONDS = 0  # Always run in tests for determinism
    PUBLIC_API_CACHE_SECONDS = 0
    LOGIN_RATE_LIMIT_ENABLED = False
    SUBMIT_RATE_LIMIT_ENABLED = False
    EVIDENCE_STORAGE_PROVIDER = 'local'
    GOOGLE_DRIVE_BACKUP_ENABLED = False
    
    # Use a project-local temporary upload folder for cross-platform tests.
    UPLOAD_FOLDER = os.path.join(basedir, 'instance', 'test_uploads')
    
    @classmethod
    def init_app(cls, app):
        super().init_app(app)


class ProductionConfig(Config):
    """
    Production configuration.
    Supports both MySQL (VPS) and PostgreSQL (Render) transparently.
    """
    DEBUG = False
    TESTING = False
    
    # Enhanced security for production
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PREFERRED_URL_SCHEME = 'https'
    TRUST_PROXY_HEADERS = True
    
    @classmethod
    def init_app(cls, app):
        """Initialize with database URI detection."""
        super().init_app(app)
        cls._validate_required_environment()
        
        uri = os.environ.get('DATABASE_URL', '')
        
        # Render gives postgres://, SQLAlchemy needs postgresql://
        if uri.startswith('postgres://'):
            uri = uri.replace('postgres://', 'postgresql://', 1)
        
        # VPS MySQL — build URI from parts
        if not uri and os.environ.get('MYSQL_HOST'):
            mysql_host = os.environ.get('MYSQL_HOST', 'localhost')
            mysql_user = os.environ.get('MYSQL_USER', 'civikindia_user')
            mysql_password = os.environ.get('MYSQL_PASSWORD', '')
            mysql_db = os.environ.get('MYSQL_DB', 'civikindia')
            
            uri = (
                f"mysql+pymysql://{mysql_user}"
                f":{mysql_password}"
                f"@{mysql_host}"
                f"/{mysql_db}?charset=utf8mb4"
            )
        
        if not uri:
            raise RuntimeError('DATABASE_URL is required in production.')
        if uri.startswith('sqlite'):
            raise RuntimeError('SQLite DATABASE_URL is not allowed in production.')
        if not uri.startswith(('postgresql://', 'mysql://', 'mysql+pymysql://')):
            raise RuntimeError(
                'DATABASE_URL must be a SQL database URL such as '
                'postgresql://... or mysql+pymysql://... in production.'
            )
        
        app.config['SQLALCHEMY_DATABASE_URI'] = uri
        if uri.startswith('postgresql'):
            app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
                'pool_pre_ping': True,
                'pool_recycle': int(os.environ.get('SQLALCHEMY_POOL_RECYCLE', 1800)),
                'pool_size': int(os.environ.get('SQLALCHEMY_POOL_SIZE', 5)),
                'max_overflow': int(os.environ.get('SQLALCHEMY_MAX_OVERFLOW', 2)),
            }
        
        # Log configuration (without sensitive data)
        db_type = 'PostgreSQL' if 'postgresql' in uri else ('MySQL' if 'mysql' in uri else 'SQLite')
        app.logger.info(f'Production database: {db_type}')

    @classmethod
    def _validate_required_environment(cls):
        required = [
            'SECRET_KEY',
            'DATABASE_URL',
            'EVIDENCE_ENCRYPTION_KEY',
            'AUDIT_HMAC_SECRET',
            'DEFAULT_ADMIN_PASSWORD',
            'DEFAULT_OFFICER_PASSWORD',
            'R2_ACCOUNT_ID',
            'R2_ACCESS_KEY_ID',
            'R2_SECRET_ACCESS_KEY',
            'R2_BUCKET_NAME',
            'R2_ENDPOINT_URL',
        ]
        missing = [name for name in required if not os.environ.get(name)]
        insecure = []
        if os.environ.get('SECRET_KEY') == 'dev-secret-key-change-in-production':
            insecure.append('SECRET_KEY')
        if os.environ.get('EVIDENCE_ENCRYPTION_KEY') == 'dev-evidence-encryption-key-persistent':
            insecure.append('EVIDENCE_ENCRYPTION_KEY')
        if os.environ.get('AUDIT_HMAC_SECRET') == 'dev-audit-hmac-secret-persistent-key':
            insecure.append('AUDIT_HMAC_SECRET')
        for name in required:
            value = os.environ.get(name, '')
            if value.lower().startswith('replace-'):
                insecure.append(name)
        if os.environ.get('DEFAULT_ADMIN_PASSWORD') == 'Admin@1234':
            insecure.append('DEFAULT_ADMIN_PASSWORD')
        if os.environ.get('DEFAULT_OFFICER_PASSWORD') == 'Officer@1234':
            insecure.append('DEFAULT_OFFICER_PASSWORD')
        insecure = sorted(set(insecure))

        if cls.GOOGLE_DRIVE_BACKUP_ENABLED:
            if not cls.GOOGLE_DRIVE_FOLDER_ID:
                missing.append('GOOGLE_DRIVE_FOLDER_ID')
            if not (cls.GOOGLE_SERVICE_ACCOUNT_JSON or cls.GOOGLE_APPLICATION_CREDENTIALS):
                missing.append('GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_APPLICATION_CREDENTIALS')

        if missing or insecure:
            problems = []
            if missing:
                problems.append('missing: ' + ', '.join(missing))
            if insecure:
                problems.append('replace development defaults: ' + ', '.join(insecure))
            raise RuntimeError('Production environment is not configured safely (' + '; '.join(problems) + ').')


# Configuration dictionary for easy access
config = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}
