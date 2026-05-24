#!/usr/bin/env python3
"""
Production-safe bootstrap script.

Creates required database tables and baseline reference data when missing.
Designed for deploy targets that don't maintain migration scripts.
"""
import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import create_app, db
from app.models import Department, Service, User, SLAPolicy
from sqlalchemy.exc import OperationalError


BASELINE_DATA = [
    {
        "department": "Water Supply",
        "description": "Water supply, distribution, and related services",
        "services": [
            "Water Connection",
            "Water Quality Issue",
            "Pipeline Leakage",
            "Billing Complaint",
        ],
    },
    {
        "department": "Roads & Infrastructure",
        "description": "Road maintenance, street lights, and public infrastructure",
        "services": [
            "Pothole Repair",
            "Street Light Issue",
            "Road Construction",
            "Drainage Problem",
        ],
    },
    {
        "department": "Public Health",
        "description": "Public health services, sanitation, and hygiene",
        "services": [
            "Mosquito Menace",
            "Garbage Collection",
            "Public Toilet Maintenance",
            "Health Violation",
        ],
    },
    {
        "department": "Electricity",
        "description": "Electricity supply and power-related services",
        "services": [
            "Power Outage",
            "Voltage Issue",
            "New Connection",
            "Meter Complaint",
        ],
    },
    {
        "department": "Sanitation",
        "description": "Waste management and sanitation services",
        "services": [
            "Sewage Blockage",
            "Waste Collection",
            "Drain Cleaning",
            "Public Cleanliness",
        ],
    },
]


def ensure_lookup_data():
    for item in BASELINE_DATA:
        department = Department.query.filter_by(name=item["department"]).first()
        if not department:
            department = Department(
                name=item["department"],
                description=item["description"]
            )
            db.session.add(department)
            db.session.flush()

        existing_services = {
            service.name: service
            for service in Service.query.filter_by(department_id=department.id).all()
        }

        for service_name in item["services"]:
            if service_name not in existing_services:
                db.session.add(
                    Service(
                        name=service_name,
                        department_id=department.id,
                        description=f"{service_name} services"
                    )
                )


def ensure_sla_policies():
    priorities = ['Normal', 'High', 'Urgent', 'Low']
    departments = Department.query.all()
    for dept in departments:
        for priority in priorities:
            existing = SLAPolicy.query.filter_by(department_id=dept.id, priority_level=priority).first()
            if not existing:
                res_hours = 72
                if priority == 'Low':
                    res_hours = 120
                elif priority == 'Normal':
                    res_hours = 72
                elif priority == 'High':
                    res_hours = 48
                elif priority == 'Urgent':
                    res_hours = 24

                if dept.name == 'Water Supply' and priority == 'High':
                    res_hours = 48
                if dept.name == 'Roads & Infrastructure' and priority == 'Urgent':
                    res_hours = 24

                policy = SLAPolicy(
                    department_id=dept.id,
                    service_id=None,
                    priority_level=priority,
                    resolution_hours=res_hours,
                    first_response_hours=24,
                    escalation_l1_hours=48,
                    escalation_l2_hours=96,
                    escalation_l3_hours=144,
                    is_active=True
                )
                db.session.add(policy)


def ensure_admin():
    username = os.environ.get("DEFAULT_ADMIN_USERNAME", "admin")
    email = os.environ.get("DEFAULT_ADMIN_EMAIL", "admin@civikindia.gov.in")
    password = os.environ.get("DEFAULT_ADMIN_PASSWORD", "Admin@1234")

    admin = User.query.filter_by(username=username).first()
    if admin is not None:
        return

    admin = User(
        username=username,
        email=email,
        role="admin",
        is_active=True
    )
    admin.set_password(password)
    db.session.add(admin)


def ensure_default_officer():
    username = os.environ.get("DEFAULT_OFFICER_USERNAME", "officer_water")
    email = os.environ.get("DEFAULT_OFFICER_EMAIL", "officer_water@civikindia.gov.in")
    password = os.environ.get("DEFAULT_OFFICER_PASSWORD", "Officer@1234")
    department_name = os.environ.get("DEFAULT_OFFICER_DEPARTMENT", "Water Supply")

    officer = User.query.filter_by(username=username).first()
    if officer is not None:
        return

    department = Department.query.filter_by(name=department_name).first()
    officer = User(
        username=username,
        email=email,
        role="officer",
        department_id=department.id if department else None,
        is_active=True
    )
    officer.set_password(password)
    db.session.add(officer)


def main():
    env = os.environ.get("FLASK_ENV", "production")
    app = create_app(env)
    max_retries = int(os.environ.get("BOOTSTRAP_DB_RETRIES", "8"))
    retry_delay = float(os.environ.get("BOOTSTRAP_DB_RETRY_DELAY", "2"))

    with app.app_context():
        for attempt in range(1, max_retries + 1):
            try:
                db.create_all()
                ensure_lookup_data()
                ensure_sla_policies()
                ensure_admin()
                ensure_default_officer()
                db.session.commit()
                break
            except OperationalError:
                if attempt >= max_retries:
                    raise
                print(f"[boot] Database not ready (attempt {attempt}/{max_retries}); retrying in {retry_delay}s")
                time.sleep(retry_delay)
                continue

        print("[boot] DB tables ensured")
        print("[boot] Baseline departments/services ensured")
        print("[boot] Default staff accounts ensured")


if __name__ == "__main__":
    main()
