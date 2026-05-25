"""
Civik India Officer Routes
Officer dashboard and complaint management.
Officers can only access complaints in their department.
"""
from flask import Blueprint, render_template, request, flash, redirect, url_for, session, jsonify, current_app
from sqlalchemy.orm import joinedload

from app import db
from app.clock import utc_now
from app.models import User, Complaint, Department, AuditLog
from app.utils import officer_required, log_action, maybe_run_sla_escalations
from app.tasks import send_status_update_notification

officer_bp = Blueprint('officer', __name__)


@officer_bp.route('/dashboard')
@officer_required
def dashboard():
    """Officer dashboard with assigned complaints."""
    maybe_run_sla_escalations()
    user_id = session['user_id']
    department_id = session.get('department_id')

    page = max(request.args.get('page', 1, type=int) or 1, 1)
    per_page = 10
    assigned_query = Complaint.query.filter_by(assigned_to=user_id)
    active_query = assigned_query.filter(Complaint.status.in_(Complaint.ACTIVE_STATUSES))

    assigned_pagination = assigned_query.options(
        joinedload(Complaint.department),
        joinedload(Complaint.service)
    ).order_by(Complaint.submitted_at.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )
    assigned_complaints = assigned_pagination.items
    
    # Get unassigned complaints in officer's department
    unassigned_complaints = Complaint.query.filter(
        Complaint.department_id == department_id,
        Complaint.assigned_to.is_(None),
        Complaint.status.notin_(['Awaiting Review', 'Rejected'])
    ).order_by(Complaint.submitted_at.desc()).limit(10).all()
    
    # Stats for officer: only count active complaints for actionable metrics.
    assigned_count = assigned_query.count()
    active_count = active_query.count()
    overdue_count = active_query.filter(
        Complaint.sla_due_at.isnot(None),
        Complaint.sla_due_at < utc_now()
    ).count()
    sla_compliance = 100
    if active_count > 0:
        sla_compliance = round(((active_count - overdue_count) / active_count) * 100, 1)

    priority_complaints = active_query.options(
        joinedload(Complaint.department)
    ).filter(
        Complaint.priority.in_(['High', 'Urgent'])
    ).order_by(Complaint.submitted_at.desc()).limit(10).all()

    stats = {
        'assigned': assigned_count,
        'active': active_count,
        'pending':       assigned_query.filter_by(status='Pending').count(),
        'under_review':  assigned_query.filter_by(status='Under Review').count(),
        'delayed':       assigned_query.filter_by(status='Delayed').count(),
        'reopened':      assigned_query.filter_by(status='Reopened').count(),
        # Only count HIGH/URGENT priority complaints that still need action
        'high_priority': active_query.filter(Complaint.priority.in_(['High', 'Urgent'])).count(),
        'closed':        assigned_query.filter_by(status='Closed').count(),
        'sla_compliance': sla_compliance,
    }
    
    return render_template('officer/dashboard.html',
                          assigned_complaints=assigned_complaints,
                          complaints_pagination=assigned_pagination,
                          unassigned_complaints=unassigned_complaints,
                          priority_complaints=priority_complaints,
                          stats=stats)


@officer_bp.route('/complaints')
def complaints_redirect():
    """Backward-compatible path kept for older bookmarks / external links."""
    return redirect(url_for('officer.dashboard'))


@officer_bp.route('/complaint/<path:tracking_id>')
@officer_required
def complaint_detail(tracking_id):
    """View complaint details."""
    complaint = Complaint.query.filter_by(tracking_id=tracking_id).first_or_404()
    
    # Check access permission
    user = db.session.get(User, session['user_id'])
    if not user.can_access_complaint(complaint):
        flash('You do not have permission to view this complaint.', 'danger')
        return redirect(url_for('officer.dashboard'))
    
    # Get audit logs for this complaint
    audit_logs = AuditLog.query.filter(
        AuditLog.details.contains(tracking_id)
    ).order_by(AuditLog.timestamp.desc()).limit(20).all()
    
    return render_template('officer/complaint_detail.html',
                          complaint=complaint,
                          audit_logs=audit_logs)


@officer_bp.route('/complaint/<path:tracking_id>/update', methods=['POST'])
@officer_required
def update_status(tracking_id):
    """Update complaint status."""
    complaint = Complaint.query.filter_by(tracking_id=tracking_id).first_or_404()
    
    # Check permission
    user = db.session.get(User, session['user_id'])
    if not user.can_access_complaint(complaint):
        flash('You do not have permission to update this complaint.', 'danger')
        return redirect(url_for('officer.dashboard'))
    
    # Get form data
    new_status = request.form.get('status')
    notes = request.form.get('notes', '').strip()
    
    # Validate status transition
    if not complaint.can_transition_to(new_status):
        flash(f"Cannot transition from '{complaint.status}' to '{new_status}'", 'danger')
        return redirect(url_for('officer.complaint_detail', tracking_id=tracking_id))
    
    # Update status
    try:
        old_status = complaint.status
        success, message = complaint.update_status(new_status, notes)
        
        if success:
            db.session.commit()
            
            # Log the action
            log_action('STATUS_UPDATE', 
                      details={
                          'tracking_id': tracking_id,
                          'old_status': old_status,
                          'new_status': new_status,
                          'notes': notes
                      }, user=user)
            
            # Trigger notification task (async)
            send_status_update_notification(tracking_id, new_status)
            
            flash(f'Status updated to {new_status}.', 'success')
        else:
            flash(message, 'danger')
            
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Status update error: {str(e)}')
        flash('Error updating status. Please try again.', 'danger')
    
    return redirect(url_for('officer.complaint_detail', tracking_id=tracking_id))


@officer_bp.route('/complaint/<path:tracking_id>/assign', methods=['POST'])
@officer_required
def assign_to_me(tracking_id):
    """Self-assign an unassigned complaint."""
    complaint = Complaint.query.filter_by(tracking_id=tracking_id).first_or_404()
    user = db.session.get(User, session['user_id'])
    
    # Check if in same department and unassigned
    if complaint.department_id != user.department_id:
        flash('This complaint is not in your department.', 'danger')
        return redirect(url_for('officer.dashboard'))
    
    if complaint.assigned_to is not None:
        flash('This complaint is already assigned.', 'warning')
        return redirect(url_for('officer.complaint_detail', tracking_id=tracking_id))
    
    # Assign
    try:
        complaint.assigned_to = user.id
        complaint.status = 'Under Review'
        db.session.commit()
        
        log_action('COMPLAINT_ASSIGNED',
                  details={
                      'tracking_id': tracking_id,
                      'assigned_to': user.username
                  }, user=user)
        
        flash('Complaint assigned to you.', 'success')
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Assignment error: {str(e)}')
        flash('Error assigning complaint. Please try again.', 'danger')
    
    return redirect(url_for('officer.complaint_detail', tracking_id=tracking_id))


@officer_bp.route('/complaint/<path:tracking_id>/notes', methods=['POST'])
@officer_required
def add_notes(tracking_id):
    """Add investigation notes to a complaint."""
    complaint = Complaint.query.filter_by(tracking_id=tracking_id).first_or_404()
    user = db.session.get(User, session['user_id'])
    
    if not user.can_access_complaint(complaint):
        flash('You do not have permission to modify this complaint.', 'danger')
        return redirect(url_for('officer.dashboard'))
    
    notes = request.form.get('notes', '').strip()
    
    if not notes:
        flash('Please enter notes.', 'warning')
        return redirect(url_for('officer.complaint_detail', tracking_id=tracking_id))
    
    try:
        # Append to existing notes
        timestamp = utc_now().strftime('%Y-%m-%d %H:%M')
        new_note = f"[{timestamp}] {user.username}: {notes}"
        
        if complaint.resolution_notes:
            complaint.resolution_notes += f"\n\n{new_note}"
        else:
            complaint.resolution_notes = new_note
        
        db.session.commit()
        
        log_action('NOTES_ADDED',
                  details={
                      'tracking_id': tracking_id,
                      'note_preview': notes[:100]
                  }, user=user)
        
        flash('Notes added successfully.', 'success')
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Notes error: {str(e)}')
        flash('Error adding notes. Please try again.', 'danger')
    
    return redirect(url_for('officer.complaint_detail', tracking_id=tracking_id))


@officer_bp.route('/api/my-stats')
@officer_required
def get_my_stats():
    """API endpoint for officer's personal stats."""
    maybe_run_sla_escalations()
    user_id = session['user_id']
    
    assigned = Complaint.query.filter_by(assigned_to=user_id)
    
    stats = {
        'total_assigned': assigned.count(),
        'pending': assigned.filter_by(status='Pending').count(),
        'under_review': assigned.filter_by(status='Under Review').count(),
        'action_taken': assigned.filter_by(status='Action Taken').count(),
        'delayed': assigned.filter_by(status='Delayed').count(),
        'reopened': assigned.filter_by(status='Reopened').count(),
        'high_priority': assigned.filter(
            Complaint.priority.in_(['High', 'Urgent']),
            Complaint.status.in_(Complaint.ACTIVE_STATUSES)
        ).count(),
        'closed': assigned.filter_by(status='Closed').count()
    }
    
    return jsonify(stats)


@officer_bp.route('/complaint/<path:tracking_id>/evidence')
@officer_required
def download_evidence(tracking_id):
    """Download private evidence file for a complaint (department-scoped)."""
    from app.models import EvidenceFile
    from app.utils import evidence_download_response
    
    complaint = Complaint.query.filter_by(tracking_id=tracking_id).first_or_404()
    user = db.session.get(User, session['user_id'])
    
    if not user.can_access_complaint(complaint):
        flash('You do not have permission to access this evidence.', 'danger')
        return redirect(url_for('officer.dashboard'))
    
    if not complaint.evidence_path:
        flash('No evidence file attached to this complaint.', 'warning')
        return redirect(url_for('officer.complaint_detail', tracking_id=tracking_id))
    
    evidence_file = EvidenceFile.query.filter_by(complaint_id=complaint.id).first()
    if not evidence_file:
        flash('Evidence metadata not found.', 'danger')
        return redirect(url_for('officer.complaint_detail', tracking_id=tracking_id))

    try:
        response = evidence_download_response(evidence_file, tracking_id)
        log_action('EVIDENCE_DOWNLOADED', details={
            'tracking_id': tracking_id,
            'filename': evidence_file.original_filename
        }, user=user)
        return response
    except FileNotFoundError:
        flash('Evidence file not found on private storage.', 'danger')
        return redirect(url_for('officer.complaint_detail', tracking_id=tracking_id))
    except Exception as e:
        current_app.logger.error(f'Evidence download error: {str(e)}')
        flash('Error downloading evidence file.', 'danger')
        return redirect(url_for('officer.complaint_detail', tracking_id=tracking_id))
