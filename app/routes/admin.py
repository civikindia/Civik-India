"""
Civik India Admin Routes
Admin-specific management routes.
"""
import csv
import io
import json
from flask import Blueprint, render_template, request, flash, redirect, url_for, session, jsonify, current_app, Response, abort
from sqlalchemy.orm import joinedload
from datetime import datetime, timedelta

from app import db
from app.clock import utc_now
from app.models import User, Department, Service, Complaint, AuditLog, EscalationContact, TrendingNews
from app.utils import admin_required, log_action, maybe_run_sla_escalations
from app.tasks import send_officer_welcome_notification, send_status_update_notification

admin_bp = Blueprint('admin', __name__)


def _build_officer_performance_records(limit=None):
    """Calculate officer performance index records."""
    officers = User.query.filter(
        User.role.in_(['officer', 'zonal_officer', 'commissioner']),
        User.is_active.is_(True)
    ).all()

    records = []
    for officer in officers:
        handled = Complaint.query.filter_by(assigned_to=officer.id).all()
        total = len(handled)
        closed = [c for c in handled if c.status == 'Closed']
        avg_hours = None
        if closed:
            avg_hours = sum(c.get_resolution_time() or 0 for c in closed) / len(closed)
        rated = [c.citizen_rating for c in closed if c.citizen_rating]
        avg_rating = (sum(rated) / len(rated)) if rated else 0
        speed_component = 0
        if avg_hours is not None:
            speed_component = max(0, 100 - min(avg_hours, 100))
        closure_component = (len(closed) / total * 100) if total else 0
        rating_component = (avg_rating / 5 * 100) if rated else 0
        performance_index = round(
            (speed_component * 0.4) + (closure_component * 0.4) + (rating_component * 0.2),
            2
        )

        records.append({
            'username': officer.username,
            'role': officer.role,
            'handled': total,
            'closed': len(closed),
            'avg_resolution_hours': round(avg_hours, 2) if avg_hours is not None else None,
            'avg_rating': round(avg_rating, 2) if rated else None,
            'performance_index': performance_index
        })

    records.sort(key=lambda r: r['performance_index'], reverse=True)
    if isinstance(limit, int) and limit > 0:
        return records[:limit]
    return records


# =============================================================================
# DASHBOARD
# =============================================================================

@admin_bp.route('/dashboard')
@admin_required
def dashboard():
    """Admin dashboard with system overview."""
    maybe_run_sla_escalations()

    # Overall stats
    stats = Complaint.get_stats()
    
    # Additional metrics
    total_officers = User.query.filter(
        User.role.in_(['officer', 'zonal_officer', 'commissioner'])
    ).count()
    total_departments = Department.query.count()
    
    # Recent complaints
    recent_complaints = Complaint.query.order_by(
        Complaint.submitted_at.desc()
    ).limit(10).all()
    
    # Recent audit logs
    recent_logs = AuditLog.query.order_by(
        AuditLog.timestamp.desc()
    ).limit(10).all()
    top_officers = _build_officer_performance_records(limit=8)
    
    # Department performance
    departments = Department.query.all()
    dept_performance = []
    for dept in departments:
        dept_complaints = Complaint.query.filter_by(department_id=dept.id)
        total = dept_complaints.count()
        closed = dept_complaints.filter_by(status='Closed').count()
        delayed = dept_complaints.filter_by(status='Delayed').count()
        
        # Calculate average resolution time
        resolved = dept_complaints.filter(Complaint.resolved_at.isnot(None)).all()
        avg_hours = None
        if resolved:
            total_hours = sum(c.get_resolution_time() or 0 for c in resolved)
            avg_hours = round(total_hours / len(resolved), 2)

        rated = [c.citizen_rating for c in resolved if c.citizen_rating]
        avg_rating = round(sum(rated) / len(rated), 2) if rated else None
        resolution_rate = round((closed / total * 100), 1) if total > 0 else 0
        delay_penalty = round((delayed / total * 100) * 0.5, 1) if total > 0 else 0
        performance_score = round(max(resolution_rate - delay_penalty, 0), 1)
        
        dept_performance.append({
            'name': dept.name,
            'total': total,
            'closed': closed,
            'delayed': delayed,
            'resolution_rate': resolution_rate,
            'performance_score': performance_score,
            'avg_rating': avg_rating,
            'avg_resolution_hours': avg_hours
        })
    
    return render_template('admin/dashboard.html',
                          stats=stats,
                          total_officers=total_officers,
                          total_departments=total_departments,
                          recent_complaints=recent_complaints,
                          recent_logs=recent_logs,
                          dept_performance=dept_performance,
                          top_officers=top_officers)


@admin_bp.route('/inbox')
@admin_required
def review_inbox():
    # Fetch complaints in 'Awaiting Review' status, ordered by submitted_at (oldest first)
    complaints = Complaint.query.filter_by(status='Awaiting Review')\
        .options(joinedload(Complaint.evidence_files))\
        .order_by(Complaint.submitted_at.asc()).all()
    return render_template('admin/review_inbox.html', complaints=complaints)


@admin_bp.route('/complaint/<int:complaint_id>/approve', methods=['POST'])
@admin_required
def approve_complaint(complaint_id):
    complaint = db.session.get(Complaint, complaint_id)
    if not complaint:
        flash('Complaint not found.', 'danger')
        return redirect(url_for('admin.review_inbox'))
    
    if complaint.status != 'Awaiting Review':
        flash('Complaint is not in a state that can be approved.', 'warning')
        return redirect(url_for('admin.review_inbox'))
    
    # Priority & Officer assignment from form input
    priority_override = request.form.get('priority')
    admin_notes = request.form.get('admin_notes', '').strip()
    
    if priority_override in ['Low', 'Normal', 'High', 'Urgent']:
        complaint.priority = priority_override
        
    complaint.status = 'Pending'
    complaint.admin_notes = admin_notes if admin_notes else None
    complaint.reviewed_by_id = session.get('user_id')
    complaint.reviewed_at = utc_now()
    
    # Assign officer based on department escalation hierarchy
    officer = complaint.assign_by_escalation_hierarchy()
    
    # Recalculate/initialize SLA due date based on new priority
    complaint.sla_due_at = None
    complaint.initialize_sla_due()
    
    db.session.commit()
    
    # Log audit event
    AuditLog.create_entry(
        user_id=session.get('user_id'),
        username=session.get('username', 'admin'),
        role='admin',
        action='COMPLAINT_APPROVED',
        details=json.dumps({
            'complaint_id': complaint.id,
            'tracking_id': complaint.tracking_id,
            'assigned_to': officer.username if officer else None,
            'priority': complaint.priority
        })
    )
    
    flash(f'Complaint {complaint.tracking_id} approved and dispatched successfully.', 'success')
    return redirect(url_for('admin.review_inbox'))


@admin_bp.route('/complaint/<int:complaint_id>/reject', methods=['POST'])
@admin_required
def reject_complaint(complaint_id):
    complaint = db.session.get(Complaint, complaint_id)
    if not complaint:
        flash('Complaint not found.', 'danger')
        return redirect(url_for('admin.review_inbox'))
        
    if complaint.status != 'Awaiting Review':
        flash('Complaint is not in a state that can be rejected.', 'warning')
        return redirect(url_for('admin.review_inbox'))
        
    rejection_reason = request.form.get('rejection_reason', '').strip()
    admin_notes = request.form.get('admin_notes', '').strip()
    
    if not rejection_reason or len(rejection_reason) < 20:
        flash('Rejection reason must be at least 20 characters long.', 'danger')
        return redirect(url_for('admin.review_inbox'))
        
    complaint.status = 'Rejected'
    complaint.rejection_reason = rejection_reason
    complaint.admin_notes = admin_notes if admin_notes else None
    complaint.reviewed_by_id = session.get('user_id')
    complaint.reviewed_at = utc_now()
    
    db.session.commit()
    
    # Log audit event
    AuditLog.create_entry(
        user_id=session.get('user_id'),
        username=session.get('username', 'admin'),
        role='admin',
        action='COMPLAINT_REJECTED',
        details=json.dumps({
            'complaint_id': complaint.id,
            'tracking_id': complaint.tracking_id,
            'reason': rejection_reason
        })
    )
    
    flash(f'Complaint {complaint.tracking_id} has been rejected.', 'warning')
    return redirect(url_for('admin.review_inbox'))


# =============================================================================
# T1-B — BULK APPROVE / REJECT
# =============================================================================

@admin_bp.route('/inbox/bulk-approve', methods=['POST'])
@admin_required
def bulk_approve_complaints():
    """Approve multiple 'Awaiting Review' complaints in a single action."""
    raw_ids = request.form.get('complaint_ids', '').strip()
    if not raw_ids:
        flash('No complaints selected for bulk approval.', 'warning')
        return redirect(url_for('admin.review_inbox'))

    complaint_ids = []
    for part in raw_ids.split(','):
        part = part.strip()
        if part.isdigit():
            complaint_ids.append(int(part))

    if not complaint_ids:
        flash('Invalid selection. Please try again.', 'danger')
        return redirect(url_for('admin.review_inbox'))

    approved_count = 0
    skipped_count = 0
    reviewer_id = session.get('user_id')
    reviewer_name = session.get('username', 'admin')
    now = utc_now()

    for cid in complaint_ids:
        complaint = db.session.get(Complaint, cid)
        if not complaint or complaint.status != 'Awaiting Review':
            skipped_count += 1
            continue

        complaint.status = 'Pending'
        complaint.reviewed_by_id = reviewer_id
        complaint.reviewed_at = now
        officer = complaint.assign_by_escalation_hierarchy()
        complaint.sla_due_at = None
        complaint.initialize_sla_due()

        AuditLog.create_entry(
            user_id=reviewer_id,
            username=reviewer_name,
            role='admin',
            action='COMPLAINT_APPROVED',
            details=json.dumps({
                'complaint_id': complaint.id,
                'tracking_id': complaint.tracking_id,
                'assigned_to': officer.username if officer else None,
                'bulk': True
            })
        )
        approved_count += 1

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('Bulk approve error: %s', exc)
        flash('An error occurred during bulk approval. Please try again.', 'danger')
        return redirect(url_for('admin.review_inbox'))

    msg = f'{approved_count} complaint(s) approved and dispatched.'
    if skipped_count:
        msg += f' {skipped_count} skipped (already processed).'
    flash(msg, 'success')
    return redirect(url_for('admin.review_inbox'))


@admin_bp.route('/inbox/bulk-reject', methods=['POST'])
@admin_required
def bulk_reject_complaints():
    """Reject multiple 'Awaiting Review' complaints with a shared reason."""
    raw_ids = request.form.get('complaint_ids', '').strip()
    rejection_reason = request.form.get('rejection_reason', '').strip()

    if not raw_ids:
        flash('No complaints selected for bulk rejection.', 'warning')
        return redirect(url_for('admin.review_inbox'))

    if not rejection_reason or len(rejection_reason) < 20:
        flash('Rejection reason must be at least 20 characters.', 'danger')
        return redirect(url_for('admin.review_inbox'))

    complaint_ids = []
    for part in raw_ids.split(','):
        part = part.strip()
        if part.isdigit():
            complaint_ids.append(int(part))

    if not complaint_ids:
        flash('Invalid selection. Please try again.', 'danger')
        return redirect(url_for('admin.review_inbox'))

    rejected_count = 0
    skipped_count = 0
    reviewer_id = session.get('user_id')
    reviewer_name = session.get('username', 'admin')
    now = utc_now()

    for cid in complaint_ids:
        complaint = db.session.get(Complaint, cid)
        if not complaint or complaint.status != 'Awaiting Review':
            skipped_count += 1
            continue

        complaint.status = 'Rejected'
        complaint.rejection_reason = rejection_reason
        complaint.reviewed_by_id = reviewer_id
        complaint.reviewed_at = now

        AuditLog.create_entry(
            user_id=reviewer_id,
            username=reviewer_name,
            role='admin',
            action='COMPLAINT_REJECTED',
            details=json.dumps({
                'complaint_id': complaint.id,
                'tracking_id': complaint.tracking_id,
                'reason': rejection_reason,
                'bulk': True
            })
        )
        rejected_count += 1

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('Bulk reject error: %s', exc)
        flash('An error occurred during bulk rejection. Please try again.', 'danger')
        return redirect(url_for('admin.review_inbox'))

    msg = f'{rejected_count} complaint(s) rejected.'
    if skipped_count:
        msg += f' {skipped_count} skipped (already processed).'
    flash(msg, 'warning')
    return redirect(url_for('admin.review_inbox'))


# =============================================================================
# T1-C — PER-COMPLAINT PRINT VIEW
# =============================================================================

@admin_bp.route('/complaint/<path:tracking_id>/print')
@admin_required
def print_complaint(tracking_id):
    """Render a print-optimised single-page complaint report."""
    complaint = Complaint.query.filter_by(tracking_id=tracking_id).first_or_404()
    audit_logs = AuditLog.query.filter(
        AuditLog.details.contains(tracking_id)
    ).order_by(AuditLog.timestamp.desc()).limit(10).all()
    log_action('COMPLAINT_PRINT_VIEW', details={'tracking_id': tracking_id})
    return render_template('admin/print_complaint.html',
                           complaint=complaint,
                           audit_logs=audit_logs)


# =============================================================================
# T1-D — ADMIN KPI STATS API
# =============================================================================

@admin_bp.route('/api/admin/kpi-stats')
@admin_required
def admin_kpi_stats():
    """Lightweight JSON endpoint for real-time dashboard KPI refresh."""
    stats = Complaint.get_stats()
    return jsonify({
        'total': stats.get('total', 0),
        'pending': stats.get('pending', 0),
        'awaiting_review': stats.get('awaiting_review', 0),
        'closed': stats.get('closed', 0),
        'resolution_rate': stats.get('resolution_rate', 0),
        'delayed': stats.get('delayed', 0),
        'reopened': stats.get('reopened', 0),
        'sla_compliance': stats.get('sla_compliance', 0),
        'action_taken': stats.get('action_taken', 0),
    })



@admin_bp.route('/complaints')
@admin_required
def complaints():
    """List all complaints with filtering."""
    maybe_run_sla_escalations()

    # Get filter parameters
    status = request.args.get('status', '')
    department_id = request.args.get('department_id', type=int)
    search = request.args.get('search', '').strip()
    
    # Build query
    query = Complaint.query
    
    if status:
        query = query.filter_by(status=status)
    
    if department_id:
        query = query.filter_by(department_id=department_id)
    
    if search:
        query = query.filter(
            db.or_(
                Complaint.tracking_id.ilike(f'%{search}%'),
                Complaint.description.ilike(f'%{search}%')
            )
        )
    
    # Pagination
    page = request.args.get('page', 1, type=int)
    per_page = 20
    pagination = query.order_by(Complaint.submitted_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    departments = Department.query.all()
    
    return render_template('admin/complaints.html',
                          complaints=pagination.items,
                          pagination=pagination,
                          departments=departments,
                          filters={
                              'status': status,
                              'department_id': department_id,
                              'search': search
                          })


@admin_bp.route('/complaint/<path:tracking_id>')
@admin_required
def complaint_detail(tracking_id):
    """View complaint details as admin."""
    maybe_run_sla_escalations()
    complaint = Complaint.query.filter_by(tracking_id=tracking_id)\
        .options(joinedload(Complaint.evidence_files))\
        .first_or_404()
    
    # Get audit logs for this complaint
    audit_logs = AuditLog.query.filter(
        AuditLog.details.contains(tracking_id)
    ).order_by(AuditLog.timestamp.desc()).all()
    
    # Get all officers for reassignment
    officers = User.query.filter(
        User.role.in_(['officer', 'zonal_officer', 'commissioner']),
        User.is_active.is_(True)
    ).all()
    
    return render_template('admin/complaint_detail.html',
                          complaint=complaint,
                          audit_logs=audit_logs,
                          officers=officers)


@admin_bp.route('/complaint/<path:tracking_id>/assign', methods=['POST'])
@admin_required
def assign_complaint(tracking_id):
    """Assign complaint to an officer."""
    complaint = Complaint.query.filter_by(tracking_id=tracking_id).first_or_404()
    
    officer_id = request.form.get('officer_id', type=int)
    
    if not officer_id:
        flash('Please select an officer.', 'warning')
        return redirect(url_for('admin.complaint_detail', tracking_id=tracking_id))
    
    officer = db.session.get(User, officer_id)
    if not officer or officer.role not in ['officer', 'zonal_officer', 'commissioner']:
        flash('Invalid officer selected.', 'danger')
        return redirect(url_for('admin.complaint_detail', tracking_id=tracking_id))
    
    try:
        old_assignee = complaint.assigned_officer.username if complaint.assigned_officer else 'Unassigned'
        
        complaint.assigned_to = officer_id
        if complaint.status == 'Pending':
            complaint.status = 'Under Review'
        
        db.session.commit()
        
        log_action('COMPLAINT_ASSIGNED_BY_ADMIN',
                  details={
                      'tracking_id': tracking_id,
                      'assigned_to': officer.username,
                      'previous_assignee': old_assignee
                  })
        
        flash(f'Complaint assigned to {officer.username}.', 'success')
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Assignment error: {str(e)}')
        flash('Error assigning complaint.', 'danger')
    
    return redirect(url_for('admin.complaint_detail', tracking_id=tracking_id))


@admin_bp.route('/complaint/<path:tracking_id>/update', methods=['POST'])
@admin_required
def update_complaint_status(tracking_id):
    """Update complaint status as admin."""
    complaint = Complaint.query.filter_by(tracking_id=tracking_id).first_or_404()
    
    new_status = request.form.get('status')
    notes = request.form.get('notes', '').strip()
    
    if not complaint.can_transition_to(new_status):
        flash(f"Cannot transition from '{complaint.status}' to '{new_status}'", 'danger')
        return redirect(url_for('admin.complaint_detail', tracking_id=tracking_id))
    
    try:
        old_status = complaint.status
        success, message = complaint.update_status(new_status, notes)
        
        if success:
            db.session.commit()
            
            log_action('STATUS_UPDATE_BY_ADMIN',
                      details={
                          'tracking_id': tracking_id,
                          'old_status': old_status,
                          'new_status': new_status
                      })
            
            send_status_update_notification(tracking_id, new_status)
            
            flash('Status updated successfully.', 'success')
        else:
            flash(message, 'danger')
            
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Status update error: {str(e)}')
        flash('Error updating status.', 'danger')
    
    return redirect(url_for('admin.complaint_detail', tracking_id=tracking_id))


# =============================================================================
# USER MANAGEMENT
# =============================================================================

@admin_bp.route('/complaint/<path:tracking_id>/evidence')
@admin_required
def download_evidence(tracking_id):
    """Download private evidence file for a complaint through the backend."""
    from app.models import EvidenceFile
    from app.utils import evidence_download_response
    
    complaint = Complaint.query.filter_by(tracking_id=tracking_id).first_or_404()
    
    if not complaint.evidence_path:
        flash('No evidence file attached to this complaint.', 'warning')
        return redirect(url_for('admin.complaint_detail', tracking_id=tracking_id))
    
    evidence_file = EvidenceFile.query.filter_by(complaint_id=complaint.id).first()
    if not evidence_file:
        flash('Evidence metadata not found.', 'danger')
        return redirect(url_for('admin.complaint_detail', tracking_id=tracking_id))

    try:
        response = evidence_download_response(evidence_file, tracking_id)
        log_action('EVIDENCE_DOWNLOADED', details={
            'tracking_id': tracking_id,
            'filename': evidence_file.original_filename
        })
        return response
    except FileNotFoundError:
        flash('Evidence file not found on private storage.', 'danger')
        return redirect(url_for('admin.complaint_detail', tracking_id=tracking_id))
    except Exception as e:
        current_app.logger.error(f'Evidence download error: {str(e)}')
        flash('Error downloading evidence file.', 'danger')
        return redirect(url_for('admin.complaint_detail', tracking_id=tracking_id))


@admin_bp.route('/complaint/<path:tracking_id>/evidence/preview')
@admin_required
def preview_evidence(tracking_id):
    """
    Stream evidence file inline for in-browser preview (image, PDF, video).
    Uses Content-Disposition: inline instead of attachment.
    Only accessible to authenticated admins.
    """
    from app.models import EvidenceFile
    from app.utils import evidence_preview_response

    complaint = Complaint.query.filter_by(tracking_id=tracking_id).first_or_404()

    if not complaint.evidence_path:
        abort(404)

    evidence_file = EvidenceFile.query.filter_by(complaint_id=complaint.id).first()
    if not evidence_file:
        abort(404)

    try:
        response = evidence_preview_response(evidence_file, tracking_id)
        log_action('EVIDENCE_PREVIEWED', details={
            'tracking_id': tracking_id,
            'filename': evidence_file.original_filename,
            'mime_type': evidence_file.mime_type
        })
        return response
    except FileNotFoundError:
        abort(404)
    except Exception as e:
        current_app.logger.error(f'Evidence preview error: {str(e)}')
        abort(500)


@admin_bp.route("/officers")
@admin_required
def officers():
    """List all officers."""
    officers = User.query.filter(
        User.role.in_(['officer', 'zonal_officer', 'commissioner'])
    ).all()
    departments = Department.query.all()
    
    return render_template('admin/officers.html', 
                          officers=officers,
                          departments=departments)


@admin_bp.route('/officers/create', methods=['POST'])
@admin_required
def create_officer():
    """Create new officer account."""
    username = request.form.get('username', '').strip()
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '')
    role = request.form.get('role', 'officer').strip()
    department_id = request.form.get('department_id', type=int)
    
    # Validation
    errors = []
    if not username or len(username) < 3:
        errors.append('Username must be at least 3 characters.')
    if role not in ['officer', 'zonal_officer', 'commissioner']:
        errors.append('Invalid role selected.')
    if User.query.filter_by(username=username).first():
        errors.append('Username already exists.')
    
    from app.utils.password_policy import validate_password
    pw_valid, pw_errors = validate_password(password, username=username)
    if not pw_valid:
        errors.extend(pw_errors)
    
    if errors:
        for error in errors:
            flash(error, 'danger')
        return redirect(url_for('admin.officers'))
    
    try:
        officer = User(
            username=username,
            email=email or None,
            role=role,
            department_id=department_id,
            is_active=True
        )
        officer.set_password(password)
        
        db.session.add(officer)
        db.session.commit()
        
        log_action('OFFICER_CREATED',
                  details={
                      'username': username,
                      'role': role,
                      'department_id': department_id
                  })

        if officer.email:
            send_officer_welcome_notification(officer.id, password)
        
        flash(f'Officer {username} created successfully.', 'success')
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Officer creation error: {str(e)}')
        flash('Error creating officer.', 'danger')
    
    return redirect(url_for('admin.officers'))


@admin_bp.route('/officers/<int:officer_id>/toggle', methods=['POST'])
@admin_required
def toggle_officer(officer_id):
    """Enable/disable officer account."""
    officer = db.session.get(User, officer_id)
    if not officer:
        abort(404)
    
    if officer.role not in ['officer', 'zonal_officer', 'commissioner']:
        flash('Invalid user.', 'danger')
        return redirect(url_for('admin.officers'))
    
    try:
        officer.is_active = not officer.is_active
        db.session.commit()
        
        status = 'enabled' if officer.is_active else 'disabled'
        
        log_action('OFFICER_TOGGLED',
                  details={
                      'username': officer.username,
                      'new_status': status
                  })
        
        flash(f'Officer {officer.username} {status}.', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash('Error updating officer.', 'danger')
    
    return redirect(url_for('admin.officers'))


@admin_bp.route('/officers/<int:officer_id>/reset-password', methods=['POST'])
@admin_required
def reset_officer_password(officer_id):
    """Reset officer password."""
    officer = db.session.get(User, officer_id)
    if not officer:
        abort(404)
    
    if officer.role not in ['officer', 'zonal_officer', 'commissioner']:
        flash('Invalid user.', 'danger')
        return redirect(url_for('admin.officers'))
    
    new_password = request.form.get('new_password', '')
    
    from app.utils.password_policy import validate_password
    pw_valid, pw_errors = validate_password(new_password, username=officer.username)
    if not pw_valid:
        for err in pw_errors:
            flash(err, 'danger')
        return redirect(url_for('admin.officers'))
    
    try:
        officer.set_password(new_password)
        db.session.commit()
        
        log_action('OFFICER_PASSWORD_RESET',
                  details={'username': officer.username})
        
        flash(f'Password reset for {officer.username}.', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash('Error resetting password.', 'danger')
    
    return redirect(url_for('admin.officers'))


# =============================================================================
# DEPARTMENT MANAGEMENT
# =============================================================================

@admin_bp.route('/departments')
@admin_required
def departments():
    """List all departments."""
    departments = Department.query.all()
    return render_template('admin/departments.html', departments=departments)


@admin_bp.route('/escalation-contacts')
@admin_required
def escalation_contacts():
    """Manage escalation contacts used by SLA breach workflows."""
    departments = Department.query.order_by(Department.name).all()
    contacts = EscalationContact.query.options(
        joinedload(EscalationContact.department)
    ).order_by(EscalationContact.department_id, EscalationContact.level, EscalationContact.name).all()
    return render_template(
        'admin/escalation_contacts.html',
        departments=departments,
        contacts=contacts
    )


@admin_bp.route('/escalation-contacts/create', methods=['POST'])
@admin_required
def create_escalation_contact():
    """Create an escalation contact for SLA notifications."""
    department_id = request.form.get('department_id', type=int)
    level = request.form.get('level', type=int)
    name = request.form.get('name', '').strip()
    designation = request.form.get('designation', '').strip()
    email = request.form.get('email', '').strip()
    phone = request.form.get('phone', '').strip()
    whatsapp_number = request.form.get('whatsapp_number', '').strip()

    if not department_id or not level or not name or not designation:
        flash('Department, level, name, and designation are required.', 'danger')
        return redirect(url_for('admin.escalation_contacts'))

    if level not in (1, 2, 3, 4):
        flash('Escalation level must be between L1 and L4.', 'danger')
        return redirect(url_for('admin.escalation_contacts'))

    if not any([email, phone, whatsapp_number]):
        flash('Provide at least one contact channel.', 'danger')
        return redirect(url_for('admin.escalation_contacts'))

    department = db.session.get(Department, department_id)
    if not department:
        flash('Invalid department selected.', 'danger')
        return redirect(url_for('admin.escalation_contacts'))

    contact = EscalationContact(
        department_id=department_id,
        level=level,
        name=name,
        designation=designation,
        email=email or None,
        phone=phone or None,
        whatsapp_number=whatsapp_number or None,
        is_active=True
    )
    db.session.add(contact)
    db.session.commit()
    log_action('ESCALATION_CONTACT_CREATED', details={
        'department_id': department_id,
        'level': level,
        'name': name
    })
    flash('Escalation contact created successfully.', 'success')
    return redirect(url_for('admin.escalation_contacts'))


@admin_bp.route('/escalation-contacts/<int:contact_id>/toggle', methods=['POST'])
@admin_required
def toggle_escalation_contact(contact_id):
    """Activate or deactivate an escalation contact."""
    contact = db.session.get(EscalationContact, contact_id)
    if not contact:
        flash('Escalation contact not found.', 'danger')
        return redirect(url_for('admin.escalation_contacts'))

    contact.is_active = not contact.is_active
    db.session.commit()
    log_action('ESCALATION_CONTACT_TOGGLED', details={
        'contact_id': contact.id,
        'is_active': contact.is_active
    })
    flash('Escalation contact status updated.', 'success')
    return redirect(url_for('admin.escalation_contacts'))


@admin_bp.route('/departments/create', methods=['POST'])
@admin_required
def create_department():
    """Create new department."""
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    
    if not name:
        flash('Department name is required.', 'danger')
        return redirect(url_for('admin.departments'))
    
    if Department.query.filter_by(name=name).first():
        flash('Department already exists.', 'danger')
        return redirect(url_for('admin.departments'))
    
    try:
        dept = Department(name=name, description=description)
        db.session.add(dept)
        db.session.commit()
        
        log_action('DEPARTMENT_CREATED',
                  details={'name': name})
        
        flash(f'Department {name} created.', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash('Error creating department.', 'danger')
    
    return redirect(url_for('admin.departments'))


@admin_bp.route('/departments/<int:dept_id>/services', methods=['POST'])
@admin_required
def add_service(dept_id):
    """Add service to department."""
    dept = db.session.get(Department, dept_id)
    if not dept:
        abort(404)
    
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    sla_days = request.form.get('sla_days', type=int) or 7
    
    if not name:
        flash('Service name is required.', 'danger')
        return redirect(url_for('admin.departments'))
    if sla_days < 1 or sla_days > 60:
        flash('SLA days must be between 1 and 60.', 'danger')
        return redirect(url_for('admin.departments'))
    
    try:
        service = Service(name=name, description=description, department_id=dept_id, sla_days=sla_days)
        db.session.add(service)
        db.session.commit()
        
        log_action('SERVICE_CREATED',
                  details={
                      'name': name,
                      'sla_days': sla_days,
                      'department': dept.name
                  })
        
        flash(f'Service {name} added to {dept.name}.', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash('Error adding service.', 'danger')
    
    return redirect(url_for('admin.departments'))


# =============================================================================
# AUDIT LOGS
# =============================================================================

@admin_bp.route('/audit-logs')
@admin_required
def audit_logs():
    """View audit logs with filtering."""
    # Get filter parameters
    action = request.args.get('action', '')
    username = request.args.get('username', '').strip()
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    
    # Build query
    query = AuditLog.query
    
    if action:
        query = query.filter_by(action=action)
    
    if username:
        query = query.filter(AuditLog.username.ilike(f'%{username}%'))
    
    if date_from:
        try:
            from_date = datetime.strptime(date_from, '%Y-%m-%d')
            query = query.filter(AuditLog.timestamp >= from_date)
        except ValueError:
            pass
    
    if date_to:
        try:
            to_date = datetime.strptime(date_to, '%Y-%m-%d')
            to_date = to_date.replace(hour=23, minute=59, second=59)
            query = query.filter(AuditLog.timestamp <= to_date)
        except ValueError:
            pass
    
    # Pagination
    page = request.args.get('page', 1, type=int)
    per_page = 50
    pagination = query.order_by(AuditLog.timestamp.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    # Get unique actions for filter dropdown
    actions = db.session.query(AuditLog.action).distinct().all()
    actions = [a[0] for a in actions]
    
    return render_template('admin/audit_logs.html',
                          logs=pagination.items,
                          pagination=pagination,
                          actions=actions,
                          filters={
                              'action': action,
                              'username': username,
                              'date_from': date_from,
                              'date_to': date_to
                          })


@admin_bp.route('/audit-logs/verify')
@admin_required
def verify_audit_logs():
    """Verify hash chain integrity of audit logs."""
    logs = AuditLog.query.order_by(AuditLog.id.asc()).all()
    if not logs:
        return jsonify({'valid': True, 'checked': 0, 'message': 'No logs to verify.'})

    previous_hash = None
    for log in logs:
        if not log.verify_integrity():
            return jsonify({
                'valid': False,
                'checked': log.id,
                'message': 'Row hash mismatch detected.',
                'broken_log_id': log.id
            }), 409
        if previous_hash is not None and log.previous_hash != previous_hash:
            return jsonify({
                'valid': False,
                'checked': log.id,
                'message': 'Chain linkage mismatch detected.',
                'broken_log_id': log.id
            }), 409
        previous_hash = log.row_hash

    return jsonify({
        'valid': True,
        'checked': len(logs),
        'message': 'Audit chain verified successfully.'
    })


@admin_bp.route('/audit-logs/rebuild', methods=['POST'])
@admin_required
def rebuild_audit_chain():
    """Recompute audit log chain hashes and previous hash pointers."""
    if session.get('role') != 'admin':
        flash('Only admins can rebuild the audit chain.', 'danger')
        return redirect(url_for('admin.audit_logs'))

    result = AuditLog.rebuild_chain(dry_run=False)
    log_action(
        'AUDIT_CHAIN_REBUILT',
        user=db.session.get(User, session['user_id'])
    )
    flash(
        f'Audit chain rebuilt for {result["total"]} logs; {result["repaired"]} entries updated.',
        'success' if result['repaired'] else 'info'
    )

    return redirect(url_for('admin.audit_logs'))


# =============================================================================
# API ENDPOINTS
# =============================================================================

@admin_bp.route('/api/system-stats')
@admin_required
def get_system_stats():
    """API endpoint for system statistics."""
    maybe_run_sla_escalations()

    # Complaint stats
    complaint_stats = Complaint.get_stats()
    
    # User stats
    user_stats = {
        'total_users': User.query.count(),
        'officers': User.query.filter(User.role.in_(['officer', 'zonal_officer', 'commissioner'])).count(),
        'admins': User.query.filter_by(role='admin').count(),
        'active_users': User.query.filter_by(is_active=True).count()
    }
    
    # Department stats
    dept_stats = []
    for dept in Department.query.all():
        complaints = Complaint.query.filter_by(department_id=dept.id)
        dept_stats.append({
            'name': dept.name,
            'total': complaints.count(),
            'pending': complaints.filter_by(status='Pending').count(),
            'closed': complaints.filter_by(status='Closed').count()
        })
    
    # Activity in last 24 hours
    yesterday = utc_now() - timedelta(days=1)
    recent_activity = {
        'new_complaints': Complaint.query.filter(Complaint.submitted_at >= yesterday).count(),
        'resolved': Complaint.query.filter(Complaint.resolved_at >= yesterday).count(),
        'admin_actions': AuditLog.query.filter(
            AuditLog.timestamp >= yesterday,
            AuditLog.role == 'admin'
        ).count()
    }
    
    return jsonify({
        'complaints': complaint_stats,
        'users': user_stats,
        'departments': dept_stats,
        'recent_activity': recent_activity
    })


@admin_bp.route('/api/analytics/sentiment')
@admin_required
def get_sentiment_analytics():
    """Get sentiment and urgency distribution for dashboard widgets."""
    total = Complaint.query.count()
    negative = Complaint.query.filter_by(ai_sentiment='negative').count()
    neutral = Complaint.query.filter_by(ai_sentiment='neutral').count()
    positive = Complaint.query.filter_by(ai_sentiment='positive').count()
    urgent = Complaint.query.filter_by(ai_urgent=True).count()
    reopened = Complaint.query.filter(Complaint.reopen_count > 0).count()

    def pct(value):
        return round((value / total * 100), 2) if total else 0

    return jsonify({
        'total': total,
        'negative_percent': pct(negative),
        'neutral_percent': pct(neutral),
        'positive_percent': pct(positive),
        'urgent_percent': pct(urgent),
        'reopened_percent': pct(reopened)
    })


@admin_bp.route('/api/analytics/service-trends')
@admin_required
def get_service_trends():
    """Top recurring service issues for analytics."""
    from sqlalchemy import func

    top_services = db.session.query(
        Service.name,
        func.count(Complaint.id).label('count')
    ).join(Complaint, Complaint.service_id == Service.id)\
        .group_by(Service.id, Service.name)\
        .order_by(func.count(Complaint.id).desc())\
        .limit(5).all()

    return jsonify({
        'labels': [row[0] for row in top_services],
        'data': [row[1] for row in top_services]
    })


@admin_bp.route('/api/analytics/officer-performance')
@admin_required
def get_officer_performance():
    """Officer performance index by workload, speed, and citizen ratings."""
    return jsonify(_build_officer_performance_records())


@admin_bp.route('/export/complaints.csv')
@admin_required
def export_complaints_csv():
    """Export complaints with analytics fields for governance/RTI workflows."""
    complaints = Complaint.query.order_by(Complaint.submitted_at.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'tracking_id', 'department', 'service', 'status', 'priority',
        'ai_sentiment', 'ai_urgent', 'escalation_level', 'reopen_count',
        'submitted_at', 'resolved_at', 'citizen_rating'
    ])

    for complaint in complaints:
        writer.writerow([
            complaint.tracking_id,
            complaint.department.name if complaint.department else '',
            complaint.service.name if complaint.service else '',
            complaint.status,
            complaint.priority,
            complaint.ai_sentiment,
            complaint.ai_urgent,
            complaint.escalation_level,
            complaint.reopen_count,
            complaint.submitted_at.isoformat() if complaint.submitted_at else '',
            complaint.resolved_at.isoformat() if complaint.resolved_at else '',
            complaint.citizen_rating or ''
        ])

    csv_data = output.getvalue()
    output.close()
    filename = f'Civik_India_Complaints_Export_{utc_now().strftime("%Y%m%d_%H%M%S")}.csv'
    return Response(
        csv_data,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@admin_bp.route('/export/complaints.pdf')
@admin_required
def export_complaints_pdf():
    """Export complaints summary as a PDF report."""
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    import io
    
    complaints = Complaint.query.order_by(Complaint.submitted_at.desc()).limit(100).all() # Limit for demo
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter))
    elements = []
    
    styles = getSampleStyleSheet()
    elements.append(Paragraph("Civik India Complaints Report (Recent 100)", styles['Title']))
    
    data = [['Tracking ID', 'Department', 'Service', 'Status', 'Priority', 'Submitted Date']]
    for c in complaints:
        data.append([
            c.tracking_id,
            c.department.name if c.department else 'N/A',
            (c.service.name[:20] + '..') if c.service and len(c.service.name)>20 else (c.service.name if c.service else 'N/A'),
            c.status,
            c.priority,
            c.submitted_at.strftime('%Y-%m-%d %H:%M') if c.submitted_at else 'N/A'
        ])
        
    t = Table(data)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a56db')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    
    elements.append(t)
    doc.build(elements)
    
    pdf_out = buffer.getvalue()
    buffer.close()
    
    filename = f'Civik_India_Complaints_Export_{utc_now().strftime("%Y%m%d_%H%M%S")}.pdf'
    return Response(
        pdf_out,
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


# =============================================================================
# AUDIT CHAIN EXPORT
# =============================================================================

@admin_bp.route('/audit-logs/export.json')
@admin_required
def export_audit_logs_json():
    """Export full audit chain as JSON for forensic submission."""
    import json as json_mod
    
    logs = AuditLog.query.order_by(AuditLog.id.asc()).all()
    chain = []
    for log in logs:
        chain.append({
            'id': log.id,
            'timestamp': log.timestamp.isoformat() if log.timestamp else None,
            'username': log.username,
            'role': log.role,
            'action': log.action,
            'details': log.details,
            'ip_address': log.ip_address,
            'previous_hash': log.previous_hash,
            'row_hash': log.row_hash,
        })
    
    json_data = json_mod.dumps(chain, indent=2, ensure_ascii=False)
    filename = f'Civik_India_Audit_Log_{utc_now().strftime("%Y%m%d_%H%M%S")}.json'
    
    log_action('AUDIT_CHAIN_EXPORTED', details={'format': 'json', 'records': len(chain)})
    
    return Response(
        json_data,
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@admin_bp.route('/audit-logs/export.csv')
@admin_required
def export_audit_logs_csv():
    """Export full audit chain as CSV."""
    logs = AuditLog.query.order_by(AuditLog.id.asc()).all()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['id', 'timestamp', 'username', 'role', 'action', 'details', 
                     'ip_address', 'previous_hash', 'row_hash'])
    
    for log in logs:
        writer.writerow([
            log.id,
            log.timestamp.isoformat() if log.timestamp else '',
            log.username or '',
            log.role or '',
            log.action,
            log.details or '',
            log.ip_address or '',
            log.previous_hash or '',
            log.row_hash or '',
        ])
    
    csv_data = output.getvalue()
    output.close()
    filename = f'Civik_India_Audit_Log_{utc_now().strftime("%Y%m%d_%H%M%S")}.csv'
    
    log_action('AUDIT_CHAIN_EXPORTED', details={'format': 'csv', 'records': len(logs)})
    
    return Response(
        csv_data,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


# =============================================================================
# RTI COMPLIANCE REPORT
# =============================================================================

@admin_bp.route('/reports/rti')
@admin_required
def rti_compliance_report():
    """RTI Act 2005 compliance report — complaints older than 30 days still open."""
    from datetime import timedelta
    
    cutoff = utc_now() - timedelta(days=30)
    
    overdue = Complaint.query.filter(
        Complaint.submitted_at < cutoff,
        Complaint.status.notin_(['Closed'])
    ).order_by(Complaint.submitted_at.asc()).all()
    
    # Calculate age in days for each
    now = utc_now()
    report_data = []
    for c in overdue:
        age_days = (now - c.submitted_at).days if c.submitted_at else 0
        sla_status = 'Breached' if c.sla_due_at and now > c.sla_due_at else 'Within SLA'
        report_data.append({
            'complaint': c,
            'age_days': age_days,
            'sla_status': sla_status,
        })
    
    return render_template('admin/rti_report.html',
                          report_data=report_data,
                          total_overdue=len(overdue),
                          cutoff_date=cutoff.strftime('%d/%m/%Y'))


# =============================================================================
# TRENDING NEWS MANAGEMENT
# =============================================================================

@admin_bp.route('/trending-news')
@admin_required
def trending_news_list():
    """List all trending news items for management."""
    items = TrendingNews.query.order_by(
        TrendingNews.display_order.asc(),
        TrendingNews.created_at.desc()
    ).all()
    return render_template('admin/trending_news.html', items=items)


@admin_bp.route('/trending-news/create', methods=['POST'])
@admin_required
def create_trending_news():
    """Create a new trending news item."""
    headline = request.form.get('headline', '').strip()
    link_url = request.form.get('link_url', '').strip()
    badge_label = request.form.get('badge_label', '').strip()
    display_order = request.form.get('display_order', 0, type=int)

    if not headline or len(headline) < 5:
        flash('Headline must be at least 5 characters.', 'danger')
        return redirect(url_for('admin.trending_news_list'))

    if len(headline) > 300:
        flash('Headline must be 300 characters or fewer.', 'danger')
        return redirect(url_for('admin.trending_news_list'))

    item = TrendingNews(
        headline=headline,
        link_url=link_url or None,
        badge_label=badge_label or None,
        display_order=display_order,
        is_active=True,
        created_by_id=session.get('user_id')
    )
    db.session.add(item)
    db.session.commit()

    AuditLog.create_entry(
        user_id=session.get('user_id'),
        username=session.get('username', 'admin'),
        role='admin',
        action='TRENDING_NEWS_CREATED',
        details=json.dumps({'headline': headline, 'id': item.id})
    )

    flash('Trending news item created successfully.', 'success')
    return redirect(url_for('admin.trending_news_list'))


@admin_bp.route('/trending-news/<int:item_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_trending_news(item_id):
    """Edit an existing trending news item."""
    item = db.session.get(TrendingNews, item_id)
    if not item:
        flash('News item not found.', 'danger')
        return redirect(url_for('admin.trending_news_list'))

    if request.method == 'POST':
        headline = request.form.get('headline', '').strip()
        link_url = request.form.get('link_url', '').strip()
        badge_label = request.form.get('badge_label', '').strip()
        display_order = request.form.get('display_order', 0, type=int)

        if not headline or len(headline) < 5:
            flash('Headline must be at least 5 characters.', 'danger')
            return redirect(url_for('admin.edit_trending_news', item_id=item_id))

        old_headline = item.headline
        item.headline = headline
        item.link_url = link_url or None
        item.badge_label = badge_label or None
        item.display_order = display_order
        db.session.commit()

        AuditLog.create_entry(
            user_id=session.get('user_id'),
            username=session.get('username', 'admin'),
            role='admin',
            action='TRENDING_NEWS_UPDATED',
            details=json.dumps({'id': item_id, 'old_headline': old_headline, 'new_headline': headline})
        )

        flash('News item updated.', 'success')
        return redirect(url_for('admin.trending_news_list'))

    return render_template('admin/trending_news_edit.html', item=item)


@admin_bp.route('/trending-news/<int:item_id>/toggle', methods=['POST'])
@admin_required
def toggle_trending_news(item_id):
    """Toggle active/inactive for a trending news item."""
    item = db.session.get(TrendingNews, item_id)
    if not item:
        flash('News item not found.', 'danger')
        return redirect(url_for('admin.trending_news_list'))

    item.is_active = not item.is_active
    db.session.commit()

    AuditLog.create_entry(
        user_id=session.get('user_id'),
        username=session.get('username', 'admin'),
        role='admin',
        action='TRENDING_NEWS_TOGGLED',
        details=json.dumps({'id': item_id, 'is_active': item.is_active, 'headline': item.headline})
    )

    status = 'activated' if item.is_active else 'deactivated'
    flash(f'News item {status}.', 'success')
    return redirect(url_for('admin.trending_news_list'))


@admin_bp.route('/trending-news/<int:item_id>/delete', methods=['POST'])
@admin_required
def delete_trending_news(item_id):
    """Permanently delete a trending news item."""
    item = db.session.get(TrendingNews, item_id)
    if not item:
        flash('News item not found.', 'danger')
        return redirect(url_for('admin.trending_news_list'))

    headline = item.headline
    db.session.delete(item)
    db.session.commit()

    AuditLog.create_entry(
        user_id=session.get('user_id'),
        username=session.get('username', 'admin'),
        role='admin',
        action='TRENDING_NEWS_DELETED',
        details=json.dumps({'id': item_id, 'headline': headline})
    )

    flash('News item deleted.', 'warning')
    return redirect(url_for('admin.trending_news_list'))


@admin_bp.route('/api/trending-news')
def api_trending_news():
    """Public JSON API — returns active trending news items for the ticker."""
    items = TrendingNews.query.filter_by(is_active=True).order_by(
        TrendingNews.display_order.asc(),
        TrendingNews.created_at.desc()
    ).all()
    return jsonify([item.to_dict() for item in items])


# =============================================================================
# AUDIT RETENTION / PURGE
# =============================================================================

@admin_bp.route('/audit-logs/purge-old', methods=['POST'])
@admin_required
def purge_old_audit_logs():
    """Delete audit log entries older than AUDIT_RETENTION_DAYS.
    
    Safety: disabled by default. Set AUDIT_PURGE_ENABLED=true to allow.
    """
    from datetime import timedelta

    if not current_app.config.get('AUDIT_PURGE_ENABLED', False):
        flash('Audit purge is disabled. Set AUDIT_PURGE_ENABLED=true to enable.', 'warning')
        return redirect(url_for('admin.audit_logs'))

    retention_days = current_app.config.get('AUDIT_RETENTION_DAYS', 2555)
    cutoff = utc_now() - timedelta(days=retention_days)

    old_count = AuditLog.query.filter(AuditLog.timestamp < cutoff).count()
    if old_count == 0:
        flash('No audit records older than the retention period found.', 'info')
        return redirect(url_for('admin.audit_logs'))

    try:
        AuditLog.query.filter(AuditLog.timestamp < cutoff).delete()
        db.session.commit()

        log_action('AUDIT_LOGS_PURGED', details={
            'retention_days': retention_days,
            'records_deleted': old_count,
            'cutoff_date': cutoff.isoformat()
        })

        flash(f'{old_count} audit records older than {retention_days} days purged.', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Audit purge error: {str(e)}')
        flash('Error purging audit records.', 'danger')

    return redirect(url_for('admin.audit_logs'))
