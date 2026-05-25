"""
Civik India Authentication Routes
Staff login, logout, password reset, and 2FA.
"""
import hashlib
import secrets
import string
import time
from collections import deque
import threading
from urllib.parse import urljoin, urlparse
from flask import Blueprint, render_template, request, flash, redirect, url_for, session, current_app
from werkzeug.security import generate_password_hash, check_password_hash
import pyotp
import qrcode
import qrcode.image.svg
import io
import json

from app import db
from app.clock import utc_epoch_seconds
from app.models import User
from app.utils import log_action, login_required
from app.tasks import send_system_email

auth_bp = Blueprint('auth', __name__)
_login_rate_lock = threading.Lock()
_login_rate_buckets = {}
RESET_TOKEN_EXPIRY_SECONDS = 3600


def _resolve_next_target():
    """Read postback-safe redirect target from query string or form body."""
    return (request.form.get('next') or request.args.get('next') or '').strip()


def _is_safe_redirect(target):
    """Allow redirects only to same-host URLs."""
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return (
        test_url.scheme in {'http', 'https'}
        and ref_url.netloc == test_url.netloc
    )


def _set_authenticated_session(user):
    """Store authenticated user in session with session ID regeneration."""
    # Preserve any data we need across the session regeneration
    _pending_next = session.get('pending_2fa_next', '')
    # Clear the old session to force Flask to issue a new session ID
    # (prevents session fixation attacks)
    session.clear()
    session.permanent = True
    session['user_id'] = user.id
    session['username'] = user.username
    session['role'] = user.role
    session['department_id'] = user.department_id


def _clear_pending_otp():
    """Clear temporary OTP challenge values from session."""
    session.pop('pending_otp_user_id', None)
    session.pop('pending_otp_hash', None)
    session.pop('pending_otp_expires_at', None)
    session.pop('pending_otp_next', None)


def _hash_reset_token(token):
    """Return a stable digest for a high-entropy password reset token."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _find_staff_account(identifier):
    """Find a staff account by username first, then by email."""
    if not identifier:
        return None
    user = User.query.filter_by(username=identifier).first()
    if user:
        return user
    return User.query.filter_by(email=identifier).first()


def _get_client_ip():
    """Resolve originating IP for login rate limiting."""
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr or 'unknown'


def _enforce_login_rate_limit():
    """Apply lightweight per-IP login throttling."""
    if not current_app.config.get('LOGIN_RATE_LIMIT_ENABLED', True):
        return True, None

    min_interval = int(current_app.config.get('LOGIN_RATE_MIN_INTERVAL_SECONDS', 1))
    window_seconds = int(current_app.config.get('LOGIN_RATE_WINDOW_SECONDS', 300))
    max_requests = int(current_app.config.get('LOGIN_RATE_MAX_ATTEMPTS_PER_IP', 25))
    client_ip = _get_client_ip()
    now_ts = time.time()

    with _login_rate_lock:
        bucket = _login_rate_buckets.get(client_ip)
        if bucket is None:
            bucket = {'last_ts': 0.0, 'hits': deque()}
            _login_rate_buckets[client_ip] = bucket

        if now_ts - bucket['last_ts'] < min_interval:
            return False, 'Please wait a moment before trying to log in again.'

        hits = bucket['hits']
        while hits and now_ts - hits[0] > window_seconds:
            hits.popleft()

        if len(hits) >= max_requests:
            return False, 'Too many login attempts from this network. Please try again later.'

        hits.append(now_ts)
        bucket['last_ts'] = now_ts

        if len(_login_rate_buckets) > 5000:
            stale_cutoff = now_ts - (window_seconds * 2)
            stale = [
                ip for ip, data in _login_rate_buckets.items()
                if not data['hits'] or data['hits'][-1] < stale_cutoff
            ]
            for ip in stale[:1000]:
                _login_rate_buckets.pop(ip, None)

    return True, None


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Request a password reset link for an active staff account."""
    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        user = _find_staff_account(identifier)

        if user and user.is_active and user.email:
            token = secrets.token_urlsafe(32)
            user.reset_token = _hash_reset_token(token)
            user.reset_token_expires_at = utc_epoch_seconds() + RESET_TOKEN_EXPIRY_SECONDS
            try:
                db.session.commit()
                reset_url = url_for('auth.reset_password', token=token, _external=True)
                subject = 'Civik India — Password Reset'
                body = (
                    f'Hello {user.username},\n\n'
                    'A password reset was requested for your Civik India staff account.\n'
                    f'Use this link within 1 hour: {reset_url}\n\n'
                    'If you did not request this, you can safely ignore this email.'
                )
                sent, send_error = send_system_email(subject, body, [user.email])
                if not sent:
                    log_action('PASSWORD_RESET_MAIL_FAILED', details={
                        'username': user.username,
                        'error': send_error or 'unknown'
                    }, user=user)
            except Exception:
                db.session.rollback()
                current_app.logger.exception('Password reset request failed.')
        elif identifier:
            identifier_hash = hashlib.sha256(identifier.lower().encode('utf-8')).hexdigest()[:12]
            log_action('PASSWORD_RESET_REQUEST_IGNORED', details={'identifier_hash': identifier_hash})

        flash('If a matching active staff account exists, reset instructions have been sent.', 'info')
        return redirect(url_for('auth.login'))

    return render_template('auth/forgot_password.html')


@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """Reset a staff password using a valid reset token."""
    token_hash = _hash_reset_token(token or '')
    user = User.query.filter_by(reset_token=token_hash).first()
    now_ts = utc_epoch_seconds()

    if (
        not user
        or not user.is_active
        or not user.reset_token_expires_at
        or user.reset_token_expires_at < now_ts
    ):
        if user:
            user.reset_token = None
            user.reset_token_expires_at = None
            db.session.commit()
        flash('Password reset link is invalid or has expired.', 'danger')
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not new_password or not confirm_password:
            flash('Both password fields are required.', 'danger')
            return render_template('auth/reset_password.html', token=token)

        if len(new_password) < 12:
            flash('New password must be at least 12 characters.', 'danger')
            return render_template('auth/reset_password.html', token=token)

        if new_password != confirm_password:
            flash('New password and confirmation do not match.', 'danger')
            return render_template('auth/reset_password.html', token=token)

        from app.utils.password_policy import validate_password
        is_valid, policy_errors = validate_password(new_password, username=user.username)
        if not is_valid:
            for err in policy_errors:
                flash(err, 'danger')
            return render_template('auth/reset_password.html', token=token)

        try:
            user.set_password(new_password)
            user.reset_token = None
            user.reset_token_expires_at = None
            user.failed_login_attempts = 0
            user.locked_until = None
            db.session.commit()
            log_action('PASSWORD_RESET_COMPLETED', user=user)
            flash('Password reset successfully. Please log in with your new password.', 'success')
            return redirect(url_for('auth.login'))
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Password reset failed.')
            flash('Error resetting password. Please try again.', 'danger')

    return render_template('auth/reset_password.html', token=token)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """
    User login page.
    Only for officers and admins - citizens don't need accounts.
    """
    # Redirect if already logged in
    if 'user_id' in session:
        if session.get('role') == 'admin':
            return redirect(url_for('admin.dashboard'))
        else:
            return redirect(url_for('officer.dashboard'))
    
    next_page = _resolve_next_target()

    if request.method == 'POST':
        allowed, rate_message = _enforce_login_rate_limit()
        if not allowed:
            flash(rate_message, 'danger')
            log_action('LOGIN_RATE_LIMITED', details={'username': request.form.get('username', '').strip()})
            return render_template('auth/login.html', next=next_page)

        _clear_pending_otp()
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        if not username or not password:
            flash('Please enter both username and password.', 'warning')
            return render_template('auth/login.html', next=next_page)
        
        # Find user
        user = User.query.filter_by(username=username).first()

        if user and user.is_locked():
            flash('Account temporarily locked due to repeated failed attempts. Please try again later.', 'danger')
            log_action(
                'LOGIN_BLOCKED_LOCKED',
                details={'username': username}
            )
            return render_template('auth/login.html', next=next_page)
        
        if user and user.check_password(password):
            if not user.is_active:
                flash('Your account has been deactivated. Contact an administrator.', 'danger')
                log_action('LOGIN_FAILED_INACTIVE', 
                          details={'username': username}, user=user)
                return render_template('auth/login.html', next=next_page)

            if user.is_admin() and current_app.config.get('ADMIN_EMAIL_2FA_ENABLED', False):
                if not user.email:
                    flash('Admin account email is required for OTP verification.', 'danger')
                    return render_template('auth/login.html', next=next_page)

                otp_length = int(current_app.config.get('ADMIN_OTP_LENGTH', 6))
                otp_expiry_minutes = int(current_app.config.get('ADMIN_OTP_EXPIRY_MINUTES', 5))
                otp_code = ''.join(secrets.choice(string.digits) for _ in range(otp_length))
                otp_expires_at = utc_epoch_seconds() + (otp_expiry_minutes * 60)

                subject = 'Civik India — Admin Login OTP'
                body = (
                    f'Your OTP for Civik India admin login is: {otp_code}\n'
                    f'This code expires in {otp_expiry_minutes} minutes.'
                )
                sent, send_error = send_system_email(subject, body, [user.email])
                if not sent:
                    log_action('LOGIN_2FA_MAIL_FAILED', details={
                        'username': username,
                        'error': send_error or 'unknown'
                    }, user=user)
                    flash(
                        'Unable to send OTP email right now. '
                        'Verify MAIL settings and retry, or ask an admin to disable ADMIN_EMAIL_2FA_ENABLED.',
                        'danger'
                    )
                    return render_template('auth/login.html', next=next_page)

                _clear_pending_otp()
                session['pending_otp_user_id'] = user.id
                session['pending_otp_hash'] = generate_password_hash(
                    otp_code, method='pbkdf2:sha256', salt_length=8
                )
                session['pending_otp_expires_at'] = otp_expires_at
                session['pending_otp_next'] = next_page if _is_safe_redirect(next_page) else ''

                log_action('LOGIN_2FA_CHALLENGE_ISSUED', details={'username': username})
                flash('OTP sent to your admin email. Please verify to continue.', 'info')
                return redirect(url_for('auth.verify_admin_otp'))

            _set_authenticated_session(user)
            user.update_last_login()
            log_action('LOGIN_SUCCESS', user=user)
            flash(f'Welcome back, {user.username}!', 'success')

            if _is_safe_redirect(next_page):
                return redirect(next_page)

            if user.is_admin():
                if user.two_fa_enabled:
                    session['pending_2fa_user_id'] = user.id
                    session['pending_2fa_next'] = next_page
                    return redirect(url_for('auth.verify_2fa'))
                return redirect(url_for('admin.dashboard'))
            
            if user.two_fa_enabled:
                session['pending_2fa_user_id'] = user.id
                session['pending_2fa_next'] = next_page
                return redirect(url_for('auth.verify_2fa'))
                
            return redirect(url_for('officer.dashboard'))
        
        else:
            if user:
                user.register_failed_login()
                db.session.commit()
                reason = 'invalid_credentials'
                if user.is_locked():
                    reason = 'account_locked'
            else:
                reason = 'invalid_credentials'

            # Log failed login
            log_action('LOGIN_FAILED', 
                      details={'username': username, 'reason': reason})

            if reason == 'account_locked':
                flash('Too many failed attempts. Account locked for 15 minutes.', 'danger')
            else:
                flash('Invalid username or password.', 'danger')

    return render_template('auth/login.html', next=next_page)


@auth_bp.route('/verify-otp', methods=['GET', 'POST'])
def verify_admin_otp():
    """Verify admin email OTP when 2FA is enabled."""
    pending_user_id = session.get('pending_otp_user_id')
    pending_hash = session.get('pending_otp_hash')
    pending_expiry = session.get('pending_otp_expires_at')

    if not pending_user_id or not pending_hash or not pending_expiry:
        flash('No pending OTP verification found. Please log in again.', 'warning')
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        otp = (request.form.get('otp') or '').strip()
        if not otp:
            flash('Please enter the OTP code.', 'danger')
            return render_template('auth/verify_otp.html')

        if utc_epoch_seconds() > int(pending_expiry):
            _clear_pending_otp()
            flash('OTP expired. Please log in again.', 'danger')
            return redirect(url_for('auth.login'))

        if not check_password_hash(pending_hash, otp):
            flash('Invalid OTP. Please try again.', 'danger')
            return render_template('auth/verify_otp.html')

        user = db.session.get(User, pending_user_id)
        if not user or not user.is_active or not user.is_admin():
            _clear_pending_otp()
            flash('Unable to verify user session. Please log in again.', 'danger')
            return redirect(url_for('auth.login'))

        next_page = session.get('pending_otp_next', '')
        _clear_pending_otp()

        _set_authenticated_session(user)
        user.update_last_login()
        log_action('LOGIN_SUCCESS', user=user)
        flash(f'Welcome back, {user.username}!', 'success')

        if _is_safe_redirect(next_page):
            return redirect(next_page)
        return redirect(url_for('admin.dashboard'))

    return render_template('auth/verify_otp.html')


@auth_bp.route('/2fa/setup', methods=['GET', 'POST'])
@login_required
def setup_2fa():
    """Setup TOTP Two-Factor Authentication."""
    user = db.session.get(User, session['user_id'])
    
    if user.two_fa_enabled:
        flash('2FA is already enabled on your account.', 'info')
        return redirect(url_for('auth.profile'))
        
    if 'totp_secret' not in session:
        session['totp_secret'] = pyotp.random_base32()
        
    secret = session['totp_secret']
    
    if request.method == 'POST':
        otp = request.form.get('otp', '').strip()
        totp = pyotp.TOTP(secret)
        
        if totp.verify(otp):
            user.totp_secret = secret
            user.two_fa_enabled = True
            
            # Generate backup codes
            backup_codes_list = [''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8)) for _ in range(10)]
            hashed_codes = [generate_password_hash(code) for code in backup_codes_list]
            user.backup_codes = json.dumps(hashed_codes)
            
            db.session.commit()
            session.pop('totp_secret', None)
            log_action('2FA_ENABLED', user=user)
            
            # Show backup codes once
            return render_template('auth/2fa_backup_codes.html', backup_codes=backup_codes_list)
        else:
            flash('Invalid OTP code. Please try again.', 'danger')
            
    # Generate QR Code
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(name=user.username, issuer_name="Civik India")
    
    factory = qrcode.image.svg.SvgImage
    img = qrcode.make(provisioning_uri, image_factory=factory)
    img_io = io.BytesIO()
    img.save(img_io)
    svg_data = img_io.getvalue().decode('utf-8')
    
    return render_template('auth/2fa_setup.html', secret=secret, qr_svg=svg_data)


@auth_bp.route('/2fa/verify', methods=['GET', 'POST'])
def verify_2fa():
    """Verify TOTP during login."""
    pending_user_id = session.get('pending_2fa_user_id')
    if not pending_user_id:
        flash('Session expired or invalid. Please login again.', 'warning')
        return redirect(url_for('auth.login'))
        
    user = db.session.get(User, pending_user_id)
    if not user or not user.two_fa_enabled:
        return redirect(url_for('auth.login'))
        
    if request.method == 'POST':
        otp = request.form.get('otp', '').strip()
        is_backup = False
        
        totp = pyotp.TOTP(user.totp_secret)
        if totp.verify(otp):
            valid = True
        elif len(otp) == 8: # Backup code
            valid = False
            if user.backup_codes:
                codes = json.loads(user.backup_codes)
                for i, hashed in enumerate(codes):
                    if check_password_hash(hashed, otp):
                        valid = True
                        is_backup = True
                        codes.pop(i)
                        user.backup_codes = json.dumps(codes)
                        db.session.commit()
                        break
        else:
            valid = False
            
        if valid:
            next_page = session.get('pending_2fa_next', '')
            session.pop('pending_2fa_user_id', None)
            session.pop('pending_2fa_next', None)
            
            _set_authenticated_session(user)
            user.update_last_login()
            log_action('LOGIN_SUCCESS_2FA', user=user, details={'used_backup': is_backup})
            flash(f'Welcome back, {user.username}!', 'success')
            
            if _is_safe_redirect(next_page):
                return redirect(next_page)
            if user.is_admin():
                return redirect(url_for('admin.dashboard'))
            return redirect(url_for('officer.dashboard'))
        else:
            flash('Invalid code. Please try again.', 'danger')
            
    return render_template('auth/2fa_verify.html')


@auth_bp.route('/logout', methods=['POST'])
def logout():
    """Logout and clear session."""
    if 'user_id' in session:
        # Log logout
        user = db.session.get(User, session['user_id'])
        if user:
            log_action('LOGOUT', user=user)
        
        # Clear session
        _clear_pending_otp()
        session.clear()
        flash('You have been logged out.', 'info')
    
    return redirect(url_for('public.index'))


@auth_bp.route('/profile')
@login_required
def profile():
    """User profile page."""
    user = db.session.get(User, session['user_id'])
    return render_template('auth/profile.html', user=user)


@auth_bp.route('/profile/change-password', methods=['POST'])
@login_required
def change_password():
    """Allow logged-in staff to change their own password from profile."""
    user = db.session.get(User, session['user_id'])
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('auth.login'))

    current_password = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    confirm_password = request.form.get('confirm_password', '')

    if not current_password or not new_password or not confirm_password:
        flash('All password fields are required.', 'danger')
        return redirect(url_for('auth.profile'))

    if not user.check_password(current_password):
        flash('Current password is incorrect.', 'danger')
        return redirect(url_for('auth.profile'))

    if len(new_password) < 12:
        flash('New password must be at least 12 characters.', 'danger')
        return redirect(url_for('auth.profile'))

    if new_password != confirm_password:
        flash('New password and confirmation do not match.', 'danger')
        return redirect(url_for('auth.profile'))

    if new_password == current_password:
        flash('New password must be different from current password.', 'danger')
        return redirect(url_for('auth.profile'))

    from app.utils.password_policy import validate_password
    is_valid, policy_errors = validate_password(new_password, username=user.username)
    if not is_valid:
        for err in policy_errors:
            flash(err, 'danger')
        return redirect(url_for('auth.profile'))

    try:
        user.set_password(new_password)
        db.session.commit()

        log_action('PASSWORD_CHANGED', user=user)
        flash('Password updated successfully.', 'success')
    except Exception:
        db.session.rollback()
        flash('Error updating password. Please try again.', 'danger')

    return redirect(url_for('auth.profile'))
