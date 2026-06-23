"""
Civik India Database Models
All SQLAlchemy models for the Civik India civic awareness and accountability platform.
"""
from datetime import timedelta
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import hashlib
import hmac
import json
from flask import current_app

from app import db
from app.clock import utc_now


class Department(db.Model):
    """
    Government department/ward entity.
    Examples: Water Supply, Roads & Infrastructure, Public Health, etc.
    """
    __tablename__ = 'departments'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    
    # Relationships
    services = db.relationship('Service', backref='department', lazy='dynamic',
                               cascade='all, delete-orphan')
    users = db.relationship('User', backref='department', lazy='dynamic')
    complaints = db.relationship('Complaint', backref='department', lazy='dynamic')
    
    def __repr__(self):
        return f'<Department {self.name}>'
    
    def to_dict(self):
        """Serialize department to dictionary."""
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'service_count': self.services.count(),
            'complaint_count': self.complaints.count()
        }


class Service(db.Model):
    """
    Service offered by a department.
    Citizens select department first, then service via AJAX.
    """
    __tablename__ = 'services'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    sla_days = db.Column(db.Integer, nullable=False, default=7)
    created_at = db.Column(db.DateTime, default=utc_now)
    
    # Relationships
    complaints = db.relationship('Complaint', backref='service', lazy='dynamic')
    
    def __repr__(self):
        return f'<Service {self.name} ({self.department.name if self.department else "No Dept"})>'
    
    def to_dict(self):
        """Serialize service to dictionary."""
        return {
            'id': self.id,
            'name': self.name,
            'department_id': self.department_id,
            'description': self.description,
            'sla_days': self.sla_days,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class SLAPolicy(db.Model):
    """
    SLA policy for a department/service combination.
    """
    __tablename__ = 'sla_policies'
    
    id = db.Column(db.Integer, primary_key=True)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=False, index=True)
    service_id = db.Column(db.Integer, db.ForeignKey('services.id'), nullable=True, index=True)
    priority_level = db.Column(db.String(20), nullable=False, default='Normal')
    resolution_hours = db.Column(db.Integer, nullable=False, default=72)
    first_response_hours = db.Column(db.Integer, nullable=False, default=24)
    escalation_l1_hours = db.Column(db.Integer, nullable=False, default=48)
    escalation_l2_hours = db.Column(db.Integer, nullable=False, default=96)
    escalation_l3_hours = db.Column(db.Integer, nullable=False, default=144)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now)
    
    department = db.relationship('Department')
    service = db.relationship('Service')


class EscalationContact(db.Model):
    """
    Contacts for SLA escalation levels (L1, L2, L3).
    """
    __tablename__ = 'escalation_contacts'
    
    id = db.Column(db.Integer, primary_key=True)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=False, index=True)
    level = db.Column(db.Integer, nullable=False) # 1, 2, 3
    name = db.Column(db.String(120), nullable=False)
    designation = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    whatsapp_number = db.Column(db.String(20), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    
    department = db.relationship('Department')


class NotificationLog(db.Model):
    """
    Audit trail for outbound notifications (SMS, Email).
    """
    __tablename__ = 'notification_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(db.Integer, db.ForeignKey('complaints.id'), nullable=True, index=True)
    channel = db.Column(db.String(20), nullable=False) # email, sms, whatsapp
    recipient_hash = db.Column(db.String(120), nullable=False) # Don't store raw PII if possible
    template_name = db.Column(db.String(120), nullable=True)
    status = db.Column(db.String(20), nullable=False, default='queued') # queued, sent, failed
    sent_at = db.Column(db.DateTime, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    
    complaint = db.relationship('Complaint')


class EvidenceFile(db.Model):
    """
    Metadata for private evidentiary files.
    """
    __tablename__ = 'evidence_files'
    
    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(db.Integer, db.ForeignKey('complaints.id'), nullable=False, index=True)
    uploaded_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)

    # Backward-compatible local fields kept for older rows/templates.
    filename = db.Column(db.String(255), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    safe_filename = db.Column(db.String(255), nullable=True)
    mime_type = db.Column(db.String(120), nullable=False)
    file_size = db.Column(db.Integer, nullable=False)
    byte_size = db.Column(db.Integer, nullable=True)
    file_extension = db.Column(db.String(20), nullable=True)
    encryption_iv = db.Column(db.String(64), nullable=True) # Hex string
    file_hash_sha256 = db.Column(db.String(64), nullable=True)  # SHA-256 of original file
    sha256_hash = db.Column(db.String(64), nullable=True)
    storage_path = db.Column(db.String(512), nullable=False)
    storage_provider = db.Column(db.String(40), nullable=False, default='local')
    storage_bucket = db.Column(db.String(255), nullable=True)
    storage_key = db.Column(db.String(512), nullable=True, index=True)
    drive_backup_file_id = db.Column(db.String(255), nullable=True)
    drive_backup_status = db.Column(db.String(20), nullable=False, default='disabled', index=True)
    encrypted = db.Column(db.Boolean, default=False, nullable=False)
    uploaded_at = db.Column(db.DateTime, default=utc_now)
    created_at = db.Column(db.DateTime, default=utc_now, index=True)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)
    deleted_at = db.Column(db.DateTime, nullable=True, index=True)
    
    complaint = db.relationship('Complaint')
    uploaded_by_user = db.relationship('User', foreign_keys=[uploaded_by_user_id])


class User(db.Model):
    """
    System user - Admin or Officer.
    Citizens do NOT have accounts (anonymous submission).
    """
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), nullable=False, unique=True, index=True)
    email = db.Column(db.String(120), unique=True, nullable=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='officer')  # 'admin' or 'officer'
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    failed_login_attempts = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    last_login = db.Column(db.DateTime, nullable=True)
    
    # 2FA fields
    totp_secret = db.Column(db.String(64), nullable=True)
    two_fa_enabled = db.Column(db.Boolean, default=False, nullable=False)
    backup_codes = db.Column(db.Text, nullable=True)  # Store JSON serialized hashes

    # Password reset fields. The token value stored here is a SHA-256 digest,
    # never the raw token sent to the staff member.
    reset_token = db.Column(db.String(64), nullable=True, index=True)
    reset_token_expires_at = db.Column(db.Integer, nullable=True)
    
    # Relationships
    assigned_complaints = db.relationship('Complaint', backref='assigned_officer',
                                          foreign_keys='Complaint.assigned_to', lazy='dynamic')
    audit_logs = db.relationship('AuditLog', backref='user', lazy='dynamic')
    
    def __repr__(self):
        return f'<User {self.username} ({self.role})>'
    
    def set_password(self, password):
        """Hash and set password using PBKDF2-SHA256."""
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256', salt_length=16)
    
    def check_password(self, password):
        """Verify password against hash."""
        return check_password_hash(self.password_hash, password)
    
    def is_admin(self):
        """Check if user has admin role."""
        return self.role == 'admin'
    
    def is_officer(self):
        """Check if user has officer role."""
        return self.role in ['officer', 'zonal_officer', 'commissioner']

    def is_locked(self):
        """Check if account is temporarily locked due to failed logins."""
        return self.locked_until is not None and self.locked_until > utc_now()

    def register_failed_login(self, threshold=5, lock_minutes=15):
        """Increment failed login attempts and lock account if threshold reached."""
        self.failed_login_attempts = (self.failed_login_attempts or 0) + 1
        if self.failed_login_attempts >= threshold:
            self.locked_until = utc_now() + timedelta(minutes=lock_minutes)

    def reset_login_failures(self):
        """Clear failed login counters on successful login."""
        self.failed_login_attempts = 0
        self.locked_until = None
    
    def can_access_complaint(self, complaint):
        """Check if user can access/modify a specific complaint."""
        if self.is_admin():
            return True
        if self.role == 'commissioner':
            return True
        if self.role in ['officer', 'zonal_officer'] and complaint.department_id == self.department_id:
            if complaint.assigned_to is None or complaint.assigned_to == self.id:
                return True
        return False
    
    def update_last_login(self):
        """Update last login timestamp."""
        self.last_login = utc_now()
        self.reset_login_failures()
        db.session.commit()
    
    def to_dict(self):
        """Serialize user to dictionary (safe - no password)."""
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'role': self.role,
            'department_id': self.department_id,
            'department_name': self.department.name if self.department else None,
            'is_active': self.is_active,
            'failed_login_attempts': self.failed_login_attempts,
            'locked_until': self.locked_until.isoformat() if self.locked_until else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_login': self.last_login.isoformat() if self.last_login else None
        }


class Complaint(db.Model):
    """
    Citizen complaint - completely anonymous.
    No PII stored (no name, phone, email, IP address).
    """
    __tablename__ = 'complaints'
    
    id = db.Column(db.Integer, primary_key=True)
    tracking_id = db.Column(db.String(30), nullable=False, unique=True, index=True)
    service_id = db.Column(db.Integer, db.ForeignKey('services.id'), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=False, index=True)
    description = db.Column(db.Text, nullable=False)
    evidence_path = db.Column(db.String(256), nullable=True)
    status = db.Column(db.String(30), default='Pending', nullable=False, index=True)
    assigned_to = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    escalation_level = db.Column(db.Integer, default=0, nullable=False)
    sla_due_at = db.Column(db.DateTime, nullable=True, index=True)
    delayed_at = db.Column(db.DateTime, nullable=True)
    reopen_count = db.Column(db.Integer, default=0, nullable=False)
    citizen_rating = db.Column(db.Integer, nullable=True)
    citizen_feedback = db.Column(db.Text, nullable=True)
    feedback_submitted_at = db.Column(db.DateTime, nullable=True)
    priority = db.Column(db.String(20), default='Normal', nullable=False, index=True)
    ai_category = db.Column(db.String(80), nullable=True)
    ai_sentiment = db.Column(db.String(20), default='neutral', nullable=False)
    ai_urgent = db.Column(db.Boolean, default=False, nullable=False)
    state = db.Column(db.String(80), nullable=True, index=True)
    district = db.Column(db.String(120), nullable=True, index=True)
    city = db.Column(db.String(120), nullable=True, index=True)
    location_lat = db.Column(db.Float, nullable=True)
    location_lng = db.Column(db.Float, nullable=True)
    
    # Module 6 Form Enhancements
    complaint_category = db.Column(db.String(80), nullable=True)
    ward_locality = db.Column(db.String(120), nullable=True)
    incident_date = db.Column(db.Date, nullable=True)
    officer_name_alleged = db.Column(db.String(120), nullable=True)
    witness_available = db.Column(db.Boolean, nullable=True)
    contact_preference = db.Column(db.String(50), nullable=True)
    voluntary_id = db.Column(db.String(256), nullable=True) # encrypted optionally
    
    submitted_at = db.Column(db.DateTime, default=utc_now, index=True)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)
    resolved_at = db.Column(db.DateTime, nullable=True)
    resolution_notes = db.Column(db.Text, nullable=True)
    
    rejection_reason = db.Column(db.Text, nullable=True)
    admin_notes = db.Column(db.Text, nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    reviewed_by = db.relationship('User', foreign_keys=[reviewed_by_id])
    
    evidence_files = db.relationship('EvidenceFile',
                                      foreign_keys='EvidenceFile.complaint_id',
                                      primaryjoin='Complaint.id == EvidenceFile.complaint_id',
                                      overlaps='complaint')
    
    # Valid status transitions
    VALID_STATUSES = ['Awaiting Review', 'Pending', 'Under Review', 'Action Taken', 'Delayed', 'Reopened', 'Closed', 'Rejected']
    ACTIVE_STATUSES = ['Pending', 'Under Review', 'Action Taken', 'Delayed', 'Reopened'] # Awaiting Review & Rejected are NOT active
    STATUS_FLOW = {
        'Awaiting Review': ['Pending', 'Rejected'],
        'Rejected': [],
        'Pending': ['Under Review', 'Delayed', 'Closed'],
        'Under Review': ['Action Taken', 'Delayed', 'Closed'],
        'Action Taken': ['Delayed', 'Closed'],
        'Delayed': ['Under Review', 'Action Taken', 'Closed'],
        'Reopened': ['Under Review', 'Action Taken', 'Delayed', 'Closed'],
        'Closed': ['Reopened']
    }
    
    def __repr__(self):
        return f'<Complaint {self.tracking_id} ({self.status})>'
    
    def can_transition_to(self, new_status):
        """Check if status transition is valid."""
        return new_status in self.STATUS_FLOW.get(self.status, [])

    def initialize_sla_due(self):
        """Initialize SLA due date from SLAPolicy."""
        if self.sla_due_at is None and self.submitted_at and self.service:
            # Find SLA policy for department/service, fallback to department only
            policy = SLAPolicy.query.filter_by(
                department_id=self.department_id, 
                service_id=self.service_id, 
                priority_level=self.priority,
                is_active=True
            ).first()
            if not policy:
                policy = SLAPolicy.query.filter_by(
                    department_id=self.department_id,
                    service_id=None,
                    priority_level=self.priority,
                    is_active=True
                ).first()
                
            if policy:
                self.sla_due_at = self.submitted_at + timedelta(hours=policy.resolution_hours)
            else:
                self.sla_due_at = self.submitted_at + timedelta(days=self.service.sla_days or 7)
        return self.sla_due_at

    def is_overdue(self):
        """Check if complaint has exceeded SLA and is still open."""
        due = self.initialize_sla_due()
        if not due:
            return False
        return self.status in self.ACTIVE_STATUSES and due < utc_now()

    def get_sla_status(self):
        """
        Returns SLA status classification:
        - 'overdue' (already overdue/breached and active)
        - 'warning' (active, not overdue, < 24 hours remaining)
        - 'safe' (active, not overdue, >= 24 hours remaining)
        - 'inactive' (not in active status, e.g. Closed, Rejected, Awaiting Review)
        - 'unknown' (no due date set)
        """
        if self.status not in self.ACTIVE_STATUSES:
            return 'inactive'
        due = self.initialize_sla_due()
        if not due:
            return 'unknown'
        now = utc_now()
        if due < now:
            return 'overdue'
        
        # Calculate time remaining
        remaining = due - now
        if remaining.total_seconds() <= 86400: # 24 hours
            return 'warning'
        return 'safe'

    def get_sla_time_remaining(self):
        """
        Returns a human-readable remaining time or overdue duration.
        """
        due = self.initialize_sla_due()
        if not due:
            return 'No SLA set'
        if self.status not in self.ACTIVE_STATUSES:
            return f"Closed" if self.status == 'Closed' else f"Inactive ({self.status})"
        
        now = utc_now()
        if due < now:
            diff = now - due
            hours = int(diff.total_seconds() / 3600)
            if hours < 24:
                return f"Overdue by {hours}h"
            return f"Overdue by {diff.days}d"
        else:
            diff = due - now
            hours = int(diff.total_seconds() / 3600)
            if hours < 24:
                return f"{hours}h left"
            return f"{diff.days}d left"

    def get_sla_progress_percentage(self):
        """
        Returns the percentage of SLA time elapsed:
        - 0 if no SLA due date or submission date
        - 100 if resolved, overdue, or elapsed exceeds total SLA time
        - otherwise, the exact percentage capped between 0 and 100
        """
        due = self.initialize_sla_due()
        if not due or not self.submitted_at:
            return 0
        
        # If already closed/resolved, show resolution percentage or cap at 100 if breached
        if self.status not in self.ACTIVE_STATUSES and self.resolved_at:
            total_duration = due - self.submitted_at
            resolution_duration = self.resolved_at - self.submitted_at
            if total_duration.total_seconds() <= 0:
                return 100
            pct = (resolution_duration.total_seconds() / total_duration.total_seconds()) * 100
            return min(100, max(0, round(pct)))
            
        now = utc_now()
        if due <= now:
            return 100
        
        total_duration = due - self.submitted_at
        elapsed = now - self.submitted_at
        if total_duration.total_seconds() <= 0:
            return 100
        
        pct = (elapsed.total_seconds() / total_duration.total_seconds()) * 100
        return min(100, max(0, round(pct)))

    def get_escalation_role(self):
        """Resolve hierarchy role from escalation level."""
        if self.escalation_level <= 0:
            return 'officer'
        if self.escalation_level == 1:
            return 'zonal_officer'
        return 'commissioner'

    def assign_by_escalation_hierarchy(self):
        """Assign complaint based on escalation hierarchy."""
        target_role = self.get_escalation_role()
        query = User.query.filter_by(role=target_role, is_active=True)
        if target_role != 'commissioner':
            query = query.filter_by(department_id=self.department_id)

        candidates = query.all()
        if not candidates and target_role != 'commissioner':
            candidates = User.query.filter_by(role='commissioner', is_active=True).all()

        if not candidates:
            return None

        candidate_ids = [c.id for c in candidates]
        from sqlalchemy import func
        active_counts = dict(
            db.session.query(
                Complaint.assigned_to,
                func.count(Complaint.id)
            ).filter(
                Complaint.assigned_to.in_(candidate_ids),
                Complaint.status != 'Closed'
            ).group_by(Complaint.assigned_to).all()
        )

        assignee = min(candidates, key=lambda user: active_counts.get(user.id, 0))
        self.assigned_to = assignee.id
        return assignee
    
    def update_status(self, new_status, notes=None, user=None):
        """
        Update complaint status with validation.
        Returns tuple (success: bool, message: str)
        """
        if not self.can_transition_to(new_status):
            return False, f"Cannot transition from '{self.status}' to '{new_status}'"
        
        old_status = self.status
        self.status = new_status
        self.updated_at = utc_now()
        
        if new_status == 'Closed':
            self.resolved_at = utc_now()
        elif new_status in self.ACTIVE_STATUSES:
            self.resolved_at = None

        if new_status == 'Reopened':
            self.reopen_count = (self.reopen_count or 0) + 1
            self.escalation_level = min((self.escalation_level or 0) + 1, 2)
            self.initialize_sla_due()
        
        if notes:
            self.resolution_notes = notes
        
        # Record status history entry
        actor = user.username if user else 'officer'
        history_entry = ComplaintStatusHistory(
            complaint_id=self.id,
            from_status=old_status,
            to_status=new_status,
            notes=notes,
            changed_by=actor,
            changed_at=self.updated_at
        )
        db.session.add(history_entry)
        
        return True, f"Status updated from '{old_status}' to '{new_status}'"
    
    def get_resolution_time(self):
        """Calculate resolution time in hours."""
        if self.resolved_at and self.submitted_at:
            delta = self.resolved_at - self.submitted_at
            return round(delta.total_seconds() / 3600, 2)
        return None

    def resolution_days(self):
        """Calculate resolution time in days (used in templates)."""
        if self.resolved_at and self.submitted_at:
            return (self.resolved_at - self.submitted_at).days
        return None

    def submit_citizen_feedback(self, rating, feedback=''):
        """Store anonymous post-closure rating and feedback."""
        if self.status != 'Closed':
            return False, 'Feedback can be submitted only after closure.'
        if rating < 1 or rating > 5:
            return False, 'Rating must be between 1 and 5.'

        self.citizen_rating = rating
        self.citizen_feedback = feedback.strip() if feedback else None
        self.feedback_submitted_at = utc_now()
        return True, 'Feedback submitted successfully.'

    def reopen(self, reason):
        """Reopen a closed complaint for further review."""
        reason = (reason or '').strip()
        if self.status != 'Closed':
            return False, 'Only closed complaints can be reopened.'
        if len(reason) < 10:
            return False, 'Please provide at least 10 characters explaining why to reopen.'

        old_status = self.status
        self.status = 'Reopened'
        self.updated_at = utc_now()
        self.resolved_at = None
        self.reopen_count = (self.reopen_count or 0) + 1
        self.escalation_level = min((self.escalation_level or 0) + 1, 2)
        self.initialize_sla_due()
        self.sla_due_at = utc_now() + timedelta(days=self.service.sla_days or 7)

        note = f"[Citizen Reopen] {reason}"
        if self.resolution_notes:
            self.resolution_notes += f"\n\n{note}"
        else:
            self.resolution_notes = note

        self.assign_by_escalation_hierarchy()

        # Record status history entry
        history_entry = ComplaintStatusHistory(
            complaint_id=self.id,
            from_status=old_status,
            to_status='Reopened',
            notes=note,
            changed_by='citizen',
            changed_at=self.updated_at
        )
        db.session.add(history_entry)

        return True, 'Complaint reopened successfully.'
    
    def to_dict(self, include_details=False):
        """Serialize complaint to dictionary."""
        data = {
            'id': self.id,
            'tracking_id': self.tracking_id,
            'service_id': self.service_id,
            'service_name': self.service.name if self.service else None,
            'department_id': self.department_id,
            'department_name': self.department.name if self.department else None,
            'status': self.status,
            'priority': self.priority,
            'ai_category': self.ai_category,
            'ai_sentiment': self.ai_sentiment,
            'ai_urgent': self.ai_urgent,
            'state': self.state,
            'district': self.district,
            'city': self.city,
            'escalation_level': self.escalation_level,
            'reopen_count': self.reopen_count,
            'sla_due_at': self.sla_due_at.isoformat() if self.sla_due_at else None,
            'is_delayed': self.status == 'Delayed',
            'citizen_rating': self.citizen_rating,
            'has_feedback': self.citizen_feedback is not None,
            'submitted_at': self.submitted_at.isoformat() if self.submitted_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'resolved_at': self.resolved_at.isoformat() if self.resolved_at else None
        }
        
        if include_details:
            data.update({
                'description': self.description,
                'has_evidence': self.evidence_path is not None,
                'assigned_to': self.assigned_to,
                'officer_name': self.assigned_officer.username if self.assigned_officer else None,
                'resolution_notes': self.resolution_notes,
                'resolution_hours': self.get_resolution_time(),
                'citizen_feedback': self.citizen_feedback,
                'location_lat': self.location_lat,
                'location_lng': self.location_lng,
                'rejection_reason': self.rejection_reason,
                'admin_notes': self.admin_notes,
                'reviewed_at': self.reviewed_at.isoformat() if self.reviewed_at else None,
                'reviewed_by_id': self.reviewed_by_id
            })
        
        return data

    @staticmethod
    def apply_sla_escalations():
        """
        Apply SLA checks and auto-escalate overdue active complaints.
        Returns number of complaints auto-escalated.
        """
        now = utc_now()
        active_query = Complaint.query.filter(Complaint.status.in_(Complaint.ACTIVE_STATUSES))
        escalated = []
        initialized_due_dates = False

        for complaint in active_query.yield_per(100):
            before_due = complaint.sla_due_at
            complaint.initialize_sla_due()
            if before_due is None and complaint.sla_due_at is not None:
                initialized_due_dates = True
            if not complaint.sla_due_at or complaint.sla_due_at >= now:
                continue

            changed = False
            previous_status = complaint.status
            previous_level = complaint.escalation_level or 0

            if complaint.status != 'Delayed':
                complaint.status = 'Delayed'
                complaint.delayed_at = now
                changed = True

            if complaint.escalation_level < 2:
                complaint.escalation_level += 1
                changed = True

            assignee = complaint.assign_by_escalation_hierarchy()
            complaint.updated_at = now

            if changed:
                escalated.append((complaint, previous_status, previous_level, assignee))

        if not escalated and not initialized_due_dates:
            return 0

        db.session.commit()

        if not escalated:
            return 0

        for complaint, previous_status, previous_level, assignee in escalated:
            details = {
                'tracking_id': complaint.tracking_id,
                'previous_status': previous_status,
                'new_status': complaint.status,
                'previous_level': previous_level,
                'new_level': complaint.escalation_level,
                'assigned_to': assignee.username if assignee else None
            }
            AuditLog.create_entry(
                username='system',
                role='system',
                action='SLA_ESCALATED',
                details=json.dumps(details)
            )

        return len(escalated)
    
    @staticmethod
    def get_stats(public=False):
        """Get aggregate statistics for dashboard using efficient SQL counts."""
        from sqlalchemy import case, func

        public_statuses = ['Pending', 'Under Review', 'Action Taken', 'Delayed', 'Reopened', 'Closed']
        all_statuses = ['Pending', 'Under Review', 'Action Taken', 'Delayed', 'Reopened', 'Closed', 'Awaiting Review', 'Rejected']
        filter_statuses = public_statuses if public else all_statuses

        query_exprs = [
            func.count(Complaint.id).label('total'),
            func.count(case((Complaint.status == 'Pending', 1))).label('pending'),
            func.count(case((Complaint.status == 'Under Review', 1))).label('under_review'),
            func.count(case((Complaint.status == 'Action Taken', 1))).label('action_taken'),
            func.count(case((Complaint.status == 'Delayed', 1))).label('delayed'),
            func.count(case((Complaint.status == 'Reopened', 1))).label('reopened'),
            func.count(case((Complaint.status == 'Closed', 1))).label('closed'),
            func.count(case((Complaint.status == 'Awaiting Review', 1))).label('awaiting_review'),
            func.count(case((Complaint.status == 'Rejected', 1))).label('rejected'),
            func.count(case((
                db.and_(
                    Complaint.priority.in_(['High', 'Urgent']),
                    Complaint.status.in_(Complaint.ACTIVE_STATUSES)
                ), 1
            ))).label('high_priority'),
            func.count(case((
                db.and_(
                    Complaint.status == 'Closed',
                    Complaint.resolved_at.isnot(None),
                    Complaint.sla_due_at.isnot(None),
                    Complaint.resolved_at <= Complaint.sla_due_at
                ), 1
            ))).label('within_sla')
        ]

        row = db.session.query(*query_exprs).filter(Complaint.status.in_(filter_statuses)).one()

        total = row.total or 0
        closed = row.closed or 0
        awaiting_review = row.awaiting_review or 0 if not public else 0
        rejected = row.rejected or 0 if not public else 0
        high_priority = row.high_priority or 0
        within_sla = row.within_sla or 0

        sla_compliance = round((within_sla / closed * 100), 2) if closed > 0 else 0
        
        return {
            'total': total,
            'pending': row.pending or 0,
            'under_review': row.under_review or 0,
            'action_taken': row.action_taken or 0,
            'delayed': row.delayed or 0,
            'reopened': row.reopened or 0,
            'closed': closed,
            'awaiting_review': awaiting_review,
            'rejected': rejected,
            'high_priority': high_priority,
            'sla_compliance': sla_compliance,
            'resolution_rate': round((closed / total * 100), 2) if total > 0 else 0
        }


class AuditLog(db.Model):
    """
    Immutable audit log with hash chaining for tamper evidence.
    Inspired by blockchain - each entry contains hash of previous entry.
    NO UPDATE OR DELETE routes exist for this table.
    """
    __tablename__ = 'audit_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    username = db.Column(db.String(64), nullable=False, index=True)
    role = db.Column(db.String(30), nullable=False)
    action = db.Column(db.String(120), nullable=False, index=True)
    details = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)  # IPv6 compatible
    timestamp = db.Column(db.DateTime, default=utc_now, index=True)
    previous_hash = db.Column(db.String(64), nullable=True)
    row_hash = db.Column(db.String(64), nullable=False, unique=True)
    
    def __repr__(self):
        return f'<AuditLog {self.username} {self.action} at {self.timestamp}>'
    
    def calculate_hash(self):
        """
        Calculate SHA-256 hash of this log entry.
        Includes previous hash for chain integrity.
        """
        data = {
            'user_id': self.user_id,
            'username': self.username,
            'role': self.role,
            'action': self.action,
            'details': self.details or '',
            'ip_address': self.ip_address or '',
            'timestamp': self.timestamp.isoformat() if self.timestamp else '',
            'previous_hash': self.previous_hash or ''
        }
        
        # Create deterministic string representation
        hash_string = json.dumps(data, sort_keys=True, separators=(',', ':'))
        
        secret = current_app.config.get('AUDIT_HMAC_SECRET', 'fallback_secret').encode('utf-8')
        return hmac.new(secret, hash_string.encode('utf-8'), hashlib.sha256).hexdigest()
    
    def verify_integrity(self):
        """Verify that stored hash matches calculated hash."""
        return self.row_hash == self.calculate_hash()

    @staticmethod
    def rebuild_chain(dry_run=False):
        """Rebuild the hash chain for all existing audit logs."""
        logs = AuditLog.query.order_by(AuditLog.id.asc()).all()
        if not logs:
            return {'total': 0, 'repaired': 0, 'dry_run': bool(dry_run)}

        repaired = 0
        previous_hash = None

        for log in logs:
            expected_previous = previous_hash

            # Apply canonical previous hash before calculating row hash.
            log.previous_hash = expected_previous
            expected_hash = log.calculate_hash()

            if log.previous_hash != expected_previous or log.row_hash != expected_hash:
                repaired += 1
                if not dry_run:
                    log.previous_hash = expected_previous
                    log.row_hash = expected_hash

            previous_hash = expected_hash

        if dry_run or repaired == 0:
            if not dry_run:
                db.session.rollback()
            return {
                'total': len(logs),
                'repaired': repaired,
                'dry_run': bool(dry_run)
            }

        db.session.commit()
        return {
            'total': len(logs),
            'repaired': repaired,
            'dry_run': bool(dry_run)
        }
    
    @staticmethod
    def get_previous_hash():
        """Get hash of most recent audit log entry."""
        last_log = AuditLog.query.order_by(AuditLog.id.desc()).first()
        return last_log.row_hash if last_log else None
    
    @staticmethod
    def create_entry(username, role, action, details=None, user_id=None, ip_address=None):
        """
        Factory method to create a new audit log entry.
        Automatically handles hash chaining.
        """
        timestamp = utc_now()
        entry = AuditLog(
            user_id=user_id,
            username=username,
            role=role,
            action=action,
            details=details,
            ip_address=ip_address,
            timestamp=timestamp,
            previous_hash=AuditLog.get_previous_hash()
        )
        
        # Hash is deterministic from entry content + previous_hash.
        entry.row_hash = entry.calculate_hash()
        
        db.session.add(entry)
        db.session.commit()
        
        return entry
    
    def to_dict(self):
        """Serialize audit log to dictionary."""
        return {
            'id': self.id,
            'username': self.username,
            'role': self.role,
            'action': self.action,
            'details': self.details,
            'ip_address': self.ip_address,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'row_hash': self.row_hash[:16] + '...'  # Truncated for display
        }


class TrendingNews(db.Model):
    """
    Admin-controlled trending news ticker items displayed on public pages.
    Items scroll horizontally as a news ticker/marquee on the homepage
    and public dashboard.
    """
    __tablename__ = 'trending_news'

    id = db.Column(db.Integer, primary_key=True)
    headline = db.Column(db.String(300), nullable=False)
    link_url = db.Column(db.String(512), nullable=True)   # optional clickable link
    badge_label = db.Column(db.String(40), nullable=True)  # e.g. "BREAKING", "UPDATE"
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    display_order = db.Column(db.Integer, default=0, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    created_by = db.relationship('User', foreign_keys=[created_by_id])

    def __repr__(self):
        return f'<TrendingNews {self.id}: {self.headline[:40]}>'

    def to_dict(self):
        return {
            'id': self.id,
            'headline': self.headline,
            'link_url': self.link_url,
            'badge_label': self.badge_label,
            'is_active': self.is_active,
            'display_order': self.display_order,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Announcement(db.Model):
    """
    Admin-created public notice board items.
    Shown on the public /notices page and as a homepage widget.
    Distinct from TrendingNews ticker — these are full-text notices with categories and expiry.
    """
    __tablename__ = 'announcements'

    CATEGORIES = ['General', 'Maintenance', 'Alert', 'Policy Update', 'Event', 'Helpline']
    PRIORITIES = ['info', 'warning', 'alert', 'critical']

    id            = db.Column(db.Integer, primary_key=True)
    title         = db.Column(db.String(200), nullable=False)
    body          = db.Column(db.Text, nullable=False)
    category      = db.Column(db.String(50), nullable=False, default='General')
    priority      = db.Column(db.String(20), nullable=False, default='info')   # info | warning | alert | critical
    is_active     = db.Column(db.Boolean, default=True, nullable=False)
    is_pinned     = db.Column(db.Boolean, default=False, nullable=False)       # pinned items float to top
    show_on_home  = db.Column(db.Boolean, default=False, nullable=False)       # appear in homepage widget
    expires_at    = db.Column(db.DateTime, nullable=True)                      # NULL = never expires
    published_at  = db.Column(db.DateTime, nullable=True, default=utc_now)    # scheduled publish date
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at    = db.Column(db.DateTime, default=utc_now)
    updated_at    = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    created_by = db.relationship('User', foreign_keys=[created_by_id])

    def __repr__(self):
        return f'<Announcement {self.id}: {self.title[:40]}>'

    @property
    def is_visible(self):
        """True if active, published, and not expired."""
        now = utc_now()
        if not self.is_active:
            return False
        if self.published_at and self.published_at > now:
            return False   # scheduled for future
        if self.expires_at and self.expires_at < now:
            return False   # expired
        return True

    @property
    def is_expired(self):
        """True if expiry date is set and has passed."""
        if not self.expires_at:
            return False
        return self.expires_at < utc_now()

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'body': self.body,
            'category': self.category,
            'priority': self.priority,
            'is_active': self.is_active,
            'is_pinned': self.is_pinned,
            'show_on_home': self.show_on_home,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'published_at': self.published_at.isoformat() if self.published_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class ComplaintStatusHistory(db.Model):
    """
    Immutable log of every status transition for a complaint.
    Created automatically by Complaint.update_status() and Complaint.reopen().
    Enables citizens to see a real timeline on the /track page.
    """
    __tablename__ = 'complaint_status_history'

    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(db.Integer, db.ForeignKey('complaints.id'), nullable=False, index=True)
    from_status = db.Column(db.String(30), nullable=True)   # NULL for initial 'Submitted' entry
    to_status = db.Column(db.String(30), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    changed_by = db.Column(db.String(64), nullable=False, default='system')
    changed_at = db.Column(db.DateTime, nullable=False, default=utc_now, index=True)

    complaint = db.relationship('Complaint', backref=db.backref(
        'status_history', lazy='dynamic',
        order_by='ComplaintStatusHistory.changed_at'
    ))

    def __repr__(self):
        return f'<StatusHistory {self.complaint_id}: {self.from_status} -> {self.to_status}>'

    def to_dict(self):
        return {
            'id': self.id,
            'from_status': self.from_status,
            'to_status': self.to_status,
            'notes': self.notes,
            'changed_by': self.changed_by,
            'changed_at': self.changed_at.isoformat() if self.changed_at else None,
        }
