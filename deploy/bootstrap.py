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
from flask import current_app

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
            "Water Tanker Request",
            "Meter Reading Dispute",
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
            "Footpath Repair",
            "Flyover/Bridge Issue",
        ],
    },
    {
        "department": "Public Health",
        "description": "Public health services, vaccination, food hygiene, and medical concerns",
        "services": [
            "Mosquito Menace",
            "Public Toilet Maintenance",
            "Health Violation",
            "Food Safety Concern",
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
            "Transformer Issue",
            "Illegal Connection Report",
        ],
    },
    {
        "department": "Sanitation & Solid Waste",
        "description": "Waste management, sanitation services, and hygiene",
        "services": [
            "Sewage Blockage",
            "Waste Collection",
            "Drain Cleaning",
            "Public Cleanliness",
            "Garbage Pileup",
            "Hazardous Waste Dumping",
        ],
    },
    {
        "department": "Town Planning & Building",
        "description": "Zoning, building regulations, and public land usage",
        "services": [
            "Unauthorized Construction",
            "Building Plan Approval",
            "Encroachment on Public Land",
            "Zoning Violation",
        ],
    },
    {
        "department": "Revenue & Taxation",
        "description": "Taxation, trade licensing, billing, and certificates",
        "services": [
            "Property Tax Dispute",
            "Trade License Issue",
            "Water/Sewage Bill Complaint",
            "Certificate Request",
        ],
    },
    {
        "department": "Transport & Traffic",
        "description": "Traffic management, public transit, and road safety",
        "services": [
            "Parking Violation",
            "Traffic Signal Malfunction",
            "Public Transport Complaint",
            "Road Safety Hazard",
        ],
    },
    {
        "department": "Environment & Parks",
        "description": "Pollution controls, parks maintenance, and stray animals",
        "services": [
            "Noise Pollution",
            "Air/Water Pollution",
            "Tree Cutting/Felling",
            "Park Maintenance",
            "Stray Animal Menace",
        ],
    },
    {
        "department": "Housing & Urban Development",
        "description": "Municipal housing schemes, slums, and community centers",
        "services": [
            "Public Housing Complaint",
            "Slum Improvement Request",
            "Community Hall Issue",
            "Shelter Home Complaint",
        ],
    },
    {
        "department": "Education & Welfare",
        "description": "Government schools, mid-day meals, and welfare programs",
        "services": [
            "Municipal School Complaint",
            "Scholarship Grievance",
            "Mid-Day Meal Issue",
            "Welfare Scheme Delay",
        ],
    },
    {
        "department": "Governance & Anti-Corruption",
        "description": "Bribery, vigilance, transparency, and official conduct issues",
        "services": [
            "Bribery/Corruption Report",
            "RTI Non-Compliance",
            "Officer Misconduct",
            "Tender Irregularity",
        ],
    },
]


def _production_requires_explicit_staff_password():
    return (
        os.environ.get("FLASK_ENV", "production") == "production"
        and not current_app.config.get("TESTING", False)
        and not current_app.config.get("DEBUG", False)
    )


def _staff_password_from_env(env_name, local_default):
    password = os.environ.get(env_name)
    if password:
        return password
    if _production_requires_explicit_staff_password():
        raise RuntimeError(
            f"{env_name} is required in production before bootstrap can create staff accounts."
        )
    return local_default


def ensure_lookup_data():
    # If "Sanitation" exists but "Sanitation & Solid Waste" does not, rename it
    old_sanitation = Department.query.filter_by(name="Sanitation").first()
    new_sanitation = Department.query.filter_by(name="Sanitation & Solid Waste").first()
    if old_sanitation and not new_sanitation:
        old_sanitation.name = "Sanitation & Solid Waste"
        old_sanitation.description = "Waste management, sanitation services, and hygiene"
        db.session.flush()

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
    email = os.environ.get("DEFAULT_ADMIN_EMAIL", "admin@civikindia.online")

    admin = User.query.filter_by(username=username).first()
    if admin is not None:
        return

    password = _staff_password_from_env("DEFAULT_ADMIN_PASSWORD", "Admin@1234")
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
    email = os.environ.get("DEFAULT_OFFICER_EMAIL", "officer_water@civikindia.online")
    department_name = os.environ.get("DEFAULT_OFFICER_DEPARTMENT", "Water Supply")

    officer = User.query.filter_by(username=username).first()
    if officer is not None:
        return

    password = _staff_password_from_env("DEFAULT_OFFICER_PASSWORD", "Officer@1234")
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


def compress_static_assets(production=False):
    import re
    import gzip
    
    static_dir = Path(__file__).resolve().parent.parent / 'app' / 'static'
    print(f"[boot] Processing static assets in {static_dir} (minify={production})...")
    
    css_comments = re.compile(r'/\*.*?\*/', re.DOTALL)
    css_spaces = re.compile(r'\s*([\{\};:,])\s*')
    css_multi_space = re.compile(r'\s+')
    
    count_comp = 0
    count_min = 0
    
    for root, _, files in os.walk(static_dir):
        for file in files:
            if file.endswith(('.css', '.js', '.json', '.svg')) and not file.endswith('.gz'):
                file_path = Path(root) / file
                gz_path = Path(root) / f"{file}.gz"
                
                # Check if we should process
                if not gz_path.exists() or file_path.stat().st_mtime > gz_path.stat().st_mtime:
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read()
                        
                        # Minify CSS in production
                        if production and file.endswith('.css'):
                            content = css_comments.sub('', content)
                            content = css_multi_space.sub(' ', content)
                            content = css_spaces.sub(r'\1', content)
                            content = content.strip()
                            with open(file_path, 'w', encoding='utf-8') as f_out:
                                f_out.write(content)
                            count_min += 1
                        
                        # Gzip compression
                        with gzip.open(gz_path, 'wb', compresslevel=9) as f_out:
                            f_out.write(content.encode('utf-8'))
                        count_comp += 1
                    except Exception as e:
                        print(f"[boot] Error processing asset {file}: {e}")
                        
    print(f"[boot] Pre-compressed {count_comp} assets, minified {count_min} CSS assets.")


def main():
    env = os.environ.get("FLASK_ENV", "production")
    
    # Pre-compress and minify static assets before starting the app
    compress_static_assets(production=(env == 'production'))

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
