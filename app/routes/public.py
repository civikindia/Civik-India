"""
CivikIndia Public Routes
Citizen-facing routes - no authentication required.
"""
import csv
import io
import inspect
import re
import time
import threading
from collections import deque
from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, current_app, Response
from sqlalchemy import text, func
from sqlalchemy.orm import joinedload
from datetime import datetime, timedelta

from app import db, csrf
from app.clock import utc_now
from app.models import Department, Service, Complaint, AuditLog, EvidenceFile
from app.utils import (
    generate_tracking_id, save_uploaded_file,
    validate_tracking_id, normalize_tracking_id, log_action,
    analyze_complaint_text, maybe_run_sla_escalations
)
from app.tasks import send_complaint_submission_notification

public_bp = Blueprint('public', __name__)
_ai_rate_lock = threading.Lock()
_ai_rate_buckets = {}
_geo_rate_lock = threading.Lock()
_geo_rate_buckets = {}
_submit_rate_lock = threading.Lock()
_submit_rate_buckets = {}
_public_api_cache_lock = threading.Lock()
_public_api_cache = {}
MIN_COMPLAINT_DESCRIPTION_CHARACTERS = 25


def _word_count(value):
    """Count non-empty words in citizen-provided text."""
    return len((value or '').strip().split())

DASHBOARD_STATUSES = ['Pending', 'Under Review', 'Action Taken', 'Delayed', 'Reopened', 'Closed']
STATUS_BADGE_CLASSES = {
    'Pending': 'badge-pending',
    'Under Review': 'badge-review',
    'Action Taken': 'badge-action',
    'Delayed': 'badge-delayed',
    'Reopened': 'badge-reopened',
    'Closed': 'badge-closed',
}


def _get_client_ip():
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr or 'unknown'


def _enforce_ai_rate_limit():
    """
    Apply in-memory per-IP limits:
    - minimum seconds between requests
    - maximum requests in a rolling window
    """
    if current_app.config.get('TESTING') or current_app.testing:
        return True, None

    min_interval = int(current_app.config.get('AI_RATE_MIN_INTERVAL_SECONDS', 3))
    window_seconds = int(current_app.config.get('AI_RATE_WINDOW_SECONDS', 60))
    max_requests = int(current_app.config.get('AI_RATE_MAX_REQUESTS_PER_WINDOW', 20))

    client_ip = _get_client_ip()
    now_ts = time.time()

    with _ai_rate_lock:
        bucket = _ai_rate_buckets.get(client_ip)
        if bucket is None:
            bucket = {'last_ts': 0.0, 'hits': deque()}
            _ai_rate_buckets[client_ip] = bucket

        if now_ts - bucket['last_ts'] < min_interval:
            return False, 'Please wait a few seconds before asking again.'

        hits = bucket['hits']
        while hits and now_ts - hits[0] > window_seconds:
            hits.popleft()

        if len(hits) >= max_requests:
            return False, 'Too many AI requests from this network. Please try again later.'

        hits.append(now_ts)
        bucket['last_ts'] = now_ts

        # Prevent indefinite growth if many unique IPs touch the endpoint.
        if len(_ai_rate_buckets) > 5000:
            stale_cutoff = now_ts - (window_seconds * 2)
            stale_keys = [
                ip for ip, data in _ai_rate_buckets.items()
                if not data['hits'] or data['hits'][-1] < stale_cutoff
            ]
            for ip in stale_keys[:1000]:
                _ai_rate_buckets.pop(ip, None)

    return True, None


def _enforce_geo_rate_limit():
    """
    Apply in-memory per-IP limits for geo queries:
    - min_interval: 0.5 second
    - max_requests: 30 requests per minute
    """
    if current_app.config.get('TESTING') or current_app.testing:
        return True, None

    min_interval = 0.5
    window_seconds = 60
    max_requests = 30

    client_ip = _get_client_ip()
    now_ts = time.time()

    with _geo_rate_lock:
        bucket = _geo_rate_buckets.get(client_ip)
        if bucket is None:
            bucket = {'last_ts': 0.0, 'hits': deque()}
            _geo_rate_buckets[client_ip] = bucket

        if now_ts - bucket['last_ts'] < min_interval:
            return False, 'Please wait a moment before sending another query.'

        hits = bucket['hits']
        while hits and now_ts - hits[0] > window_seconds:
            hits.popleft()

        if len(hits) >= max_requests:
            return False, 'Too many map queries. Please try again in a minute.'

        hits.append(now_ts)
        bucket['last_ts'] = now_ts

        if len(_geo_rate_buckets) > 5000:
            stale_cutoff = now_ts - (window_seconds * 2)
            stale_keys = [
                ip for ip, data in _geo_rate_buckets.items()
                if not data['hits'] or data['hits'][-1] < stale_cutoff
            ]
            for ip in stale_keys[:1000]:
                _geo_rate_buckets.pop(ip, None)

    return True, None


def _enforce_submit_rate_limit():
    """Apply a small in-memory per-IP guard around public complaint submissions."""
    if (
        current_app.config.get('TESTING')
        or current_app.testing
        or not current_app.config.get('SUBMIT_RATE_LIMIT_ENABLED', True)
    ):
        return True, None

    window_seconds = int(current_app.config.get('SUBMIT_RATE_WINDOW_SECONDS', 300))
    max_requests = int(current_app.config.get('SUBMIT_RATE_MAX_ATTEMPTS_PER_IP', 20))
    client_ip = _get_client_ip()
    now_ts = time.time()

    with _submit_rate_lock:
        hits = _submit_rate_buckets.setdefault(client_ip, deque())
        while hits and now_ts - hits[0] > window_seconds:
            hits.popleft()
        if len(hits) >= max_requests:
            return False, 'Too many submissions from this network. Please try again later.'
        hits.append(now_ts)

        if len(_submit_rate_buckets) > 5000:
            stale_cutoff = now_ts - (window_seconds * 2)
            stale_keys = [
                ip for ip, data in _submit_rate_buckets.items()
                if not data or data[-1] < stale_cutoff
            ]
            for ip in stale_keys[:1000]:
                _submit_rate_buckets.pop(ip, None)

    return True, None


def _fallback_homepage_reply(message):
    """Provide deterministic portal guidance when external AI is unavailable."""
    text = (message or '').lower()

    if any(word in text for word in ['submit', 'report', 'complaint']):
        return (
            "To submit a strong anonymous complaint, include what happened, where it happened, "
            "when it happened, and what impact it caused. Keep facts specific and avoid personal identifiers.\n"
            "Next best action: Open /submit and complete the complaint form with clear details."
        )

    if any(word in text for word in ['track', 'status', 'id']):
        return (
            "You can track progress using your complaint tracking ID (starts with MIB). "
            "Enter it on the tracking page to see current status and timeline updates.\n"
            "Next best action: Open /track and search with your tracking ID."
        )

    if any(word in text for word in ['evidence', 'proof', 'photo', 'file', 'document']):
        return (
            "Useful evidence includes photos, documents, receipts, and timestamps connected to the incident. "
            "Upload only relevant files and avoid personal identifiers in attachments.\n"
            "Next best action: Prepare supporting files, then submit through /submit."
        )

    if any(word in text for word in ['dashboard', 'stats', 'heatmap', 'transparency']):
        return (
            "The public dashboard shows aggregate complaint performance, while the geo heatmap shows location trends. "
            "These tools help you understand resolution patterns.\n"
            "Next best action: Visit /dashboard for analytics and /geo-heatmap for map insights."
        )

    return (
        "I can help with complaint submission, status tracking, evidence guidance, and transparency pages. "
        "Ask a specific question to get step-by-step guidance.\n"
        "Next best action: Tell me whether you want help with /submit, /track, /dashboard, or /geo-heatmap."
    )


def _fallback_draft_reply(message, description, department_name, service_name):
    """Provide structured drafting help without external AI."""
    draft = (description or '').strip()
    brief_draft = draft[:300] if draft else (
        "I want to report misconduct related to municipal service delivery in my area."
    )

    return (
        "1) Quick guidance\n"
        "Keep your complaint factual and specific. Mention incident date/time, location, requested action, and impact.\n\n"
        "2) Improved complaint draft (template)\n"
        f"I am submitting an anonymous complaint regarding {service_name or 'a municipal service'}"
        f" under {department_name or 'the relevant department'}. "
        "The incident occurred at [location] on [date/time]. The issue involved [clear factual description]. "
        "This caused [impact on citizens/service delivery]. Any available evidence includes [documents/photos/reference details]. "
        "I request a formal review and corrective action, and I request updates against the complaint tracking ID.\n\n"
        f"Context from your draft: {brief_draft}\n\n"
        "3) Missing details checklist\n"
        "- Exact location and approximate time\n"
        "- Specific action/behavior observed\n"
        "- Service impact and frequency\n"
        "- Evidence references (if available)"
    )


def _fallback_ai_reply(assistant_mode, message, description, department_name, service_name):
    """Return local fallback text for chatbot responses."""
    if assistant_mode == 'homepage':
        return _fallback_homepage_reply(message)
    return _fallback_draft_reply(message, description, department_name, service_name)


def _month_start(value):
    """Normalize datetime to month start."""
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _shift_month(value, months):
    """Shift datetime by N months, preserving month-start format."""
    month_index = (value.month - 1) + months
    year = value.year + (month_index // 12)
    month = (month_index % 12) + 1
    return value.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


def _parse_month_value(raw):
    """Parse YYYY-MM into datetime at month start."""
    if not raw:
        return None
    try:
        parsed = datetime.strptime(raw, '%Y-%m')
        return _month_start(parsed)
    except ValueError:
        return None


def _parse_optional_coordinate(raw, field_name):
    """Parse optional coordinate input and return float value."""
    if raw is None:
        return None

    text_value = str(raw).strip()
    if not text_value:
        return None

    normalized = text_value.replace(',', '.')
    try:
        return float(normalized)
    except (TypeError, ValueError):
        raise ValueError(f'{field_name} must be a valid number.')


def _parse_geo_filter_value(raw, field_name=None):
    """Normalize and validate optional geo filter inputs."""
    text_value = (raw or '').strip()
    if not text_value or text_value.lower() == 'all':
        return None
    if field_name:
        if len(text_value) > 100:
            raise ValueError(f'{field_name} must be under 100 characters.')
        if not re.match(r"^[\w\s\.\-']+$", text_value, re.UNICODE):
            raise ValueError(f'Invalid characters in {field_name}.')
    return text_value


def _parse_geo_filters():
    """Parse and normalize query filters for geolocation endpoints."""
    status = _parse_geo_filter_value(request.args.get('status'))
    if status and status != 'all' and status not in DASHBOARD_STATUSES:
        raise ValueError('Invalid status filter.')

    priority = _parse_geo_filter_value(request.args.get('priority'))
    if priority and priority not in ('Low', 'Normal', 'High', 'Urgent'):
        raise ValueError('Invalid priority filter.')

    state = _parse_geo_filter_value(request.args.get('state'), 'State')
    district = _parse_geo_filter_value(request.args.get('district'), 'District')
    city = _parse_geo_filter_value(request.args.get('city'), 'City')

    department_id = request.args.get('department_id', type=int)

    limit = request.args.get('limit', type=int)
    max_points = int(current_app.config.get('GEO_HEATMAP_MAX_POINTS', 2500))
    if limit is None or limit <= 0:
        limit = max_points
    limit = min(limit, max_points * 2)
    return {
        'status': status or None,
        'priority': priority or None,
        'state': state or None,
        'district': district or None,
        'city': city or None,
        'department_id': department_id or None,
        'limit': limit
    }


def _no_cache_json(payload, status=200):
    """Return JSON response with browser/proxy no-cache headers."""
    response = jsonify(payload)
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def _public_cache_key(namespace):
    """Build a stable short-lived cache key for public aggregate endpoints."""
    args = tuple(sorted(
        (key, value)
        for key, value in request.args.items()
        if key != '_'
    ))
    return namespace, args


def _cached_public_payload(namespace, producer):
    """
    Return short-lived data for public dashboard/map endpoints.
    Disabled in tests so mutation-heavy test cases remain deterministic.
    """
    ttl = int(current_app.config.get('PUBLIC_API_CACHE_SECONDS', 0) or 0)
    if ttl <= 0 or current_app.config.get('TESTING') or current_app.testing:
        return producer(), False, ttl

    key = _public_cache_key(namespace)
    now_ts = time.time()

    with _public_api_cache_lock:
        cached = _public_api_cache.get(key)
        if cached and now_ts - cached['stored_at'] < ttl:
            return cached['payload'], True, ttl

    payload = producer()
    with _public_api_cache_lock:
        _public_api_cache[key] = {'stored_at': now_ts, 'payload': payload}
        if len(_public_api_cache) > 256:
            stale_cutoff = now_ts - (ttl * 4)
            stale_keys = [
                cache_key for cache_key, value in _public_api_cache.items()
                if value['stored_at'] < stale_cutoff
            ]
            for cache_key in stale_keys[:128]:
                _public_api_cache.pop(cache_key, None)

    return payload, False, ttl


def _cached_public_json(namespace, producer, status=200):
    """
    Return short-lived JSON for public dashboard/map endpoints.
    Disabled in tests so mutation-heavy test cases remain deterministic.
    """
    if status != 200:
        return _no_cache_json(producer(), status)

    payload, cache_hit, ttl = _cached_public_payload(namespace, producer)
    if ttl <= 0 or current_app.config.get('TESTING') or current_app.testing:
        return _no_cache_json(payload, status)
    response = jsonify(payload)
    response.headers['Cache-Control'] = f'public, max-age={ttl}'
    response.headers['X-CivikIndia-Cache'] = 'hit' if cache_hit else 'miss'
    return response


def _public_cache_hit_response(namespace):
    """Return a cached public JSON response without running expensive guards."""
    ttl = int(current_app.config.get('PUBLIC_API_CACHE_SECONDS', 0) or 0)
    if ttl <= 0 or current_app.config.get('TESTING') or current_app.testing:
        return None

    key = _public_cache_key(namespace)
    now_ts = time.time()
    with _public_api_cache_lock:
        cached = _public_api_cache.get(key)
        if not cached or now_ts - cached['stored_at'] >= ttl:
            return None
        response = jsonify(cached['payload'])
        response.headers['Cache-Control'] = f'public, max-age={ttl}'
        response.headers['X-CivikIndia-Cache'] = 'hit'
        return response


def _iter_month_starts(from_month_start, to_month_start):
    """Yield month starts in inclusive range."""
    if from_month_start is None or to_month_start is None:
        return []
    months = []
    cursor = from_month_start
    while cursor <= to_month_start:
        months.append(cursor)
        cursor = _shift_month(cursor, 1)
    return months


def _parse_dashboard_filters(default_month_window=False):
    """
    Parse dashboard filters from query params.
    Supports:
    - department_id (int)
    - status (enum)
    - from_month, to_month (YYYY-MM)
    """
    department_id = request.args.get('department_id', type=int)
    if department_id is not None and department_id <= 0:
        department_id = None

    status = (request.args.get('status') or '').strip()
    if status and status not in DASHBOARD_STATUSES:
        raise ValueError('Invalid status filter.')

    from_month_raw = (request.args.get('from_month') or '').strip()
    to_month_raw = (request.args.get('to_month') or '').strip()

    from_month_start = _parse_month_value(from_month_raw) if from_month_raw else None
    to_month_start = _parse_month_value(to_month_raw) if to_month_raw else None

    if from_month_raw and from_month_start is None:
        raise ValueError('Invalid from_month format. Use YYYY-MM.')
    if to_month_raw and to_month_start is None:
        raise ValueError('Invalid to_month format. Use YYYY-MM.')

    if from_month_start and not to_month_start:
        to_month_start = from_month_start
    if to_month_start and not from_month_start:
        from_month_start = to_month_start

    if from_month_start is None and to_month_start is None and default_month_window:
        to_month_start = _month_start(utc_now())
        from_month_start = _shift_month(to_month_start, -11)

    if from_month_start and to_month_start and from_month_start > to_month_start:
        raise ValueError('from_month must be earlier than or equal to to_month.')

    to_month_end = _shift_month(to_month_start, 1) if to_month_start else None

    return {
        'department_id': department_id,
        'status': status or None,
        'from_month_start': from_month_start,
        'to_month_start': to_month_start,
        'to_month_end': to_month_end,
        'from_month': from_month_start.strftime('%Y-%m') if from_month_start else '',
        'to_month': to_month_start.strftime('%Y-%m') if to_month_start else '',
    }


def _apply_dashboard_filters(query, filters, date_field=Complaint.submitted_at, include_time_window=True):
    """Apply reusable dashboard filters to a complaint query."""
    if filters.get('department_id'):
        query = query.filter(Complaint.department_id == filters['department_id'])

    if filters.get('status'):
        query = query.filter(Complaint.status == filters['status'])

    if include_time_window and filters.get('from_month_start') and filters.get('to_month_end'):
        query = query.filter(
            date_field >= filters['from_month_start'],
            date_field < filters['to_month_end']
        )

    return query


def _tokenize_for_classification(text):
    """Tokenize free-form text for lightweight classifier matching."""
    cleaned = ''.join(ch.lower() if ch.isalnum() else ' ' for ch in (text or ''))
    return {token for token in cleaned.split() if len(token) >= 3}


def _score_text_overlap(text_tokens, candidate_tokens):
    """Compute a simple overlap score between two token sets."""
    if not text_tokens or not candidate_tokens:
        return 0
    return len(text_tokens.intersection(candidate_tokens))


def _predict_department_and_service(description):
    """
    Predict department/service using deterministic keyword matching.
    Designed as an explainable fallback even without external AI APIs.
    """
    description_tokens = _tokenize_for_classification(description)
    if not description_tokens:
        return {
            'department_id': None,
            'department_name': None,
            'service_id': None,
            'service_name': None,
            'confidence': 0
        }

    analysis = analyze_complaint_text(description)
    category_hint = (analysis.get('category') or '').lower()

    services = Service.query.options(joinedload(Service.department)).all()
    best = None
    best_score = 0

    for service in services:
        dept_name = service.department.name if service.department else ''
        candidate_text = f'{service.name} {service.description or ""} {dept_name}'
        candidate_tokens = _tokenize_for_classification(candidate_text)

        score = _score_text_overlap(description_tokens, candidate_tokens)
        if category_hint:
            if category_hint in (dept_name or '').lower():
                score += 3
            if category_hint in (service.name or '').lower():
                score += 2

        # Reward direct service-name mentions.
        service_name_tokens = _tokenize_for_classification(service.name or '')
        score += _score_text_overlap(description_tokens, service_name_tokens) * 2

        if score > best_score:
            best_score = score
            best = service

    if not best or best_score <= 0:
        return {
            'department_id': None,
            'department_name': None,
            'service_id': None,
            'service_name': None,
            'confidence': 0
        }

    confidence = min(100, 35 + (best_score * 8))
    return {
        'department_id': best.department_id,
        'department_name': best.department.name if best.department else None,
        'service_id': best.id,
        'service_name': best.name,
        'confidence': confidence
    }


def _compute_dashboard_stats(filters):
    """Compute aggregate dashboard stats for current filter set."""
    base_query = _apply_dashboard_filters(Complaint.query, filters, include_time_window=True)

    total = base_query.count()
    pending = base_query.filter(Complaint.status == 'Pending').count()
    under_review = base_query.filter(Complaint.status == 'Under Review').count()
    action_taken = base_query.filter(Complaint.status == 'Action Taken').count()
    delayed = base_query.filter(Complaint.status == 'Delayed').count()
    reopened = base_query.filter(Complaint.status == 'Reopened').count()
    closed = base_query.filter(Complaint.status == 'Closed').count()
    high_priority = base_query.filter(
        Complaint.priority.in_(['High', 'Urgent']),
        Complaint.status.in_(Complaint.ACTIVE_STATUSES)
    ).count()

    closed_items = base_query.filter(Complaint.status == 'Closed').all()
    within_sla = sum(
        1 for item in closed_items
        if item.sla_due_at and item.resolved_at and item.resolved_at <= item.sla_due_at
    )
    sla_compliance = round((within_sla / len(closed_items) * 100), 2) if closed_items else 0
    resolution_rate = round((closed / total * 100), 2) if total > 0 else 0
    in_progress = under_review + action_taken + delayed + reopened
    backlog_rate = round(((pending + in_progress) / total * 100), 2) if total > 0 else 0

    negative = base_query.filter(Complaint.ai_sentiment == 'negative').count()
    urgent = high_priority
    repeated = base_query.filter(Complaint.reopen_count > 0).count()
    closed_with_feedback = sum(1 for complaint in closed_items if complaint.citizen_rating is not None)
    avg_resolution_hours = (
        round(sum(
            complaint.get_resolution_time()
            for complaint in closed_items
            if complaint.get_resolution_time()
        ) / len(closed_items), 2)
        if closed_items else 0
    )
    closed_feedback_rate = round((closed_with_feedback / len(closed_items) * 100), 2) if closed_items else 0

    return {
        'total': total,
        'pending': pending,
        'under_review': under_review,
        'action_taken': action_taken,
        'delayed': delayed,
        'reopened': reopened,
        'closed': closed,
        'high_priority': high_priority,
        'sla_compliance': sla_compliance,
        'resolution_rate': resolution_rate,
        'in_progress': in_progress,
        'backlog_rate': backlog_rate,
        'negative_percent': round((negative / total * 100), 2) if total > 0 else 0,
        'urgent_percent': round((urgent / total * 100), 2) if total > 0 else 0,
        'repeated_percent': round((repeated / total * 100), 2) if total > 0 else 0,
        'avg_resolution_hours': avg_resolution_hours,
        'feedback_rate': closed_feedback_rate
    }


def _compute_department_stats(filters):
    """Compute per-department stats for scoreboard and ranking."""
    departments_query = Department.query.order_by(Department.name.asc())
    if filters.get('department_id'):
        departments_query = departments_query.filter(Department.id == filters['department_id'])
    departments = departments_query.all()

    dept_stats = []
    for dept in departments:
        dept_query = _apply_dashboard_filters(
            Complaint.query.filter(Complaint.department_id == dept.id),
            filters,
            include_time_window=True
        )
        total = dept_query.count()
        pending = dept_query.filter(Complaint.status == 'Pending').count()
        closed = dept_query.filter(Complaint.status == 'Closed').count()
        delayed = dept_query.filter(Complaint.status == 'Delayed').count()
        resolution_rate = round((closed / total * 100), 1) if total > 0 else 0
        delay_penalty = round((delayed / total * 100) * 0.5, 1) if total > 0 else 0
        transparency_score = round(max(resolution_rate - delay_penalty, 0), 1)

        dept_stats.append({
            'id': dept.id,
            'name': dept.name,
            'total': total,
            'pending': pending,
            'closed': closed,
            'delayed': delayed,
            'resolution_rate': resolution_rate,
            'delay_penalty': delay_penalty,
            'score': transparency_score
        })

    ranked = sorted(
        [item for item in dept_stats if item['total'] > 0],
        key=lambda item: item['score'],
        reverse=True
    )
    best_department = ranked[0] if ranked else None
    worst_department = ranked[-1] if ranked else None

    return dept_stats, best_department, worst_department


def _compute_top_services(filters, limit=6):
    """Compute top services for trends and scoreboard sections."""
    base_query = _apply_dashboard_filters(Complaint.query, filters, include_time_window=True)

    rows = (
        base_query.join(Service, Complaint.service_id == Service.id)
        .with_entities(
            Service.name.label('service_name'),
            func.count(Complaint.id).label('count')
        )
        .group_by(Service.id, Service.name)
        .order_by(func.count(Complaint.id).desc(), Service.name.asc())
        .limit(limit)
        .all()
    )

    return [{'name': row.service_name, 'count': row.count} for row in rows]


# =============================================================================
# HOMEPAGE & STATIC PAGES
# =============================================================================

@public_bp.route('/')
def index():
    """Homepage with hero section and quick stats."""
    maybe_run_sla_escalations()
    stats, _, _ = _cached_public_payload('page_home_stats', Complaint.get_stats)
    departments = Department.query.all()
    
    return render_template('public/index.html', 
                          stats=stats, 
                          departments=departments)


@public_bp.route('/about')
def about():
    """About page explaining the portal."""
    return render_template('public/about.html')


@public_bp.route('/contact')
def contact():
    """GIGW contact and help desk information."""
    return render_template('public/static_page.html', page={
        'title': 'Contact Us',
        'icon': 'fa-headset',
        'summary': 'Official support channels for portal assistance, accessibility issues, and grievance guidance.',
        'sections': [
            {
                'heading': 'Help Desk',
                'items': [
                    'Email: support@civikindia.gov.in',
                    'Toll Free: 1800-11-0180',
                    'Anti-Corruption Helpline: 1064',
                    'Emergency Assistance: 112',
                ],
            },
            {
                'heading': 'Office Hours',
                'items': [
                    'Monday to Saturday: 9:00 AM to 6:00 PM',
                    'CivikIndia — Civic Accountability Platform, Main Civic Office',
                ],
            },
        ],
    })


@public_bp.route('/privacy')
def privacy():
    """Privacy statement for citizen-facing pages."""
    return render_template('public/static_page.html', page={
        'title': 'Privacy Policy',
        'icon': 'fa-user-shield',
        'summary': 'CivikIndia follows data minimisation and purpose limitation for grievance processing.',
        'sections': [
            {
                'heading': 'Information Collection',
                'items': [
                    'Anonymous complaint submission is supported without citizen login.',
                    'Optional contact details, if introduced or enabled, must be used only for grievance communication.',
                    'Operational logs are retained for audit, fraud prevention, and service reliability.',
                ],
            },
            {
                'heading': 'Data Use',
                'items': [
                    'Complaint records are used for routing, investigation, redressal, and public aggregate statistics.',
                    'Public dashboards expose aggregate information and do not reveal citizen identity.',
                ],
            },
        ],
    })


@public_bp.route('/terms')
def terms():
    """Terms of use page."""
    return render_template('public/static_page.html', page={
        'title': 'Terms of Use',
        'icon': 'fa-scale-balanced',
        'summary': 'Conditions for using the portal as an official municipal grievance channel.',
        'sections': [
            {
                'heading': 'Acceptable Use',
                'items': [
                    'Submit truthful, specific, and service-related grievance information.',
                    'Do not upload unlawful, abusive, misleading, or unrelated content.',
                    'Keep tracking IDs confidential and use them only for status lookup.',
                ],
            },
            {
                'heading': 'Service Scope',
                'items': [
                    'The portal supports complaint intake, tracking, analytics, and administrative workflow.',
                    'Emergency matters should also be reported through the appropriate emergency helpline.',
                ],
            },
        ],
    })


@public_bp.route('/how-it-works')
def how_it_works():
    """How it works page with process explanation."""
    return render_template('public/how_it_works.html')


@public_bp.route('/disclaimer')
def disclaimer():
    """Government website disclaimer page."""
    return render_template('public/static_page.html', page={
        'title': 'Disclaimer',
        'icon': 'fa-circle-info',
        'summary': 'Information on this portal is provided for grievance facilitation and public transparency.',
        'sections': [
            {
                'heading': 'Official Use',
                'items': [
                    'Every effort is made to keep information accurate and current.',
                    'Administrative decisions are taken by authorised officers according to applicable rules.',
                    'External links, where present, are provided for convenience and are not endorsements.',
                ],
            },
        ],
    })


@public_bp.route('/accessibility')
def accessibility_statement():
    """GIGW accessibility statement."""
    return render_template('public/static_page.html', page={
        'title': 'Accessibility Statement',
        'icon': 'fa-universal-access',
        'summary': 'CivikIndia is being upgraded toward WCAG 2.1 Level AA and GIGW accessibility expectations.',
        'sections': [
            {
                'heading': 'Accessibility Measures',
                'items': [
                    'Skip-to-content navigation is available as the first focusable control.',
                    'Keyboard-visible focus indicators are provided for interactive elements.',
                    'Pages use semantic landmarks including header, nav, main, and footer.',
                    'Feedback about accessibility barriers can be sent to support@civikindia.gov.in.',
                ],
            },
        ],
    })


@public_bp.route('/screen-reader-access')
def screen_reader_access():
    """Screen reader guidance page."""
    return render_template('public/static_page.html', page={
        'title': 'Screen Reader Access',
        'icon': 'fa-assistive-listening-systems',
        'summary': 'The portal is designed to support modern assistive technologies and keyboard navigation.',
        'sections': [
            {
                'heading': 'Recommended Access',
                'items': [
                    'Use the skip link to move directly to main content.',
                    'Navigate forms using labels and standard keyboard controls.',
                    'Use browser zoom controls up to 200 percent where needed.',
                ],
            },
        ],
    })


@public_bp.route('/website-policies')
def website_policies():
    """GIGW website policies page."""
    return render_template('public/static_page.html', page={
        'title': 'Website Policies',
        'icon': 'fa-file-contract',
        'summary': 'Portal policies covering copyright, privacy, hyperlinking, and acceptable use.',
        'sections': [
            {
                'heading': 'Policy Areas',
                'items': [
                    'Content is maintained for public grievance facilitation and transparency.',
                    'Hyperlinks to official resources should open without implying private endorsement.',
                    'Privacy and terms of use are available through the footer on every page.',
                ],
            },
        ],
    })


@public_bp.route('/help')
def help_page():
    """Citizen help page."""
    return render_template('public/static_page.html', page={
        'title': 'Help',
        'icon': 'fa-life-ring',
        'summary': 'Quick guidance for submitting, tracking, and understanding complaint status.',
        'sections': [
            {
                'heading': 'Common Tasks',
                'items': [
                    'Use Submit Complaint to report a municipal issue or corruption concern.',
                    'Use Track Complaint with the tracking ID shown after successful submission.',
                    'Use Public Dashboard and Geo Heatmap to view aggregate transparency data.',
                ],
            },
        ],
    })


@public_bp.route('/sitemap')
def sitemap():
    """Human-readable sitemap required by GIGW navigation guidance."""
    sitemap_sections = [
        {
            'heading': 'Citizen Services',
            'links': [
                ('Home', 'public.index'),
                ('Submit Complaint', 'public.submit_complaint'),
                ('Track Complaint', 'public.track_complaint'),
                ('How It Works', 'public.how_it_works'),
            ],
        },
        {
            'heading': 'Transparency',
            'links': [
                ('Public Dashboard', 'public.public_dashboard'),
                ('Geo Heatmap', 'public.geo_heatmap'),
                ('About CivikIndia', 'public.about'),
            ],
        },
        {
            'heading': 'Policies and Help',
            'links': [
                ('Accessibility Statement', 'public.accessibility_statement'),
                ('Screen Reader Access', 'public.screen_reader_access'),
                ('Website Policies', 'public.website_policies'),
                ('Privacy Policy', 'public.privacy'),
                ('Terms of Use', 'public.terms'),
                ('Disclaimer', 'public.disclaimer'),
                ('Help', 'public.help_page'),
                ('Contact Us', 'public.contact'),
            ],
        },
    ]
    return render_template('public/sitemap.html', sitemap_sections=sitemap_sections)


@public_bp.route('/favicon.ico')
def favicon():
    """Serve site favicon through static asset pipeline."""
    return redirect(url_for('static', filename='favicon.svg'))


@public_bp.route('/geo-heatmap')
def geo_heatmap():
    """Public geospatial complaint heatmap visualization."""
    maybe_run_sla_escalations()
    stats, _, _ = _cached_public_payload('page_geo_stats', Complaint.get_stats)
    departments = Department.query.order_by(Department.name.asc()).all()
    return render_template('public/geo_heatmap.html', stats=stats, departments=departments)


# =============================================================================
# COMPLAINT SUBMISSION
# =============================================================================

@public_bp.route('/submit', methods=['GET', 'POST'])
def submit_complaint():
    """
    Anonymous complaint submission form.
    No login required, no PII collected.
    """
    if request.method == 'POST':
        allowed, rate_error = _enforce_submit_rate_limit()
        if not allowed:
            flash(rate_error, 'danger')
            departments = Department.query.all()
            return render_template('public/submit.html',
                                  departments=departments,
                                  form_data=request.form), 429

        # reCAPTCHA v3 server-side verification
        from app.utils import verify_recaptcha
        recaptcha_token = request.form.get('g-recaptcha-response', '')
        captcha_valid, captcha_score = verify_recaptcha(recaptcha_token)
        if not captcha_valid:
            flash('Anti-spam verification failed. Please try again.', 'danger')
            departments = Department.query.all()
            return render_template('public/submit.html',
                                  departments=departments,
                                  form_data=request.form)
        
        # Get form data
        department_id = request.form.get('department_id', type=int)
        service_id = request.form.get('service_id', type=int)
        description = request.form.get('description', '').strip()
        state = (request.form.get('state') or '').strip() or None
        district = (request.form.get('district') or '').strip() or None
        city = (request.form.get('city') or '').strip() or None
        
        # New fields
        complaint_category = request.form.get('complaint_category', '').strip() or None
        ward_locality = request.form.get('ward_locality', '').strip() or None
        incident_date_str = request.form.get('incident_date', '').strip()
        officer_name_alleged = request.form.get('officer_name_alleged', '').strip() or None
        witness_available = request.form.get('witness_available') == 'yes'
        contact_preference = request.form.get('contact_preference', '').strip() or None
        voluntary_id = request.form.get('voluntary_id', '').strip() or None
        
        incident_date = None
        if incident_date_str:
            try:
                incident_date = datetime.strptime(incident_date_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        try:
            location_lat = _parse_optional_coordinate(
                request.form.get('location_lat'),
                'Latitude'
            )
            location_lng = _parse_optional_coordinate(
                request.form.get('location_lng'),
                'Longitude'
            )
        except ValueError as exc:
            flash(str(exc), 'danger')
            departments = Department.query.all()
            return render_template('public/submit.html',
                                  departments=departments,
                                  form_data=request.form)
        # Ensure coordinate pair is either both present or both absent.
        if (location_lat is None) != (location_lng is None):
            flash('Latitude and longitude must be provided together.', 'danger')
            departments = Department.query.all()
            return render_template('public/submit.html',
                                  departments=departments,
                                  form_data=request.form)

        # Optional geo validation
        if state and len(state) > 80:
            flash('State must be 80 characters or fewer.', 'danger')
            departments = Department.query.all()
            return render_template('public/submit.html',
                                  departments=departments,
                                  form_data=request.form)
        if district and len(district) > 120:
            flash('District must be 120 characters or fewer.', 'danger')
            departments = Department.query.all()
            return render_template('public/submit.html',
                                  departments=departments,
                                  form_data=request.form)
        if city and len(city) > 120:
            flash('City must be 120 characters or fewer.', 'danger')
            departments = Department.query.all()
            return render_template('public/submit.html',
                                  departments=departments,
                                  form_data=request.form)
        if location_lat is not None and not (-90 <= location_lat <= 90):
            flash('Latitude must be between -90 and 90.', 'danger')
            departments = Department.query.all()
            return render_template('public/submit.html',
                                  departments=departments,
                                  form_data=request.form)
        if location_lng is not None and not (-180 <= location_lng <= 180):
            flash('Longitude must be between -180 and 180.', 'danger')
            departments = Department.query.all()
            return render_template('public/submit.html',
                                  departments=departments,
                                  form_data=request.form)

        # AI Auto-classification fallback
        if (not department_id or not service_id) and description:
            prediction = _predict_department_and_service(description)
            if prediction:
                if not department_id and prediction.get('department_id'):
                    department_id = prediction['department_id']
                if not service_id and prediction.get('service_id'):
                    service_id = prediction['service_id']

        # Server-side validation
        errors = []
        service = None
        
        if not department_id:
            errors.append('Please select a department.')
        if not service_id:
            errors.append('Please select a service.')
        if not description or len(description) < MIN_COMPLAINT_DESCRIPTION_CHARACTERS:
            errors.append(f'Description must be at least {MIN_COMPLAINT_DESCRIPTION_CHARACTERS} characters.')
        if len(description) > 5000:
            errors.append('Description must not exceed 5000 characters.')
        evidence_file = request.files.get('evidence')
        if not evidence_file or not evidence_file.filename:
            errors.append('Evidence upload is required.')
        
        # Validate department and service relationship
        if department_id and service_id:
            service = db.session.get(Service, service_id)
            if not service or service.department_id != department_id:
                errors.append('Invalid service selection for this department.')

        if errors:
            for error in errors:
                flash(error, 'danger')
            departments = Department.query.all()
            return render_template('public/submit.html', 
                                  departments=departments,
                                  form_data=request.form)
        
        # Create complaint and then save evidence under the generated complaint id.
        evidence_file_record = None
        try:
            analysis = analyze_complaint_text(description)
            now = utc_now()
            complaint = Complaint(
                tracking_id=generate_tracking_id(),
                service_id=service_id,
                department_id=department_id,
                description=description,
                status='Awaiting Review',
                submitted_at=now,
                updated_at=now,
                priority=analysis['priority'],
                ai_category=analysis['category'],
                ai_sentiment=analysis['sentiment'],
                ai_urgent=analysis['urgent'],
                state=state,
                district=district,
                city=city,
                location_lat=location_lat,
                location_lng=location_lng,
                complaint_category=complaint_category,
                ward_locality=ward_locality,
                incident_date=incident_date,
                officer_name_alleged=officer_name_alleged,
                witness_available=witness_available,
                contact_preference=contact_preference,
                voluntary_id=voluntary_id
            )
            complaint.initialize_sla_due()
            db.session.add(complaint)
            db.session.flush()

            if 'complaint_id' in inspect.signature(save_uploaded_file).parameters:
                success, result = save_uploaded_file(evidence_file, complaint_id=complaint.id)
            else:
                success, result = save_uploaded_file(evidence_file)
            if not success:
                db.session.rollback()
                flash(f'File upload error: {result}', 'danger')
                departments = Department.query.all()
                return render_template('public/submit.html',
                                      departments=departments,
                                      form_data=request.form)

            evidence_file_record = EvidenceFile(
                complaint_id=complaint.id,
                uploaded_by_user_id=None,
                filename=result['filename'],
                original_filename=result['original_filename'],
                safe_filename=result.get('safe_filename'),
                mime_type=result['mime_type'],
                file_size=result['file_size'],
                byte_size=result.get('byte_size'),
                file_extension=result.get('file_extension'),
                encryption_iv=result.get('encryption_iv'),
                storage_path=result['relative_path'],
                storage_provider=result.get('storage_provider', 'local'),
                storage_bucket=result.get('storage_bucket'),
                storage_key=result.get('storage_key'),
                drive_backup_status=result.get('drive_backup_status', 'disabled'),
                encrypted=result.get('encrypted', False),
                file_hash_sha256=result.get('file_hash_sha256'),
                sha256_hash=result.get('sha256_hash')
            )
            complaint.evidence_path = evidence_file_record.storage_key or evidence_file_record.storage_path
            db.session.add(evidence_file_record)
            db.session.commit()

            # Notify internal staff channels (email/SMS) when configured.
            send_complaint_submission_notification(complaint.tracking_id)
            
            # Log the submission (anonymous - no user)
            log_action('COMPLAINT_SUBMITTED', 
                      details={
                          'tracking_id': complaint.tracking_id,
                          'priority': complaint.priority,
                          'ai_urgent': complaint.ai_urgent,
                          'ai_category': complaint.ai_category
                      })
            
            flash('Complaint submitted successfully!', 'success')
            return redirect(url_for('public.confirmation', 
                                   tracking_id=complaint.tracking_id))
            
        except Exception as e:
            db.session.rollback()
            if evidence_file_record and (evidence_file_record.storage_key or evidence_file_record.storage_path):
                try:
                    from app.utils import delete_uploaded_file
                    delete_uploaded_file(
                        evidence_file_record.storage_key or evidence_file_record.storage_path,
                        evidence_file_record.storage_provider
                    )
                except Exception as del_err:
                    current_app.logger.error(f'Failed to delete orphaned upload: {str(del_err)}')
            current_app.logger.error(f'Complaint submission error: {str(e)}')
            flash('Error submitting complaint. Please try again.', 'danger')
            departments = Department.query.all()
            return render_template('public/submit.html', 
                                  departments=departments,
                                  form_data=request.form)
    
    # GET request - show form
    departments = Department.query.all()
    return render_template('public/submit.html', departments=departments)


@public_bp.route('/submit-complaint')
def submit_complaint_legacy():
    """Backward-compatible path kept for older bookmarks / external links."""
    return redirect(url_for('public.submit_complaint'))


@public_bp.route('/confirmation/<path:tracking_id>')
def confirmation(tracking_id):
    """Confirmation page showing tracking ID."""
    complaint = Complaint.query.filter_by(tracking_id=tracking_id).first_or_404()
    return render_template('public/confirm.html', complaint=complaint)


@public_bp.route('/confirmation/<path:tracking_id>/receipt.pdf')
def complaint_receipt_pdf(tracking_id):
    """Generate a downloadable PDF receipt for a submitted complaint."""
    import io
    from flask import Response
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
    
    complaint = Complaint.query.filter_by(tracking_id=tracking_id).first_or_404()
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
    elements = []
    styles = getSampleStyleSheet()
    
    # Custom styles
    header_style = ParagraphStyle(
        'GovHeader', parent=styles['Title'],
        fontSize=16, textColor=colors.HexColor('#1a3c5e'),
        spaceAfter=4
    )
    subtitle_style = ParagraphStyle(
        'GovSubtitle', parent=styles['Normal'],
        fontSize=10, textColor=colors.HexColor('#555555'),
        spaceAfter=12, alignment=1  # center
    )
    tracking_style = ParagraphStyle(
        'TrackingID', parent=styles['Title'],
        fontSize=22, textColor=colors.HexColor('#138808'),
        spaceAfter=6, alignment=1
    )
    
    # Header
    elements.append(Paragraph("CivikIndia — Civic Grievance & Accountability Portal", header_style))
    elements.append(Paragraph("Government of India | भारत सरकार", subtitle_style))
    elements.append(Spacer(1, 12))
    
    # Tracking ID (prominent)
    elements.append(Paragraph("Complaint Receipt", styles['Heading2']))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(f"Tracking ID: {complaint.tracking_id}", tracking_style))
    elements.append(Spacer(1, 12))
    
    # QR Code
    try:
        import qrcode
        import qrcode.image.pil
        track_url = url_for('public.track_complaint', tracking_id=tracking_id, _external=True)
        qr_img = qrcode.make(track_url, box_size=4, border=2)
        qr_buffer = io.BytesIO()
        qr_img.save(qr_buffer, format='PNG')
        qr_buffer.seek(0)
        elements.append(Image(qr_buffer, width=1.5*inch, height=1.5*inch))
        elements.append(Paragraph("Scan to track your complaint online", ParagraphStyle(
            'QRCaption', parent=styles['Normal'], fontSize=8, alignment=1, textColor=colors.grey
        )))
        elements.append(Spacer(1, 12))
    except Exception:
        pass  # QR generation is optional
    
    # Complaint details table
    dept_name = complaint.department.name if complaint.department else 'N/A'
    service_name = complaint.service.name if complaint.service else 'N/A'
    submitted = complaint.submitted_at.strftime('%d %B %Y, %I:%M %p') if complaint.submitted_at else 'N/A'
    
    detail_data = [
        ['Field', 'Value'],
        ['Department', dept_name],
        ['Service', service_name],
        ['Category', complaint.complaint_category or 'N/A'],
        ['Priority', complaint.priority or 'Normal'],
        ['Status', complaint.status],
        ['Submitted On', submitted],
    ]
    
    detail_table = Table(detail_data, colWidths=[2*inch, 4*inch])
    detail_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a3c5e')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f5f5f5')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#f5f5f5'), colors.white]),
    ]))
    elements.append(detail_table)
    elements.append(Spacer(1, 20))
    
    # Notice
    elements.append(Paragraph(
        "<b>Important:</b> Please save this receipt for your records. "
        "You can track the status of your complaint at any time using the tracking ID above.",
        styles['Normal']
    ))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        "Anti-Corruption Helpline: <b>1064</b> | Vigilance Helpline: <b>1800-11-0180</b>",
        ParagraphStyle('Helpline', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor('#666666'))
    ))
    
    doc.build(elements)
    pdf_data = buffer.getvalue()
    buffer.close()
    
    filename = f'CivikIndia_Receipt_{tracking_id}.pdf'
    return Response(
        pdf_data,
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


# =============================================================================
# COMPLAINT TRACKING
# =============================================================================

@public_bp.route('/track', methods=['GET', 'POST'])
def track_complaint():
    """
    Public complaint tracking - no login required.
    Only shows non-sensitive information.
    """
    maybe_run_sla_escalations()
    complaint = None
    tracking_id = normalize_tracking_id(request.args.get('tracking_id', ''))

    if request.method == 'POST':
        tracking_id = normalize_tracking_id(request.form.get('tracking_id', ''))

    if tracking_id:
        if not validate_tracking_id(tracking_id):
            flash('Invalid tracking ID format.', 'danger')
        else:
            complaint = Complaint.query.filter_by(tracking_id=tracking_id).first()
            if not complaint:
                flash('Complaint not found. Please check your tracking ID.', 'warning')
            else:
                # Log tracking access (anonymous)
                log_action('COMPLAINT_TRACKED', details={'tracking_id': tracking_id})
    elif request.method == 'POST':
        flash('Please enter a tracking ID.', 'warning')
    
    evidence_file = None
    if complaint:
        from app.models import EvidenceFile
        evidence_file = EvidenceFile.query.filter_by(complaint_id=complaint.id).first()

    return render_template('public/track.html',
                          complaint=complaint,
                          evidence_file=evidence_file,
                          tracking_id=tracking_id)


@public_bp.route('/track-complaint', methods=['GET', 'POST'])
def track_complaint_legacy():
    """Backward-compatible path kept for older bookmarks / external links."""
    if request.method == 'POST':
        tracking_id = normalize_tracking_id(request.form.get('tracking_id', ''))
        if tracking_id:
            return redirect(url_for('public.track_complaint',
                                    tracking_id=tracking_id))
        return redirect(url_for('public.track_complaint'))

    return redirect(url_for('public.track_complaint',
                            tracking_id=normalize_tracking_id(request.args.get('tracking_id', ''))))


@public_bp.route('/complaint/<path:tracking_id>/reopen', methods=['POST'])
def reopen_complaint(tracking_id):
    """Allow citizen to reopen a closed complaint with reason."""
    complaint = Complaint.query.filter_by(tracking_id=tracking_id).first_or_404()
    reason = request.form.get('reopen_reason', '').strip()

    if len(reason) > 1000:
        flash('Reopen reason must be under 1000 characters.', 'danger')
        return redirect(url_for('public.track_complaint', tracking_id=tracking_id))

    success, message = complaint.reopen(reason)
    if not success:
        flash(message, 'danger')
        return redirect(url_for('public.track_complaint', tracking_id=tracking_id))

    db.session.commit()
    log_action('COMPLAINT_REOPENED_BY_CITIZEN', details={
        'tracking_id': tracking_id,
        'reopen_count': complaint.reopen_count
    })
    flash(message, 'success')
    return redirect(url_for('public.track_complaint', tracking_id=tracking_id))


@public_bp.route('/complaint/<path:tracking_id>/feedback', methods=['POST'])
def submit_feedback(tracking_id):
    """Allow anonymous rating/feedback after complaint closure."""
    complaint = Complaint.query.filter_by(tracking_id=tracking_id).first_or_404()
    rating = request.form.get('rating', type=int)
    feedback = request.form.get('feedback', '').strip()

    if feedback and len(feedback) > 1000:
        flash('Feedback must be under 1000 characters.', 'danger')
        return redirect(url_for('public.track_complaint', tracking_id=tracking_id))

    success, message = complaint.submit_citizen_feedback(rating or 0, feedback)
    if not success:
        flash(message, 'danger')
        return redirect(url_for('public.track_complaint', tracking_id=tracking_id))

    db.session.commit()
    log_action('CITIZEN_FEEDBACK_SUBMITTED', details={
        'tracking_id': tracking_id,
        'rating': complaint.citizen_rating
    })
    flash(message, 'success')
    return redirect(url_for('public.track_complaint', tracking_id=tracking_id))


# =============================================================================
# PUBLIC DASHBOARD
# =============================================================================

@public_bp.route('/dashboard')
def public_dashboard():
    """
    Public analytics dashboard.
    Shows aggregate statistics only - no sensitive data.
    """
    maybe_run_sla_escalations()

    default_filters = {
        'department_id': None,
        'status': None,
        'from_month_start': None,
        'to_month_start': None,
        'to_month_end': None,
        'from_month': '',
        'to_month': '',
    }

    stats = _compute_dashboard_stats(default_filters)
    dept_stats, best_department, worst_department = _compute_department_stats(default_filters)
    top_services = _compute_top_services(default_filters, limit=6)
    
    # Recent activity (last 30 days)
    thirty_days_ago = utc_now() - timedelta(days=30)
    recent_complaints = Complaint.query.filter(
        Complaint.submitted_at >= thirty_days_ago
    ).order_by(Complaint.submitted_at.desc()).limit(10).all()
    
    return render_template('public/dashboard.html',
                          stats=stats,
                          dept_stats=dept_stats,
                          top_services=top_services,
                          best_department=best_department,
                          worst_department=worst_department,
                          recent_complaints=recent_complaints,
                          status_options=DASHBOARD_STATUSES)


# =============================================================================
# API ENDPOINTS
# =============================================================================

@public_bp.route('/api/services/<int:department_id>')
def get_services(department_id):
    """
    AJAX endpoint to get services for a department.
    Used in complaint form for dynamic service dropdown.
    """
    services = Service.query.filter_by(department_id=department_id).all()
    return jsonify([service.to_dict() for service in services])


@public_bp.route('/api/stats')
def get_stats():
    """API endpoint for statistics (used by charts)."""
    maybe_run_sla_escalations()
    return _cached_public_json('stats', Complaint.get_stats)


@public_bp.route('/api/dashboard/overview')
def get_dashboard_overview():
    """Filtered dashboard payload for client-side interactive updates."""
    maybe_run_sla_escalations()

    try:
        filters = _parse_dashboard_filters(default_month_window=False)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    def build_payload():
        stats = _compute_dashboard_stats(filters)
        dept_stats, best_department, worst_department = _compute_department_stats(filters)
        top_services = _compute_top_services(filters)
        ranked_departments = sorted(
            dept_stats,
            key=lambda item: (item['score'], item['total']),
            reverse=True
        )
        active_departments = len([item for item in dept_stats if item['total'] > 0])

        recent_query = _apply_dashboard_filters(
            Complaint.query.options(
                joinedload(Complaint.department),
                joinedload(Complaint.service)
            ),
            filters,
            include_time_window=True
        )
        recent_complaints = recent_query.order_by(Complaint.submitted_at.desc()).limit(10).all()
        recent_serialized = []
        for complaint in recent_complaints:
            recent_serialized.append({
                'tracking_id': complaint.tracking_id,
                'department': complaint.department.name if complaint.department else '',
                'service': complaint.service.name if complaint.service else '',
                'status': complaint.status,
                'status_badge': STATUS_BADGE_CLASSES.get(complaint.status, 'badge-secondary'),
                'submitted_at': (
                    complaint.submitted_at.strftime('%d %b %Y, %I:%M %p')
                    if complaint.submitted_at else 'N/A'
                )
            })

        return {
            'filters': {
                'department_id': filters.get('department_id'),
                'status': filters.get('status') or '',
                'from_month': filters.get('from_month') or '',
                'to_month': filters.get('to_month') or '',
            },
            'stats': stats,
            'top_services': top_services,
            'active_departments': active_departments,
            'best_department': best_department,
            'worst_department': worst_department,
            'dept_stats': ranked_departments,
            'recent_complaints': recent_serialized,
        }

    return _cached_public_json('dashboard_overview', build_payload)


@public_bp.route('/api/chart/monthly')
def get_monthly_chart_data():
    """Get monthly complaint data for Chart.js."""
    try:
        filters = _parse_dashboard_filters(default_month_window=True)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    def build_payload():
        base_query = _apply_dashboard_filters(
            Complaint.query,
            filters,
            include_time_window=False
        )

        labels = []
        data = []
        for month_start in _iter_month_starts(filters['from_month_start'], filters['to_month_start']):
            month_end = _shift_month(month_start, 1)
            count = base_query.filter(
                Complaint.submitted_at >= month_start,
                Complaint.submitted_at < month_end
            ).count()
            labels.append(month_start.strftime('%b %Y'))
            data.append(count)

        return {'labels': labels, 'data': data}

    return _cached_public_json('chart_monthly', build_payload)


@public_bp.route('/api/chart/dept')
def get_dept_chart_data():
    """Get department-wise complaint data for Chart.js."""
    try:
        filters = _parse_dashboard_filters(default_month_window=False)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    def build_payload():
        departments_query = Department.query.order_by(Department.name.asc())
        if filters.get('department_id'):
            departments_query = departments_query.filter(Department.id == filters['department_id'])
        departments = departments_query.all()

        labels = []
        data = []
        for dept in departments:
            count = _apply_dashboard_filters(
                Complaint.query.filter(Complaint.department_id == dept.id),
                filters,
                include_time_window=True
            ).count()
            labels.append(dept.name)
            data.append(count)

        return {'labels': labels, 'data': data}

    return _cached_public_json('chart_dept', build_payload)


@public_bp.route('/api/chart/status')
def get_status_chart_data():
    """Get status breakdown for Chart.js doughnut chart."""
    try:
        filters = _parse_dashboard_filters(default_month_window=False)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    def build_payload():
        base_query = _apply_dashboard_filters(
            Complaint.query,
            filters,
            include_time_window=True
        )

        statuses = DASHBOARD_STATUSES
        data = []
        for status in statuses:
            count = base_query.filter(Complaint.status == status).count()
            data.append(count)

        return {'labels': statuses, 'data': data}

    return _cached_public_json('chart_status', build_payload)


@public_bp.route('/api/chart/resolution-time')
def get_resolution_time_chart_data():
    """Average resolution hours per month for closed complaints."""
    try:
        filters = _parse_dashboard_filters(default_month_window=True)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    def build_payload():
        base_query = _apply_dashboard_filters(
            Complaint.query.filter(Complaint.resolved_at.isnot(None)),
            filters,
            date_field=Complaint.resolved_at,
            include_time_window=False
        )

        labels = []
        values = []
        for month_start in _iter_month_starts(filters['from_month_start'], filters['to_month_start']):
            month_end = _shift_month(month_start, 1)
            closed = base_query.filter(
                Complaint.resolved_at >= month_start,
                Complaint.resolved_at < month_end
            ).all()
            avg_hours = round(
                sum(c.get_resolution_time() or 0 for c in closed) / len(closed),
                2
            ) if closed else 0
            labels.append(month_start.strftime('%b %Y'))
            values.append(avg_hours)

        return {'labels': labels, 'data': values}

    return _cached_public_json('chart_resolution_time', build_payload)


@public_bp.route('/api/chart/sla-compliance')
def get_sla_compliance_chart_data():
    """SLA compliance percentage per month for closed complaints."""
    try:
        filters = _parse_dashboard_filters(default_month_window=True)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    def build_payload():
        base_query = _apply_dashboard_filters(
            Complaint.query.filter(Complaint.resolved_at.isnot(None)),
            filters,
            date_field=Complaint.resolved_at,
            include_time_window=False
        )

        labels = []
        values = []
        for month_start in _iter_month_starts(filters['from_month_start'], filters['to_month_start']):
            month_end = _shift_month(month_start, 1)
            closed = base_query.filter(
                Complaint.resolved_at >= month_start,
                Complaint.resolved_at < month_end
            ).all()
            within = sum(1 for c in closed if c.sla_due_at and c.resolved_at and c.resolved_at <= c.sla_due_at)
            compliance = round((within / len(closed) * 100), 2) if closed else 0
            labels.append(month_start.strftime('%b %Y'))
            values.append(compliance)

        return {'labels': labels, 'data': values}

    return _cached_public_json('chart_sla_compliance', build_payload)


@public_bp.route('/api/public/data')
def public_data_api():
    """Public transparency dataset (aggregate only)."""
    def build_payload():
        stats = Complaint.get_stats()
        departments = []
        for dept in Department.query.all():
            q = Complaint.query.filter_by(department_id=dept.id)
            total = q.count()
            closed = q.filter_by(status='Closed').count()
            delayed = q.filter_by(status='Delayed').count()
            departments.append({
                'department': dept.name,
                'total': total,
                'closed': closed,
                'delayed': delayed,
                'resolution_rate': round((closed / total * 100), 2) if total else 0
            })
        return {'stats': stats, 'departments': departments}

    return _cached_public_json('public_data', build_payload)


@public_bp.route('/api/public/export/monthly.csv')
def export_monthly_csv():
    """Export monthly anonymized complaint data as CSV."""
    month_value = request.args.get('month', utc_now().strftime('%Y-%m'))
    try:
        month_start = datetime.strptime(month_value, '%Y-%m')
    except ValueError:
        return jsonify({'error': 'Invalid month format. Use YYYY-MM.'}), 400

    if month_start.month == 12:
        month_end = month_start.replace(year=month_start.year + 1, month=1)
    else:
        month_end = month_start.replace(month=month_start.month + 1)

    complaints = Complaint.query.filter(
        Complaint.submitted_at >= month_start,
        Complaint.submitted_at < month_end
    ).order_by(Complaint.submitted_at.asc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'tracking_id', 'department', 'service', 'status', 'priority',
        'submitted_at', 'resolved_at', 'reopen_count', 'citizen_rating'
    ])
    for complaint in complaints:
        writer.writerow([
            complaint.tracking_id,
            complaint.department.name if complaint.department else '',
            complaint.service.name if complaint.service else '',
            complaint.status,
            complaint.priority,
            complaint.submitted_at.isoformat() if complaint.submitted_at else '',
            complaint.resolved_at.isoformat() if complaint.resolved_at else '',
            complaint.reopen_count or 0,
            complaint.citizen_rating or ''
        ])

    csv_data = output.getvalue()
    output.close()
    filename = f'civikindia_public_export_{month_value}.csv'
    return Response(
        csv_data,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@public_bp.route('/api/geo/heatmap')
def get_geo_heatmap_data():
    """Return geo-tagged complaint points for heatmap rendering."""
    try:
        geo_filters = _parse_geo_filters()
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    cached_response = _public_cache_hit_response('geo_heatmap')
    if cached_response is not None:
        return cached_response

    allowed, rate_error = _enforce_geo_rate_limit()
    if not allowed:
        return jsonify({'error': rate_error}), 429

    maybe_run_sla_escalations()

    def build_payload():
        requested_limit = geo_filters['limit']

        query = Complaint.query.options(joinedload(Complaint.department)).filter(
            Complaint.location_lat.isnot(None),
            Complaint.location_lng.isnot(None)
        )

        if geo_filters.get('status'):
            query = query.filter(Complaint.status == geo_filters['status'])
        if geo_filters.get('priority'):
            if geo_filters['priority'] == 'High':
                query = query.filter(Complaint.priority.in_(['High', 'Urgent']))
            else:
                query = query.filter(Complaint.priority == geo_filters['priority'])
        if geo_filters.get('state'):
            query = query.filter(Complaint.state == geo_filters['state'])
        if geo_filters.get('district'):
            query = query.filter(Complaint.district == geo_filters['district'])
        if geo_filters.get('city'):
            query = query.filter(Complaint.city == geo_filters['city'])
        if geo_filters.get('department_id'):
            query = query.filter(Complaint.department_id == geo_filters['department_id'])

        complaints = query.order_by(Complaint.submitted_at.desc()).limit(requested_limit).all()
        return [
            {
                'lat': complaint.location_lat,
                'lng': complaint.location_lng,
                'tracking_id': complaint.tracking_id,
                'status': complaint.status,
                'priority': complaint.priority,
                'state': complaint.state,
                'district': complaint.district,
                'city': complaint.city,
                'department_id': complaint.department_id,
                'department_name': complaint.department.name if complaint.department else 'Unassigned',
                'submitted_at': complaint.submitted_at.isoformat() if complaint.submitted_at else None
            } for complaint in complaints
        ]

    return _cached_public_json('geo_heatmap', build_payload)


@public_bp.route('/api/ai/assist', methods=['POST'])
@csrf.exempt
def ai_assist():
    """
    AI assistant for complaint drafting and homepage guidance.
    Returns guidance only; does not store chat content.
    """
    if not request.is_json:
        return jsonify({'error': 'JSON request body required.'}), 400

    payload = request.get_json(silent=True) or {}
    message = (payload.get('message') or '').strip()
    assistant_mode = (payload.get('assistant') or '').strip().lower()
    description = (payload.get('description') or '').strip()
    department_id = payload.get('department_id')
    service_id = payload.get('service_id')

    if len(message) < 5:
        return jsonify({'error': 'Please provide a more specific question.'}), 400
    if len(message) > 1000:
        return jsonify({'error': 'Question is too long. Keep it under 1000 characters.'}), 400

    # Abuse guard for unauthenticated endpoint.
    allowed, rate_error = _enforce_ai_rate_limit()
    if not allowed:
        return jsonify({'error': rate_error}), 429

    department_name = None
    service_name = None
    if isinstance(department_id, int):
        department = db.session.get(Department, department_id)
        department_name = department.name if department else None
    if isinstance(service_id, int):
        service = db.session.get(Service, service_id)
        service_name = service.name if service else None

    api_key = current_app.config.get('OPENAI_API_KEY')
    model = current_app.config.get('OPENAI_MODEL', 'gpt-4o-mini')
    base_url = (current_app.config.get('OPENAI_BASE_URL') or '').strip()
    if not api_key:
        fallback = _fallback_ai_reply(
            assistant_mode, message, description, department_name, service_name
        )
        return jsonify({'reply': fallback, 'fallback': True}), 200

    try:
        from openai import OpenAI
    except ImportError:
        fallback = _fallback_ai_reply(
            assistant_mode, message, description, department_name, service_name
        )
        return jsonify({'reply': fallback, 'fallback': True}), 200

    if assistant_mode == 'homepage':
        system_prompt = (
            "You are the homepage help chatbot for CivikIndia — Civic Grievance & Accountability Portal. "
            "Help citizens use the portal effectively. "
            "Be concise, practical, and neutral. "
            "Do not request or encourage sharing personal identifiers. "
            "When relevant, guide users to these routes: /submit, /track, /dashboard, /geo-heatmap. "
            "Output plain text only."
        )
        user_prompt = (
            f"Citizen question: {message}\n\n"
            "Respond with:\n"
            "1) direct answer in 2-4 short sentences\n"
            "2) next best action in one line"
        )
    else:
        system_prompt = (
            "You assist citizens in drafting municipal complaints. "
            "Be concise, practical, and neutral. "
            "Do not request personal identifiers. "
            "Focus on facts: what happened, where, when, impact, and evidence. "
            "Output plain text."
        )
        user_prompt = (
            f"Citizen question: {message}\n"
            f"Department: {department_name or 'Not selected'}\n"
            f"Service: {service_name or 'Not selected'}\n"
            f"Current complaint draft: {description[:2000] if description else 'None'}\n\n"
            "Provide:\n"
            "1) quick guidance\n"
            "2) an improved complaint draft (120-220 words)\n"
            "3) a short checklist of missing details"
        )

    try:
        client_kwargs = {'api_key': api_key}
        if base_url:
            client_kwargs['base_url'] = base_url
        client = OpenAI(**client_kwargs)
        completion = client.chat.completions.create(
            model=model,
            temperature=0.2,
            max_tokens=500,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
        )
        reply = (completion.choices[0].message.content or '').strip()
        if not reply:
            return jsonify({'error': 'AI assistant returned an empty response.'}), 502
        return jsonify({'reply': reply}), 200
    except Exception:
        current_app.logger.exception('AI assistant request failed.')
        fallback = _fallback_ai_reply(
            assistant_mode, message, description, department_name, service_name
        )
        return jsonify({'reply': fallback, 'fallback': True}), 200


@public_bp.route('/api/ai/classify', methods=['POST'])
@csrf.exempt
def ai_classify():
    """
    Lightweight AI classification endpoint.
    Suggests department/service and urgency signals before submission.
    """
    if not request.is_json:
        return jsonify({'error': 'JSON request body required.'}), 400

    payload = request.get_json(silent=True) or {}
    description = (payload.get('description') or '').strip()

    if len(description) < 20:
        return jsonify({'error': 'Please provide at least 20 characters for classification.'}), 400
    if len(description) > 5000:
        return jsonify({'error': 'Description is too long. Keep it under 5000 characters.'}), 400

    analysis = analyze_complaint_text(description)
    prediction = _predict_department_and_service(description)

    return _no_cache_json({
        'priority': analysis.get('priority'),
        'urgent': bool(analysis.get('urgent')),
        'sentiment': analysis.get('sentiment'),
        'category': analysis.get('category'),
        'department_id': prediction.get('department_id'),
        'department_name': prediction.get('department_name'),
        'service_id': prediction.get('service_id'),
        'service_name': prediction.get('service_name'),
        'confidence': prediction.get('confidence', 0)
    })


# =============================================================================
# HEALTH CHECK
# =============================================================================

@public_bp.route('/health')
@public_bp.route('/healthz')
def health_check():
    """Health check endpoint for monitoring."""
    try:
        # Test database connection
        db.session.execute(text('SELECT 1'))
        return jsonify({
            'status': 'healthy',
            'database': 'connected',
            'timestamp': utc_now().isoformat()
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'database': 'disconnected',
            'error': str(e),
            'timestamp': utc_now().isoformat()
        }), 503
