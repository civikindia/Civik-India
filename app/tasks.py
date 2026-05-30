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


def _send_twilio_whatsapp(message, recipients):
    """
    Send WhatsApp message using Twilio's WhatsApp sandbox/business API.
    Recipients should be phone numbers as strings (e.g. '+919876543210').
    """
    if not current_app.config.get('WHATSAPP_ENABLED', False):
        return False, 'WhatsApp is disabled.'

    account_sid = (current_app.config.get('TWILIO_ACCOUNT_SID') or '').strip()
    auth_token  = (current_app.config.get('TWILIO_AUTH_TOKEN') or '').strip()
    from_number = (current_app.config.get('TWILIO_WHATSAPP_FROM') or '').strip()

    if not account_sid or not auth_token or not from_number:
        return False, 'Twilio WhatsApp credentials are incomplete.'

    base_url = f'https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json'
    auth_handler = urllib.request.HTTPBasicAuthHandler()
    auth_handler.add_password(realm=None, uri=base_url,
                               user=account_sid, passwd=auth_token)
    opener = urllib.request.build_opener(auth_handler)

    sent_count = 0
    errors = []
    for recipient in recipients:
        # Twilio WhatsApp numbers must be prefixed with 'whatsapp:'
        to_whatsapp = recipient if recipient.startswith('whatsapp:') else f'whatsapp:{recipient}'
        from_whatsapp = from_number if from_number.startswith('whatsapp:') else f'whatsapp:{from_number}'

        payload = urllib.parse.urlencode({
            'To': to_whatsapp,
            'From': from_whatsapp,
            'Body': message
        }).encode('utf-8')
        req = urllib.request.Request(base_url, data=payload, method='POST')
        try:
            with opener.open(req, timeout=15):
                sent_count += 1
        except Exception as exc:
            logger.exception('WhatsApp notification failed for %s.', recipient)
            errors.append(str(exc))

    if sent_count > 0:
        return True, None
    return False, '; '.join(errors[:3]) if errors else 'WhatsApp send failed.'


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
    # WhatsApp notification
    whatsapp_sent = False
    whatsapp_error = None
    citizen_phone = _citizen_phone(complaint)
    if citizen_phone and current_app.config.get('WHATSAPP_ENABLED'):
        whatsapp_message = (
            f'*Civik India Update*\n'
            f'Complaint `{tracking_id}` is now *{new_status}*.\n'
            f'Track at: {url_for("public.track_complaint", tracking_id=tracking_id, _external=True)}'
        )
        whatsapp_sent, whatsapp_error = _send_twilio_whatsapp(
            whatsapp_message, [citizen_phone]
        )
        _log_notification(complaint, 'whatsapp', citizen_phone, 'status_update',
                          'sent' if whatsapp_sent else 'failed', whatsapp_error)

    db.session.commit()

    if email_sent or sms_sent or whatsapp_sent:
        logger.info('[TASK] Status notification sent: complaint=%s email=%s sms=%s whatsapp=%s', tracking_id, email_sent, sms_sent, whatsapp_sent)
        return {
            'success': True,
            'tracking_id': tracking_id,
            'status': new_status,
            'mode': 'notification',
            'email_sent': email_sent,
            'sms_sent': sms_sent,
            'whatsapp_sent': whatsapp_sent,
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
        'sms_reason': sms_error,
        'whatsapp_sent': whatsapp_sent,
        'whatsapp_reason': whatsapp_error
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
    """
    Send a nightly summary email to all admin accounts.
    Covers: complaint pipeline status, department breakdown,
    top SLA breaches, and yesterday's activity.
    Runs daily via Celery beat (see __init__.py beat_schedule).
    """
    if not has_app_context():
        logger.info('[TASK] Daily report skipped (no app context).')
        return {'success': False, 'mode': 'skipped'}

    from app.models import Department, AuditLog
    from datetime import timedelta
    from sqlalchemy import func

    now = utc_now()
    yesterday = now - timedelta(hours=24)

    # ── 1. Overall pipeline stats ──────────────────────────────────────────
    stats = Complaint.get_stats()

    # ── 2. Per-department breakdown ────────────────────────────────────────
    dept_rows = []
    for dept in Department.query.order_by(Department.name).all():
        q = Complaint.query.filter_by(department_id=dept.id)
        total = q.count()
        if total == 0:
            continue
        pending  = q.filter_by(status='Pending').count()
        delayed  = q.filter_by(status='Delayed').count()
        closed   = q.filter_by(status='Closed').count()
        dept_rows.append({
            'name':    dept.name,
            'total':   total,
            'pending': pending,
            'delayed': delayed,
            'closed':  closed,
            'rate':    round(closed / total * 100, 1) if total else 0,
        })

    # ── 3. Top SLA-breached (oldest overdue active complaints) ─────────────
    top_breached = Complaint.query.filter(
        Complaint.status.in_(Complaint.ACTIVE_STATUSES),
        Complaint.sla_due_at.isnot(None),
        Complaint.sla_due_at < now,
    ).order_by(Complaint.sla_due_at.asc()).limit(5).all()

    breached_rows = []
    for c in top_breached:
        overdue_hours = round((now - c.sla_due_at).total_seconds() / 3600, 1)
        breached_rows.append({
            'tracking_id':   c.tracking_id,
            'department':    c.department.name if c.department else 'N/A',
            'service':       c.service.name if c.service else 'N/A',
            'overdue_hours': overdue_hours,
            'priority':      c.priority,
        })

    # ── 4. Yesterday's activity ────────────────────────────────────────────
    submitted_24h = Complaint.query.filter(Complaint.submitted_at >= yesterday).count()
    resolved_24h  = Complaint.query.filter(
        Complaint.resolved_at >= yesterday,
        Complaint.status == 'Closed'
    ).count()

    # ── 5. Send email ──────────────────────────────────────────────────────
    recipients = [
        u.email for u in User.query.filter_by(role='admin', is_active=True).all()
        if u.email
    ]
    fallback = (current_app.config.get('NOTIFICATION_TO_EMAIL') or '').strip()
    if fallback and fallback not in recipients:
        recipients.append(fallback)

    if not recipients:
        logger.warning('[TASK] Daily report: no admin email recipients configured.')
        return {'success': False, 'reason': 'no_recipients'}

    report_date = yesterday.strftime('%d %b %Y')
    subject = f'Civik India — Daily Report: {report_date}'

    context = dict(
        stats=stats,
        dept_rows=dept_rows,
        breached_rows=breached_rows,
        submitted_24h=submitted_24h,
        resolved_24h=resolved_24h,
        report_date=report_date,
        generated_at=now.strftime('%d %b %Y, %I:%M %p UTC'),
    )

    try:
        plain_body = render_template('email/daily_report.txt', **context)
        html_body  = render_template('email/daily_report.html', **context)
    except Exception as exc:
        logger.exception('[TASK] Daily report template render failed.')
        return {'success': False, 'error': str(exc)}

    sent, error = send_system_email(subject, plain_body, recipients, html_body=html_body)

    # ── 6. Audit log ───────────────────────────────────────────────────────
    import json as _json
    AuditLog.create_entry(
        username='system',
        role='system',
        action='DAILY_REPORT_SENT',
        details=_json.dumps({
            'report_date':   report_date,
            'recipients':    len(recipients),
            'total':         stats['total'],
            'delayed':       stats['delayed'],
            'sla_breached':  len(breached_rows),
            'sent':          sent,
            'error':         error,
        })
    )

    if sent:
        logger.info('[TASK] Daily report sent: date=%s recipients=%d', report_date, len(recipients))
    else:
        logger.warning('[TASK] Daily report email failed: %s', error)

    return {
        'success': sent,
        'report_date': report_date,
        'recipients': len(recipients),
        'total': stats['total'],
        'delayed': stats['delayed'],
        'sla_breached': len(breached_rows),
        'submitted_24h': submitted_24h,
        'resolved_24h': resolved_24h,
        'error': error,
    }


@celery.task(name='app.tasks.cleanup_old_uploads')
def cleanup_old_uploads(days=30):
    """
    Hard-delete soft-deleted EvidenceFile records older than `days` days.
    Removes both the physical file (local disk or R2/S3) and the DB row.
    Only processes rows where deleted_at IS NOT NULL and older than cutoff.

    Args:
        days (int): retention window in days after soft-deletion (default 30).
    """
    if not has_app_context():
        logger.info('[TASK] Upload cleanup skipped (no app context).')
        return {'success': False, 'mode': 'skipped'}

    from app.models import EvidenceFile, AuditLog
    from datetime import timedelta
    import os, json as _json

    now = utc_now()
    cutoff = now - timedelta(days=days)

    candidates = EvidenceFile.query.filter(
        EvidenceFile.deleted_at.isnot(None),
        EvidenceFile.deleted_at < cutoff,
    ).all()

    if not candidates:
        logger.info('[TASK] Upload cleanup: no files to purge (cutoff=%s).', cutoff.date())
        return {'success': True, 'purged': 0, 'errors': 0, 'cutoff': str(cutoff.date())}

    purged = 0
    errors = 0
    error_ids = []

    for ef in candidates:
        try:
            provider = (ef.storage_provider or 'local').lower()

            # ── Physical deletion ──────────────────────────────────────────
            if provider == 'r2':
                _delete_r2_object(ef)
            elif provider == 'local':
                _delete_local_file(ef)
            # Other providers: log and skip physical deletion but still purge row

            # ── DB row deletion ────────────────────────────────────────────
            db.session.delete(ef)
            db.session.commit()
            purged += 1
            logger.debug('[TASK] Purged EvidenceFile id=%d provider=%s', ef.id, provider)

        except Exception as exc:
            db.session.rollback()
            errors += 1
            error_ids.append(ef.id)
            logger.exception('[TASK] Failed to purge EvidenceFile id=%d: %s', ef.id, exc)

    # ── Audit log ──────────────────────────────────────────────────────────
    AuditLog.create_entry(
        username='system',
        role='system',
        action='EVIDENCE_CLEANUP_RUN',
        details=_json.dumps({
            'days_retention': days,
            'cutoff_date':    str(cutoff.date()),
            'candidates':     len(candidates),
            'purged':         purged,
            'errors':         errors,
            'error_ids':      error_ids[:20],   # cap to avoid huge log entries
        })
    )

    logger.info(
        '[TASK] Upload cleanup complete: candidates=%d purged=%d errors=%d cutoff=%s',
        len(candidates), purged, errors, cutoff.date()
    )
    return {
        'success': True,
        'candidates': len(candidates),
        'purged': purged,
        'errors': errors,
        'error_ids': error_ids,
        'cutoff': str(cutoff.date()),
    }


def _delete_r2_object(ef: 'EvidenceFile'):
    """Delete an object from R2/S3 storage."""
    import boto3, botocore.exceptions
    bucket = ef.storage_bucket or current_app.config.get('R2_BUCKET_NAME')
    key    = ef.storage_key
    if not bucket or not key:
        logger.warning('[TASK] R2 delete skipped: missing bucket/key for EvidenceFile id=%d', ef.id)
        return

    s3 = boto3.client(
        's3',
        endpoint_url          = current_app.config.get('R2_ENDPOINT_URL'),
        aws_access_key_id     = current_app.config.get('R2_ACCESS_KEY_ID'),
        aws_secret_access_key = current_app.config.get('R2_SECRET_ACCESS_KEY'),
        region_name           = 'auto',
    )
    s3.delete_object(Bucket=bucket, Key=key)
    logger.debug('[TASK] R2 object deleted: bucket=%s key=%s', bucket, key)


def _delete_local_file(ef: 'EvidenceFile'):
    """Delete a file from local disk storage."""
    import os
    path = ef.evidence_path if hasattr(ef, 'evidence_path') else None
    # Fallback: reconstruct from storage_key if available
    if not path and ef.storage_key:
        upload_folder = current_app.config.get('UPLOAD_FOLDER', 'uploads')
        path = os.path.join(upload_folder, ef.storage_key)
    if path and os.path.exists(path):
        os.remove(path)
        logger.debug('[TASK] Local file deleted: %s', path)
    elif path:
        logger.debug('[TASK] Local file not found (already gone): %s', path)


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
