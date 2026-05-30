"""
CivikIndia Utility Modules
Helper functions for audit logging, tracking ID generation, and file handling.
"""
import os
import io
import subprocess
import tempfile
import uuid
import secrets
import string
import time
import hashlib
import threading
from functools import wraps
from flask import session, redirect, url_for, flash, request, current_app, abort, Response
from werkzeug.utils import secure_filename
import magic
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

from app import db
from app.clock import utc_now
from app.models import AuditLog

_sla_guard_lock = threading.Lock()


# =============================================================================
# DECORATORS - Role-Based Access Control
# =============================================================================

def login_required(f):
    """Decorator to require user login."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('auth.login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Decorator to require admin role."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('auth.login', next=request.url))
        if session.get('role') != 'admin':
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


def officer_required(f):
    """Decorator to require officer or admin role."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('auth.login', next=request.url))
        if session.get('role') not in ['officer', 'zonal_officer', 'commissioner', 'admin']:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


def maybe_run_sla_escalations(force=False):
    """
    Run SLA escalation checks with a per-process interval guard.
    Prevents full-table scans from running on every request.
    """
    from app.models import Complaint

    if force:
        return Complaint.apply_sla_escalations()

    interval = int(current_app.config.get('SLA_CHECK_INTERVAL_SECONDS', 20))
    if interval <= 0:
        return Complaint.apply_sla_escalations()

    now_ts = time.time()
    bucket = current_app.extensions.setdefault('civikindia_runtime', {})

    with _sla_guard_lock:
        last_run_ts = bucket.get('sla_last_run_ts', 0)
        if now_ts - last_run_ts < interval:
            return 0
        bucket['sla_last_run_ts'] = now_ts

    return Complaint.apply_sla_escalations()


# =============================================================================
# TRACKING ID GENERATOR
# =============================================================================

def generate_tracking_id():
    """
    Generate a unique tamper-proof tracking ID.
    Format: CIVIK/YYYY/MM/8_RANDOM_CHARS
    Example: CIVIK/2026/05/3A9F2K1X
    
    Ensures uniqueness by checking database.
    """
    from app.models import Complaint
    
    now = utc_now()
    prefix = f"CIVIK/{now.year}/{now.month:02d}/"
    attempts = 0
    max_attempts = 100
    
    while attempts < max_attempts:
        random_part = ''.join(secrets.choice(string.ascii_uppercase + string.digits) 
                              for _ in range(8))
        tracking_id = f"{prefix}{random_part}"
        
        existing = Complaint.query.filter_by(tracking_id=tracking_id).first()
        if not existing:
            return tracking_id
        
        attempts += 1
    
    timestamp = utc_now().strftime('%H%M%S')
    random_part = ''.join(secrets.choice(string.ascii_uppercase + string.digits) 
                          for _ in range(2))
    return f"{prefix}{timestamp}{random_part}"


# =============================================================================
# FILE UPLOAD HANDLER
# =============================================================================

def allowed_file(filename):
    """Check if file extension is allowed."""
    if '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    if ext in current_app.config.get('BLOCKED_UPLOAD_EXTENSIONS', set()):
        return False
    return ext in current_app.config['ALLOWED_EXTENSIONS']


def get_file_extension(filename):
    """Safely extract file extension."""
    if '.' not in filename:
        return ''
    return filename.rsplit('.', 1)[1].lower()


def scan_upload_for_malware(file_data):
    """Optionally scan upload bytes with ClamAV before encrypted storage."""
    if not current_app.config.get('CLAMAV_ENABLED', False):
        return True, None

    scanner = current_app.config.get('CLAMAV_SCANNER_PATH', 'clamscan')
    timeout = int(current_app.config.get('CLAMAV_SCAN_TIMEOUT_SECONDS', 30))
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            dir=current_app.instance_path,
            prefix='civikindia_scan_'
        ) as temp_file:
            temp_file.write(file_data)
            temp_path = temp_file.name

        result = subprocess.run(
            [scanner, '--no-summary', temp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False
        )
        scan_output = (result.stdout or result.stderr or '').strip()
        if result.returncode == 0:
            return True, None
        if result.returncode == 1:
            current_app.logger.warning('ClamAV rejected uploaded evidence: %s', scan_output)
            return False, 'Evidence file failed malware scanning.'

        current_app.logger.error('ClamAV scanner error: %s', scan_output)
        return False, 'Unable to complete malware scan. Please try again.'
    except FileNotFoundError:
        current_app.logger.error('ClamAV scanner not found at %s', scanner)
        return False, 'Malware scanner is not available. Please try again later.'
    except subprocess.TimeoutExpired:
        current_app.logger.error('ClamAV scan timed out after %s seconds', timeout)
        return False, 'Malware scan timed out. Please try again.'
    except Exception as exc:
        current_app.logger.exception('ClamAV scan failed: %s', exc)
        return False, 'Unable to scan evidence file. Please try again.'
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass


def _allowed_mime_for_extension(extension, mime_type):
    allowed_by_extension = {
        'pdf': {'application/pdf'},
        'jpg': {'image/jpeg'},
        'jpeg': {'image/jpeg'},
        'png': {'image/png'},
        'webp': {'image/webp'},
        'mp4': {'video/mp4', 'application/mp4'},
        'mov': {'video/quicktime', 'video/mp4'},
        'mp3': {'audio/mpeg', 'audio/mp3'},
        'wav': {'audio/wav', 'audio/x-wav', 'audio/wave'},
        'txt': {'text/plain'},
        'doc': {'application/msword', 'application/octet-stream'},
        'docx': {
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/zip',
            'application/octet-stream',
        },
    }
    return mime_type in allowed_by_extension.get(extension, set())


def _evidence_key_bytes():
    key_value = current_app.config.get('EVIDENCE_ENCRYPTION_KEY')
    if not key_value:
        return None
    try:
        if len(key_value) == 64:
            return bytes.fromhex(key_value)
    except ValueError:
        pass
    return hashlib.sha256(key_value.encode('utf-8')).digest()


def _encrypt_evidence_bytes(file_data):
    key = _evidence_key_bytes()
    if not key:
        return file_data, None, False
    iv = secrets.token_bytes(16)
    cipher = Cipher(algorithms.AES(key), modes.CFB(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    return encryptor.update(file_data) + encryptor.finalize(), iv.hex(), True


def decrypt_evidence_bytes(file_data, encryption_iv):
    key = _evidence_key_bytes()
    if not key:
        raise ValueError('Evidence encryption key is not configured.')
    iv = bytes.fromhex(encryption_iv)
    cipher = Cipher(algorithms.AES(key), modes.CFB(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    return decryptor.update(file_data) + decryptor.finalize()


def save_uploaded_file(file, subfolder='evidence', complaint_id=None):
    """
    Securely save an uploaded file with UUID prefix, MIME validation, and AES-256 encryption.
    
    Args:
        file: Flask FileStorage object
        subfolder: Subdirectory within uploads
    
    Returns:
        tuple: (success: bool, dict/str) - Dict contains file metadata if success, else error string.
    """
    if not file or file.filename == '':
        return False, 'No file selected'
    
    file_data = file.read()
    if len(file_data) == 0:
        return False, 'File is empty'

    max_bytes = int(current_app.config.get('MAX_CONTENT_LENGTH', 16 * 1024 * 1024))
    if len(file_data) > max_bytes:
        max_mb = current_app.config.get('MAX_UPLOAD_MB', max_bytes // (1024 * 1024))
        return False, f'File exceeds the maximum allowed size of {max_mb}MB'

    extension = get_file_extension(file.filename)
    if extension in current_app.config.get('BLOCKED_UPLOAD_EXTENSIONS', set()):
        return False, 'This file type is not allowed for security reasons.'
    if not allowed_file(file.filename):
        allowed = ', '.join(sorted(current_app.config['ALLOWED_EXTENSIONS']))
        return False, f'Invalid file extension. Allowed: {allowed}'
        
    mime_type = magic.from_buffer(file_data, mime=True)
    if not _allowed_mime_for_extension(extension, mime_type):
        return False, f'Invalid file type ({mime_type}) for .{extension} evidence.'

    scan_ok, scan_error = scan_upload_for_malware(file_data)
    if not scan_ok:
        return False, scan_error

    original_filename = secure_filename(file.filename) or f'evidence.{extension}'
    file_hash = hashlib.sha256(file_data).hexdigest()

    try:
        from app.storage import generate_evidence_storage_key, get_storage

        encrypted_data, encryption_iv, encrypted = _encrypt_evidence_bytes(file_data)
        storage_key = generate_evidence_storage_key(complaint_id, extension, encrypted=encrypted)
        storage = get_storage()
        storage.save_file(
            io.BytesIO(encrypted_data),
            storage_key,
            content_type=mime_type,
            metadata={
                'sha256': file_hash,
                'encrypted': str(encrypted).lower(),
                'complaint_id': complaint_id or '',
            },
        )
        provider = current_app.config.get('EVIDENCE_STORAGE_PROVIDER', 'local')
        bucket = current_app.config.get('R2_BUCKET_NAME') if provider == 'r2' else None
        legacy_filename = os.path.basename(storage_key)
        
        return True, {
            'relative_path': storage_key,
            'original_filename': original_filename,
            'safe_filename': original_filename,
            'filename': legacy_filename,
            'file_extension': extension,
            'mime_type': mime_type,
            'file_size': len(file_data),
            'byte_size': len(file_data),
            'encryption_iv': encryption_iv,
            'file_hash_sha256': file_hash,
            'sha256_hash': file_hash,
            'storage_provider': provider,
            'storage_bucket': bucket,
            'storage_key': storage_key,
            'drive_backup_status': (
                'pending' if current_app.config.get('GOOGLE_DRIVE_BACKUP_ENABLED') else 'disabled'
            ),
            'encrypted': encrypted,
        }
    except Exception as e:
        current_app.logger.error(f'File upload error: {str(e)}')
        return False, 'Error saving evidence file. Please try again.'


def delete_uploaded_file(relative_path, storage_provider=None):
    """
    Delete an uploaded file.
    
    Args:
        relative_path: Path relative to UPLOAD_FOLDER
    
    Returns:
        bool: True if deleted or not found, False on error
    """
    if not relative_path:
        return True
    
    try:
        from app.storage import get_storage
        get_storage(storage_provider).delete_file(str(relative_path).replace('\\', '/'))
        return True
    except Exception as e:
        current_app.logger.error(f'File deletion error: {str(e)}')
        return False


def evidence_download_response(evidence_file, tracking_id):
    """Build a secure backend-streamed download response for an EvidenceFile."""
    from app.storage import get_storage

    storage_key = evidence_file.storage_key or evidence_file.storage_path
    storage_provider = evidence_file.storage_provider or 'local'
    storage = get_storage(storage_provider)
    with storage.open_file(storage_key) as stored_file:
        payload = stored_file.read()

    if evidence_file.encryption_iv:
        payload = decrypt_evidence_bytes(payload, evidence_file.encryption_iv)

    download_name = evidence_file.original_filename or f'evidence_{tracking_id}'
    mime_type = evidence_file.mime_type or 'application/octet-stream'
    response = Response(payload, mimetype=mime_type)
    response.headers['Content-Disposition'] = f'attachment; filename="{download_name}"'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store, private'
    return response


def evidence_preview_response(evidence_file, tracking_id):
    """
    Build a secure backend-streamed INLINE response for an EvidenceFile.
    Used for in-browser preview (images, PDFs, videos).
    Unlike evidence_download_response, this uses Content-Disposition: inline.
    """
    from app.storage import get_storage

    storage_key = evidence_file.storage_key or evidence_file.storage_path
    storage_provider = evidence_file.storage_provider or 'local'
    storage = get_storage(storage_provider)
    with storage.open_file(storage_key) as stored_file:
        payload = stored_file.read()

    if evidence_file.encryption_iv:
        payload = decrypt_evidence_bytes(payload, evidence_file.encryption_iv)

    # Safe filename for Content-Disposition
    safe_name = evidence_file.original_filename or f'evidence_{tracking_id}'
    mime_type = evidence_file.mime_type or 'application/octet-stream'

    response = Response(payload, mimetype=mime_type)
    # KEY DIFFERENCE: inline instead of attachment — allows browser rendering
    response.headers['Content-Disposition'] = f'inline; filename="{safe_name}"'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store, private'
    # Security: prevent embedding in third-party frames
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return response


# =============================================================================
# AUDIT LOG HELPER
# =============================================================================

def log_action(action, details=None, user=None):
    """
    Create an audit log entry.
    
    Args:
        action: Short description of action (e.g., 'LOGIN', 'STATUS_UPDATE')
        details: Additional details (JSON-serializable)
        user: User object (optional, uses session if not provided)
    """
    # Get user info
    if user is None:
        user_id = session.get('user_id')
        username = session.get('username', 'anonymous')
        role = session.get('role', 'guest')
    else:
        user_id = user.id
        username = user.username
        role = user.role
    
    # Store IP address only for authenticated staff actions.
    ip_address = None
    if role in {'admin', 'officer', 'zonal_officer', 'commissioner'}:
        ip_address = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ip_address and ',' in ip_address:
            ip_address = ip_address.split(',')[0].strip()
    
    # Convert details to string if needed
    if details and not isinstance(details, str):
        import json
        try:
            details = json.dumps(details)
        except:
            details = str(details)
    
    # Create audit log entry
    AuditLog.create_entry(
        user_id=user_id,
        username=username,
        role=role,
        action=action,
        details=details,
        ip_address=ip_address
    )


# =============================================================================
# FORMATTING HELPERS
# =============================================================================

def format_status_badge(status):
    """Return Bootstrap badge class for status."""
    badges = {
        'Pending': 'bg-warning text-dark',
        'Under Review': 'bg-info text-dark',
        'Action Taken': 'bg-primary',
        'Delayed': 'bg-danger',
        'Reopened': 'bg-secondary',
        'Closed': 'bg-success'
    }
    return badges.get(status, 'bg-secondary')


def format_status_icon(status):
    """Return FontAwesome icon class for status."""
    icons = {
        'Pending': 'fa-clock',
        'Under Review': 'fa-search',
        'Action Taken': 'fa-tasks',
        'Delayed': 'fa-triangle-exclamation',
        'Reopened': 'fa-rotate-left',
        'Closed': 'fa-check-circle'
    }
    return icons.get(status, 'fa-question-circle')


def truncate_text(text, length=100):
    """Truncate text to specified length with ellipsis."""
    if not text:
        return ''
    if len(text) <= length:
        return text
    return text[:length].rsplit(' ', 1)[0] + '...'


def analyze_complaint_text(description):
    """
    Lightweight AI-like text analysis for urgency, category, and sentiment.
    Used as fallback even when external AI APIs are unavailable.
    """
    text = (description or '').lower()

    urgent_keywords = [
        'corruption', 'bribe', 'threat', 'danger', 'health hazard',
        'sewage', 'outbreak', 'collapse', 'emergency', 'unsafe'
    ]
    negative_keywords = [
        'bad', 'worst', 'delay', 'ignored', 'no action', 'problem',
        'complaint', 'issue', 'hazard', 'unsafe'
    ]
    positive_keywords = ['resolved', 'improved', 'good', 'satisfied']

    category_rules = {
        'Water Supply': ['water', 'pipeline', 'tap', 'leakage'],
        'Roads & Infrastructure': ['road', 'pothole', 'street light', 'drainage'],
        'Public Health': ['mosquito', 'health', 'hygiene', 'toilet'],
        'Electricity': ['power', 'electricity', 'voltage', 'meter'],
        'Sanitation': ['garbage', 'waste', 'sewage', 'cleaning']
    }

    is_urgent = any(keyword in text for keyword in urgent_keywords)
    priority = 'High' if is_urgent else 'Normal'

    negative_score = sum(1 for keyword in negative_keywords if keyword in text)
    positive_score = sum(1 for keyword in positive_keywords if keyword in text)
    sentiment = 'negative'
    if positive_score > negative_score:
        sentiment = 'positive'
    elif negative_score == positive_score:
        sentiment = 'neutral'

    detected_category = None
    best_score = 0
    for category, keywords in category_rules.items():
        score = sum(1 for keyword in keywords if keyword in text)
        if score > best_score:
            detected_category = category
            best_score = score

    return {
        'priority': priority,
        'urgent': is_urgent,
        'sentiment': sentiment,
        'category': detected_category
    }


# =============================================================================
# VALIDATION HELPERS
# =============================================================================

def validate_tracking_id(tracking_id):
    """
    Accepts legacy formats (MIBxxxxxxxx, MIBSP/YYYY/MM/XXXXXXXX) and new format (CIVIK/YYYY/MM/XXXXXXXX).
    
    Args:
        tracking_id: Tracking ID string to validate
    
    Returns:
        bool: True if valid format
    """
    if not tracking_id:
        return False
    
    # New format: CIVIK/YYYY/MM/XXXXXXXX
    # Also accept legacy MIBSP/ prefix from old records
    if tracking_id.startswith('CIVIK/') or tracking_id.startswith('MIBSP/'):
        parts = tracking_id.split('/')
        if len(parts) != 4:
            return False
        _, year, month, random_part = parts
        if not (year.isdigit() and len(year) == 4):
            return False
        if not (month.isdigit() and 1 <= int(month) <= 12):
            return False
        if not (random_part.isalnum() and random_part == random_part.upper() and len(random_part) >= 2):
            return False
        return True
    
    # Legacy format: MIB + 8 alphanumeric chars
    if not (tracking_id.startswith('CIV') or tracking_id.startswith('MIB')):
        return False
    if len(tracking_id) < 11:
        return False
    random_part = tracking_id[3:]
    return random_part.isalnum() and random_part == random_part.upper()


def normalize_tracking_id(tracking_id):
    """
    Normalize citizen-entered tracking IDs for lookup.

    Citizens often copy IDs from the confirmation page or type them manually.
    This keeps the displayed ID format unchanged while tolerating casing and
    accidental whitespace around separators.
    """
    if not tracking_id:
        return ''
    return ''.join(str(tracking_id).strip().upper().split())


# =============================================================================
# RECAPTCHA VERIFICATION
# =============================================================================

def verify_recaptcha(token):
    """
    Verify a Google reCAPTCHA v3 token server-side.
    
    Args:
        token: The reCAPTCHA response token from the frontend.
    
    Returns:
        tuple: (is_valid: bool, score: float or None)
        If no secret key is configured, returns (True, None) — dev mode bypass.
    """
    import urllib.request
    import urllib.parse
    import json
    
    secret_key = current_app.config.get('RECAPTCHA_SECRET_KEY', '')
    if not secret_key:
        # Dev mode — no reCAPTCHA configured, allow through
        return True, None
    
    if not token:
        return False, 0.0
    
    try:
        data = urllib.parse.urlencode({
            'secret': secret_key,
            'response': token,
            'remoteip': request.remote_addr
        }).encode('utf-8')
        
        req = urllib.request.Request(
            'https://www.google.com/recaptcha/api/siteverify',
            data=data,
            method='POST'
        )
        
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        
        success = result.get('success', False)
        score = result.get('score', 0.0)
        
        if success and score >= 0.5:
            return True, score
        
        current_app.logger.warning(
            f'reCAPTCHA failed: success={success}, score={score}, '
            f'errors={result.get("error-codes", [])}'
        )
        return False, score
        
    except Exception as e:
        current_app.logger.error(f'reCAPTCHA verification error: {str(e)}')
        # Fail open to avoid blocking users if Google is unreachable
        return True, None


def run_async(func, *args, **kwargs):
    """Run a function in a background daemon thread with the current app and request contexts."""
    from flask import has_app_context, has_request_context, request

    if not has_app_context():
        # Fallback to direct synchronous execution if no app context
        return func(*args, **kwargs)

    if current_app.config.get('TESTING') or current_app.testing:
        # Run synchronously in tests so assertions work deterministically
        return func(*args, **kwargs)

    app = current_app._get_current_object()
    environ = request.environ if has_request_context() else None

    def wrapper():
        with app.app_context():
            if environ:
                with app.request_context(environ):
                    try:
                        func(*args, **kwargs)
                    except Exception as e:
                        app.logger.error(f"Error in background task {func.__name__}: {e}")
            else:
                try:
                    func(*args, **kwargs)
                except Exception as e:
                    app.logger.error(f"Error in background task {func.__name__}: {e}")

    threading.Thread(target=wrapper, daemon=True).start()
