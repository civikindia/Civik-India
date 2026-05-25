"""
CivikIndia Task Utilities
Synchronous task helpers used when Celery/Redis are not configured.
"""
import hashlib
import logging
import urllib.parse
import urllib.request

from flask import current_app, has_app_context, render_template, url_for
from flask_mail import Message

from app import db, celery, mail
from app.models import Complaint, EscalationContact, User, NotificationLog
from app.clock import utc_now

logger = logging.getLogger(__name__)


def _collect_status_update_recipients(complaint):
    """Collect recipient emails for staff notifications."""
    recipients = set()

    if complaint and complaint.assigned_officer and complaint.assigned_officer.email:
        recipients.add(complaint.assigned_officer.email.strip())

    admins = User.query.filter_by(role='admin', is_active=True).all()
    for admin in admins:
        if admin.email:
            recipients.add(admin.email.strip())

    fallback = (current_app.config.get('NOTIFICATION_TO_EMAIL') or '').strip()
    if fallback:
        recipients.add(fallback)

    return sorted(email for email in recipients if email)


def _collect_submission_recipients():
    """Collect recipient emails for new complaint alerts."""
    recipients = set()

    admins = User.query.filter_by(role='admin', is_active=True).all()
    for admin in admins:
        if admin.email:
            recipients.add(admin.email.strip())

    officers = User.query.filter(
        User.role.in_(['officer', 'zonal_officer', 'commissioner']),
        User.is_active.is_(True)
    ).all()
    for officer in officers:
        if officer.email:
            recipients.add(officer.email.strip())

    fallback = (current_app.config.get('NOTIFICATION_TO_EMAIL') or '').strip()
    if fallback:
        recipients.add(fallback)

    return sorted(email for email in recipients if email)


def _collect_sms_recipients():
    """Collect SMS recipients from config."""
    raw = (current_app.config.get('SMS_NOTIFICATION_TO') or '').strip()
    if not raw:
        return []
    recipients = []
    for part in raw.split(','):
        number = part.strip()
        if number:
            recipients.append(number)
    return recipients


def _hash_recipient(recipient):
    """Hash recipient addresses/numbers before storing delivery logs."""
    normalized = (recipient or '').strip().lower()
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest() if normalized else 'unknown'


def _log_notification(complaint, channel, recipient, template_name, status, error_message=None):
    """Persist a delivery attempt without storing raw recipient PII."""
    db.session.add(NotificationLog(
        complaint_id=complaint.id if complaint else None,
        channel=channel,
        recipient_hash=_hash_recipient(recipient),
        template_name=template_name,
        status=status,
        sent_at=utc_now() if status == 'sent' else None,
        error_message=(error_message[:1000] if error_message else None)
    ))


def _render_email(template_name, **context):
    """Render paired plain-text and HTML notification templates."""
    return (
        render_template(f'email/{template_name}.txt', **context),
        render_template(f'email/{template_name}.html', **context),
    )


def _looks_like_email(value):
    value = (value or '').strip()
    return '@' in value and '.' in value.rsplit('@', 1)[-1]


def _looks_like_phone(value):
    digits = ''.join(ch for ch in (value or '') if ch.isdigit())
    return len(digits) >= 10


def _citizen_email(complaint):
    if not complaint or complaint.contact_preference != 'email':
        return None
    value = (complaint.voluntary_id or '').strip()
    return value if _looks_like_email(value) else None


def _citizen_phone(complaint):
    if not complaint or complaint.contact_preference not in ('phone', 'sms', 'whatsapp'):
        return None
    value = (complaint.voluntary_id or '').strip()
    return value if _looks_like_phone(value) else None


def _send_email_notification(subject, template_name, recipients, complaint=None, **context):
    recipients = sorted({r.strip() for r in recipients if r and r.strip()})
    body, html = _render_email(template_name, complaint=complaint, **context)
    sent, error = send_system_email(subject, body, recipients, html_body=html)
    status = 'sent' if sent else 'failed'
    if recipients:
        for recipient in recipients:
            _log_notification(complaint, 'email', recipient, template_name, status, error)
    else:
        _log_notification(complaint, 'email', 'none', template_name, 'failed', 'No recipients available.')
    return sent, error


def _send_sms_notification(message, template_name, recipients, complaint=None, template_id=None):
    recipients = sorted({r.strip() for r in recipients if r and r.strip()})
    sent, error = send_system_sms(message, recipients, template_id=template_id)
    status = 'sent' if sent else 'failed'
    if recipients:
        for recipient in recipients:
            _log_notification(complaint, 'sms', recipient, template_name, status, error)
    else:
        _log_notification(complaint, 'sms', 'none', template_name, 'failed', 'No recipients available.')
    return sent, error


def send_system_email(subject, body, recipients, html_body=None):
    """Send SMTP email if mail settings are configured."""
    mail_server = (current_app.config.get('MAIL_SERVER') or '').strip()

    if not mail_server:
        return False, 'MAIL_SERVER not configured.'
    if not recipients:
        return False, 'No recipients available.'

    sender = current_app.config.get('MAIL_DEFAULT_SENDER') or 'no-reply@civikindia.online'
    message = Message(
        subject=subject,
        sender=sender,
        recipients=recipients,
        body=body,
        html=html_body
    )

    try:
        if current_app.config.get('MAIL_SUPPRESS_SEND', False):
            return True, None
        mail.send(message)
        return True, None
    except Exception as exc:
        logger.exception('Email notification failed.')
        return False, str(exc)


def send_system_sms(message, recipients, template_id=None):
    """Send SMS messages through the configured provider."""
    if not current_app.config.get('SMS_ENABLED', False):
        return False, 'SMS is disabled.'
    if not recipients:
        return False, 'No SMS recipients available.'

    provider = current_app.config.get('SMS_PROVIDER', 'msg91').strip().lower()
    if provider == 'msg91':
        return _send_msg91_sms(message, recipients, template_id=template_id)
    if provider == 'fast2sms':
        return _send_fast2sms(message, recipients)
    if provider == 'twilio':
        return _send_twilio_sms(message, recipients)
    return False, f'Unsupported SMS provider configured: {provider}.'


def _send_msg91_sms(message, recipients, template_id=None):
    auth_key = (current_app.config.get('MSG91_AUTH_KEY') or '').strip()
    sender_id = (current_app.config.get('MSG91_SENDER_ID') or '').strip()
    if not auth_key or not sender_id:
        return False, 'MSG91 credentials are incomplete.'

    params = {
        'authkey': auth_key,
        'mobiles': ','.join(recipients),
        'message': message,
        'sender': sender_id,
        'route': current_app.config.get('MSG91_ROUTE', '4'),
        'country': current_app.config.get('MSG91_COUNTRY', '91'),
    }
    if template_id:
        params['DLT_TE_ID'] = template_id

    url = 'https://api.msg91.com/api/sendhttp.php?' + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=15):
            return True, None
    except Exception as exc:
        logger.exception('MSG91 SMS notification failed.')
        return False, str(exc)


def _send_fast2sms(message, recipients):
    api_key = (current_app.config.get('FAST2SMS_API_KEY') or '').strip()
    if not api_key:
        return False, 'Fast2SMS API key is missing.'

    payload = urllib.parse.urlencode({
        'route': 'q',
        'message': message,
        'language': 'english',
        'flash': '0',
        'numbers': ','.join(recipients),
        'sender_id': (current_app.config.get('FAST2SMS_SENDER_ID') or '').strip(),
    }).encode('utf-8')
    request = urllib.request.Request(
        'https://www.fast2sms.com/dev/bulkV2',
        data=payload,
        method='POST',
        headers={
            'authorization': api_key,
            'Content-Type': 'application/x-www-form-urlencoded'
        }
    )
    try:
        with urllib.request.urlopen(request, timeout=15):
            return True, None
    except Exception as exc:
        logger.exception('Fast2SMS notification failed.')
        return False, str(exc)


def _send_twilio_sms(message, recipients):
    account_sid = (current_app.config.get('TWILIO_ACCOUNT_SID') or '').strip()
    auth_token = (current_app.config.get('TWILIO_AUTH_TOKEN') or '').strip()
    from_number = (current_app.config.get('TWILIO_FROM_NUMBER') or '').strip()
    if not account_sid or not auth_token or not from_number:
        return False, 'Twilio credentials are incomplete.'

    base_url = f'https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json'
    auth_handler = urllib.request.HTTPBasicAuthHandler()
    auth_handler.add_password(
        realm=None,
        uri=base_url,
        user=account_sid,
        passwd=auth_token
    )
    opener = urllib.request.build_opener(auth_handler)

    sent_count = 0
    errors = []
    for to_number in recipients:
        payload = urllib.parse.urlencode({
            'To': to_number,
            'From': from_number,
            'Body': message
        }).encode('utf-8')
        request = urllib.request.Request(base_url, data=payload, method='POST')
        try:
            with opener.open(request, timeout=15):
                sent_count += 1
        except Exception as exc:
            logger.exception('SMS notification failed for %s.', to_number)
            errors.append(str(exc))

    if sent_count > 0:
        return True, None
    return False, '; '.join(errors[:3]) if errors else 'SMS send failed.'


@celery.task(name='app.tasks.send_status_update_notification')
def send_status_update_notification(tracking_id, new_status, contact_method=None):
    """
    Send status-update notifications to staff emails when configured.
    Falls back to structured logging if email settings are unavailable.
    """
    if not has_app_context():
        logger.info(
            '[TASK] Notification skipped (no app context): complaint=%s status=%s',
            tracking_id, new_status
        )
        return {
            'success': False,
            'tracking_id': tracking_id,
            'status': new_status,
            'mode': 'skipped'
        }

    complaint = Complaint.query.filter_by(tracking_id=tracking_id).first()

    email_recipients = set(_collect_status_update_recipients(complaint))
    citizen_email = _citizen_email(complaint)
    if citizen_email:
        email_recipients.add(citizen_email)

    subject = f'Civik India — Complaint Status Update: {tracking_id} is now {new_status}'
    email_sent, email_error = _send_email_notification(
        subject,
        'status_update',
        email_recipients,
        complaint=complaint,
        new_status=new_status,
        track_url=url_for('public.track_complaint', tracking_id=tracking_id, _external=True)
    )

    sms_recipients = set(_collect_sms_recipients())
    citizen_phone = _citizen_phone(complaint)
    if citizen_phone:
        sms_recipients.add(citizen_phone)

    sms_message = (
        f'Civik India: Complaint {tracking_id} status updated to {new_status}. '
        f'Track at /track using your ID.'
    )
    sms_sent, sms_error = _send_sms_notification(
        sms_message,
        'status_update',
        sms_recipients,
        complaint=complaint,
        template_id=current_app.config.get('SMS_TEMPLATE_STATUS_UPDATE')
    )
    db.session.commit()

    if email_sent or sms_sent:
        logger.info('[TASK] Status notification sent: complaint=%s email=%s sms=%s', tracking_id, email_sent, sms_sent)
        return {
            'success': True,
            'tracking_id': tracking_id,
            'status': new_status,
            'mode': 'notification',
            'email_sent': email_sent,
            'sms_sent': sms_sent,
            'recipient_count': len(email_recipients),
            'sms_recipient_count': len(sms_recipients)
        }

    logger.info('[TASK] Notification fallback: complaint=%s status=%s reason=%s', tracking_id, new_status, email_error)
    return {
        'success': True,
        'tracking_id': tracking_id,
        'status': new_status,
        'mode': 'log',
        'reason': email_error,
        'sms_sent': sms_sent,
        'sms_reason': sms_error
    }


@celery.task(name='app.tasks.send_complaint_submission_notification')
def send_complaint_submission_notification(tracking_id):
    """
    Send new complaint submission notifications to internal staff channels.
    Uses email by default and optional SMS when configured.
    """
    if not has_app_context():
        logger.info('[TASK] Submission notification skipped (no app context): complaint=%s', tracking_id)
        return {'success': False, 'tracking_id': tracking_id, 'mode': 'skipped'}

    complaint = Complaint.query.filter_by(tracking_id=tracking_id).first()
    if not complaint:
        return {'success': False, 'tracking_id': tracking_id, 'mode': 'missing_complaint'}

    track_url = url_for('public.track_complaint', tracking_id=tracking_id, _external=True)
    staff_recipients = _collect_submission_recipients()
    staff_subject = f'Civik India New Complaint Submitted: {tracking_id}'
    staff_email_sent, staff_email_error = _send_email_notification(
        staff_subject,
        'assignment_notification',
        staff_recipients,
        complaint=complaint,
        track_url=track_url
    )

    citizen_email_sent = False
    citizen_email_error = None
    citizen_email = _citizen_email(complaint)
    if citizen_email:
        citizen_email_sent, citizen_email_error = _send_email_notification(
            f'Civik India — Complaint Acknowledgment: {tracking_id}',
            'complaint_acknowledgment',
            [citizen_email],
            complaint=complaint,
            track_url=track_url
        )

    sms_recipients = set(_collect_sms_recipients())
    citizen_phone = _citizen_phone(complaint)
    if citizen_phone:
        sms_recipients.add(citizen_phone)
    sms_message = (
        f'Civik India: Complaint received. Tracking ID {tracking_id}. '
        f'Use this ID to track status on the portal.'
    )
    sms_sent, sms_error = _send_sms_notification(
        sms_message,
        'complaint_acknowledgment',
        sms_recipients,
        complaint=complaint,
        template_id=current_app.config.get('SMS_TEMPLATE_COMPLAINT_ACK')
    )
    db.session.commit()

    email_sent = staff_email_sent or citizen_email_sent
    email_error = staff_email_error or citizen_email_error

    if email_sent or sms_sent:
        return {
            'success': True,
            'tracking_id': tracking_id,
            'email_sent': email_sent,
            'staff_email_sent': staff_email_sent,
            'citizen_email_sent': citizen_email_sent,
            'sms_sent': sms_sent,
            'recipient_count': len(staff_recipients) + (1 if citizen_email else 0),
            'sms_recipient_count': len(sms_recipients)
        }

    logger.info(
        '[TASK] Submission notification fallback: complaint=%s email_reason=%s sms_reason=%s',
        tracking_id,
        email_error,
        sms_error
    )
    return {
        'success': True,
        'tracking_id': tracking_id,
        'mode': 'log',
        'email_reason': email_error,
        'sms_reason': sms_error
    }


@celery.task(name='app.tasks.generate_daily_report')
def generate_daily_report():
    """Placeholder daily report hook for scheduler integration."""
    logger.info('[TASK] Daily report generation is not scheduled in this runtime.')
    return {}


@celery.task(name='app.tasks.cleanup_old_uploads')
def cleanup_old_uploads(days=30):
    """Placeholder upload cleanup hook for scheduler integration."""
    logger.info('[TASK] Upload cleanup is not scheduled in this runtime.')
    return {}


@celery.task(name='app.tasks.check_sla_breaches')
def check_sla_breaches():
    """Run SLA checks and trigger escalations."""
    if not has_app_context():
        logger.info('[TASK] SLA check skipped (no app context)')
        return {}
        
    try:
        overdue_before = {
            complaint.id: complaint.escalation_level or 0
            for complaint in Complaint.query.filter(
                Complaint.status.in_(Complaint.ACTIVE_STATUSES),
                Complaint.sla_due_at.isnot(None),
                Complaint.sla_due_at < utc_now()
            ).all()
        }
        escalated_count = Complaint.apply_sla_escalations()
        notices_sent = 0
        if escalated_count:
            for complaint in Complaint.query.filter(Complaint.id.in_(overdue_before.keys())).all():
                if (complaint.escalation_level or 0) > overdue_before.get(complaint.id, 0):
                    result = send_escalation_notice(complaint.tracking_id)
                    if result.get('success'):
                        notices_sent += 1

        return {'success': True, 'escalated_count': escalated_count}
    except Exception as exc:
        logger.exception('SLA breach check failed.')
        return {'success': False, 'error': str(exc)}


@celery.task(name='app.tasks.send_escalation_notice')
def send_escalation_notice(tracking_id):
    """Notify escalation contacts when a complaint breaches SLA."""
    if not has_app_context():
        return {'success': False, 'tracking_id': tracking_id, 'mode': 'skipped'}

    complaint = Complaint.query.filter_by(tracking_id=tracking_id).first()
    if not complaint:
        return {'success': False, 'tracking_id': tracking_id, 'mode': 'missing_complaint'}

    contacts = EscalationContact.query.filter_by(
        department_id=complaint.department_id,
        level=min(complaint.escalation_level or 1, 4),
        is_active=True
    ).all()
    email_recipients = [contact.email for contact in contacts if contact.email]
    sms_recipients = [contact.phone for contact in contacts if contact.phone]

    subject = f'Civik India — Escalation Notice: {tracking_id}'
    email_sent, email_error = _send_email_notification(
        subject,
        'escalation_notice',
        email_recipients,
        complaint=complaint,
        escalation_level=complaint.escalation_level,
        track_url=url_for('admin.complaint_detail', tracking_id=tracking_id, _external=True)
    )
    sms_sent, sms_error = _send_sms_notification(
        f'Civik India SLA breach L{complaint.escalation_level}: {tracking_id}. Login to review.',
        'escalation_notice',
        sms_recipients,
        complaint=complaint,
        template_id=current_app.config.get('SMS_TEMPLATE_ESCALATION')
    )
    db.session.commit()
    return {
        'success': email_sent or sms_sent,
        'tracking_id': tracking_id,
        'email_sent': email_sent,
        'sms_sent': sms_sent,
        'reason': email_error or sms_error
    }


@celery.task(name='app.tasks.send_officer_welcome_notification')
def send_officer_welcome_notification(user_id, temporary_password=None):
    """Send a welcome email to a newly created officer account."""
    if not has_app_context():
        return {'success': False, 'user_id': user_id, 'mode': 'skipped'}

    user = db.session.get(User, user_id)
    if not user or not user.email:
        return {'success': False, 'user_id': user_id, 'mode': 'missing_email'}

    sent, error = _send_email_notification(
        'Civik India — Officer Account Created',
        'officer_welcome',
        [user.email],
        complaint=None,
        user=user,
        temporary_password=temporary_password,
        login_url=url_for('auth.login', _external=True)
    )
    db.session.commit()
    return {'success': sent, 'user_id': user_id, 'reason': error}


@celery.task(name='app.tasks.backup_database')
def backup_database():
    """Placeholder database backup hook for scheduler integration."""
    logger.info('[TASK] Database backup is not scheduled in this runtime.')
    return {}
