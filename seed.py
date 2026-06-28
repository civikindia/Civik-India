#!/usr/bin/env python3
"""
CivikIndia Database Seeder
Creates demo data for development and testing.
"""
import os
import sys
import argparse
from datetime import timedelta
import random
import secrets

# Add app to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db
from app.clock import utc_now
from app.models import Department, Service, User, Complaint, AuditLog, SLAPolicy, EscalationContact
from app.utils import generate_tracking_id


def _demo_password(env_name):
    """Use an explicit demo password when provided, otherwise create one per seed run."""
    return os.environ.get(env_name) or secrets.token_urlsafe(18)


LOCATION_CATALOG = [
    {'state': 'Maharashtra', 'district': 'Mumbai Suburban', 'city': 'Mumbai', 'lat': 19.0760, 'lng': 72.8777},
    {'state': 'Maharashtra', 'district': 'Pune', 'city': 'Pune', 'lat': 18.5204, 'lng': 73.8567},
    {'state': 'Maharashtra', 'district': 'Nagpur', 'city': 'Nagpur', 'lat': 21.1458, 'lng': 79.0882},
    {'state': 'Maharashtra', 'district': 'Nashik', 'city': 'Nashik', 'lat': 19.9975, 'lng': 73.7898},
    {'state': 'Karnataka', 'district': 'Bengaluru Urban', 'city': 'Bengaluru', 'lat': 12.9716, 'lng': 77.5946},
    {'state': 'Karnataka', 'district': 'Mysuru', 'city': 'Mysuru', 'lat': 12.2958, 'lng': 76.6394},
    {'state': 'Karnataka', 'district': 'Dakshina Kannada', 'city': 'Mangaluru', 'lat': 12.9141, 'lng': 74.8560},
    {'state': 'Karnataka', 'district': 'Dharwad', 'city': 'Hubballi', 'lat': 15.3647, 'lng': 75.1240},
    {'state': 'Tamil Nadu', 'district': 'Chennai', 'city': 'Chennai', 'lat': 13.0827, 'lng': 80.2707},
    {'state': 'Telangana', 'district': 'Hyderabad', 'city': 'Hyderabad', 'lat': 17.3850, 'lng': 78.4867},
    {'state': 'Delhi', 'district': 'New Delhi', 'city': 'New Delhi', 'lat': 28.6139, 'lng': 77.2090},
    {'state': 'Gujarat', 'district': 'Ahmedabad', 'city': 'Ahmedabad', 'lat': 23.0225, 'lng': 72.5714},
    {'state': 'West Bengal', 'district': 'Kolkata', 'city': 'Kolkata', 'lat': 22.5726, 'lng': 88.3639},
    {'state': 'Uttar Pradesh', 'district': 'Lucknow', 'city': 'Lucknow', 'lat': 26.8467, 'lng': 80.9462},
    {'state': 'Rajasthan', 'district': 'Jaipur', 'city': 'Jaipur', 'lat': 26.9124, 'lng': 75.7873},
    {'state': 'Madhya Pradesh', 'district': 'Indore', 'city': 'Indore', 'lat': 22.7196, 'lng': 75.8577},
    {'state': 'Kerala', 'district': 'Ernakulam', 'city': 'Kochi', 'lat': 9.9312, 'lng': 76.2673},
    {'state': 'Bihar', 'district': 'Patna', 'city': 'Patna', 'lat': 25.5941, 'lng': 85.1376},
    {'state': 'Odisha', 'district': 'Khordha', 'city': 'Bhubaneswar', 'lat': 20.2961, 'lng': 85.8245},
    {'state': 'Punjab', 'district': 'Ludhiana', 'city': 'Ludhiana', 'lat': 30.9010, 'lng': 75.8573}
]


def random_nearby_coords(base_lat, base_lng):
    """Return slightly jittered coordinates around a city center."""
    lat = round(base_lat + random.uniform(-0.035, 0.035), 6)
    lng = round(base_lng + random.uniform(-0.035, 0.035), 6)
    return lat, lng
def seed_departments():
    """Create default departments."""
    departments_data = [
        {
            'name': 'Water Supply',
            'description': 'Water supply, distribution, and related services'
        },
        {
            'name': 'Roads & Infrastructure',
            'description': 'Road maintenance, street lights, and public infrastructure'
        },
        {
            'name': 'Public Health',
            'description': 'Public health services, vaccination, food hygiene, and medical concerns'
        },
        {
            'name': 'Electricity',
            'description': 'Electricity supply and power-related services'
        },
        {
            'name': 'Sanitation & Solid Waste',
            'description': 'Waste management, sanitation services, and hygiene'
        },
        {
            'name': 'Town Planning & Building',
            'description': 'Zoning, building regulations, and public land usage'
        },
        {
            'name': 'Revenue & Taxation',
            'description': 'Taxation, trade licensing, billing, and certificates'
        },
        {
            'name': 'Transport & Traffic',
            'description': 'Traffic management, public transit, and road safety'
        },
        {
            'name': 'Environment & Parks',
            'description': 'Pollution controls, parks maintenance, and stray animals'
        },
        {
            'name': 'Housing & Urban Development',
            'description': 'Municipal housing schemes, slums, and community centers'
        },
        {
            'name': 'Education & Welfare',
            'description': 'Government schools, mid-day meals, and welfare programs'
        },
        {
            'name': 'Governance & Anti-Corruption',
            'description': 'Bribery, vigilance, transparency, and official conduct issues'
        }
    ]
    
    # Check if old "Sanitation" exists and rename it to keep IDs intact
    old_sanitation = Department.query.filter_by(name="Sanitation").first()
    new_sanitation = Department.query.filter_by(name="Sanitation & Solid Waste").first()
    if old_sanitation and not new_sanitation:
        old_sanitation.name = "Sanitation & Solid Waste"
        old_sanitation.description = "Waste management, sanitation services, and hygiene"
        db.session.flush()

    departments = []
    for data in departments_data:
        dept = Department.query.filter_by(name=data['name']).first()
        if not dept:
            dept = Department(**data)
            db.session.add(dept)
            print(f"  Created department: {data['name']}")
        departments.append(dept)
    
    db.session.commit()
    return departments


def seed_sla_policies(departments):
    """Seed SLA policies (48 combinations of 12 departments x 4 priorities)."""
    priorities = ['Normal', 'High', 'Urgent', 'Low']
    policies_seeded = 0
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
                policies_seeded += 1
    db.session.commit()
    print(f"  Seeded {policies_seeded} SLA policies.")


def seed_escalation_contacts(departments):
    """Seed escalation contacts (L1, L2, L3 for each department)."""
    contacts_seeded = 0
    for dept in departments:
        for level in [1, 2, 3]:
            name_map = {
                1: f"L1 Escalation Officer - {dept.name}",
                2: f"L2 Zonal Head - {dept.name}",
                3: f"L3 Director - {dept.name}"
            }
            designation_map = {
                1: f"Assistant Engineer ({dept.name})",
                2: f"Executive Engineer ({dept.name})",
                3: f"Superintending Engineer ({dept.name})"
            }
            
            clean_name = dept.name.lower().replace(' ', '').replace('&', '')
            existing = EscalationContact.query.filter_by(department_id=dept.id, level=level).first()
            if not existing:
                contact = EscalationContact(
                    department_id=dept.id,
                    level=level,
                    name=name_map[level],
                    designation=designation_map[level],
                    email=f"escalation.l{level}.{clean_name}@civikindia.gov.in",
                    phone=f"98765432{dept.id % 10}{level}",
                    whatsapp_number=f"98765432{dept.id % 10}{level}",
                    is_active=True
                )
                db.session.add(contact)
                contacts_seeded += 1
    db.session.commit()
    print(f"  Seeded {contacts_seeded} escalation contacts.")


def seed_services(departments):
    """Create services for each department."""
    services_data = {
        'Water Supply': [
            'Water Connection',
            'Water Quality Issue',
            'Pipeline Leakage',
            'Water Tanker Request',
            'Meter Reading Dispute',
        ],
        'Roads & Infrastructure': [
            'Pothole Repair',
            'Street Light Issue',
            'Road Construction',
            'Drainage Problem',
            'Footpath Repair',
            'Flyover/Bridge Issue',
        ],
        'Public Health': [
            'Mosquito Menace',
            'Public Toilet Maintenance',
            'Health Violation',
            'Food Safety Concern',
        ],
        'Electricity': [
            'Power Outage',
            'Voltage Issue',
            'New Connection',
            'Meter Complaint',
            'Transformer Issue',
            'Illegal Connection Report',
        ],
        'Sanitation & Solid Waste': [
            'Sewage Blockage',
            'Waste Collection',
            'Drain Cleaning',
            'Public Cleanliness',
            'Garbage Pileup',
            'Hazardous Waste Dumping',
        ],
        'Town Planning & Building': [
            'Unauthorized Construction',
            'Building Plan Approval',
            'Encroachment on Public Land',
            'Zoning Violation',
        ],
        'Revenue & Taxation': [
            'Property Tax Dispute',
            'Trade License Issue',
            'Water/Sewage Bill Complaint',
            'Certificate Request',
        ],
        'Transport & Traffic': [
            'Parking Violation',
            'Traffic Signal Malfunction',
            'Public Transport Complaint',
            'Road Safety Hazard',
        ],
        'Environment & Parks': [
            'Noise Pollution',
            'Air/Water Pollution',
            'Tree Cutting/Felling',
            'Park Maintenance',
            'Stray Animal Menace',
        ],
        'Housing & Urban Development': [
            'Public Housing Complaint',
            'Slum Improvement Request',
            'Community Hall Issue',
            'Shelter Home Complaint',
        ],
        'Education & Welfare': [
            'Municipal School Complaint',
            'Scholarship Grievance',
            'Mid-Day Meal Issue',
            'Welfare Scheme Delay',
        ],
        'Governance & Anti-Corruption': [
            'Bribery/Corruption Report',
            'RTI Non-Compliance',
            'Officer Misconduct',
            'Tender Irregularity',
        ],
    }
    
    services = []
    for dept in departments:
        dept_services = services_data.get(dept.name, [])
        for service_name in dept_services:
            existing = Service.query.filter_by(name=service_name, department_id=dept.id).first()
            if not existing:
                service = Service(
                    name=service_name,
                    department_id=dept.id,
                    description=f'{service_name} services'
                )
                db.session.add(service)
                print(f"  Created service: {service_name} ({dept.name})")
            services.append(existing or service)
    
    db.session.commit()
    return services


def seed_users(departments):
    """Create admin and officer users."""
    users_created = []
    admin_password = _demo_password('SEED_ADMIN_PASSWORD')
    officer_password = _demo_password('SEED_OFFICER_PASSWORD')
    
    # Create admin
    admin = User.query.filter_by(username='admin').first()
    if not admin:
        admin = User(
            username='admin',
            email='admin@civikindia.gov.in',
            role='admin',
            is_active=True
        )
        admin.set_password(admin_password)
        db.session.add(admin)
        users_created.append(('admin', admin_password, 'admin'))
        print("  Created user: admin (role: admin)")
    
    # Create officers
    officer_data = [
        ('officer_water', 'Water Supply'),
        ('officer_roads', 'Roads & Infrastructure'),
        ('officer_health', 'Public Health'),
        ('officer_electricity', 'Electricity'),
        ('officer_sanitation', 'Sanitation & Solid Waste'),
        ('officer_planning', 'Town Planning & Building'),
        ('officer_revenue', 'Revenue & Taxation'),
        ('officer_transport', 'Transport & Traffic'),
        ('officer_environment', 'Environment & Parks'),
        ('officer_housing', 'Housing & Urban Development'),
        ('officer_education', 'Education & Welfare'),
        ('officer_governance', 'Governance & Anti-Corruption')
    ]
    
    for username, dept_name in officer_data:
        existing = User.query.filter_by(username=username).first()
        if not existing:
            dept = Department.query.filter_by(name=dept_name).first()
            officer = User(
                username=username,
                email=f'{username}@civikindia.gov.in',
                role='officer',
                department_id=dept.id if dept else None,
                is_active=True
            )
            officer.set_password(officer_password)
            db.session.add(officer)
            users_created.append((username, officer_password, 'officer'))
            print(f"  Created user: {username} (role: officer, dept: {dept_name})")
    
    # Create zonal officers and commissioner
    zonal_data = [
        ('zonal_water', 'Water Supply'),
        ('zonal_sanitation', 'Sanitation & Solid Waste')
    ]
    for username, dept_name in zonal_data:
        existing = User.query.filter_by(username=username).first()
        if not existing:
            dept = Department.query.filter_by(name=dept_name).first()
            zo = User(
                username=username,
                email=f'{username}@civikindia.gov.in',
                role='zonal_officer',
                department_id=dept.id if dept else None,
                is_active=True
            )
            zo.set_password(officer_password)
            db.session.add(zo)
            users_created.append((username, officer_password, 'zonal_officer'))
            print(f"  Created user: {username} (role: zonal_officer, dept: {dept_name})")

    existing_comm = User.query.filter_by(username='commissioner').first()
    if not existing_comm:
        comm = User(
            username='commissioner',
            email='commissioner@civikindia.gov.in',
            role='commissioner',
            is_active=True
        )
        comm.set_password(officer_password)
        db.session.add(comm)
        users_created.append(('commissioner', officer_password, 'commissioner'))
        print("  Created user: commissioner (role: commissioner)")

    db.session.commit()
    return users_created


def seed_complaints(departments, services, count=20):
    """Create sample complaints."""
    sample_descriptions_map = {
        'Water Supply': [
            "Water supply has been irregular for the past week. We are facing severe shortage.",
            "Water quality is poor with bad smell and color. Not fit for consumption.",
            "New water connection requested 2 months ago. No response yet.",
            "Huge pipeline leakage observed on the main street. Thousands of liters of water being wasted.",
            "Low water pressure received since last month, barely enough for ground floor tanks."
        ],
        'Roads & Infrastructure': [
            "There is a large pothole on the main road causing accidents. Immediate repair needed.",
            "Street light not working for past 2 weeks. Area is dark and unsafe.",
            "Road construction incomplete for past 3 months. Commuters suffering.",
            "Footpath pavement tiles are broken and missing, making walking hazardous for senior citizens.",
            "A main street light pole is leaning dangerously and might fall. Urgent repair required."
        ],
        'Public Health': [
            "Mosquito breeding in stagnant water near construction site. Risk of dengue outbreak.",
            "Public toilet not maintained properly. Extremely unhygienic conditions and foul smell.",
            "Restaurant in our area is disposing of uncooked food waste in the open. Huge health violation.",
            "Vaccination drive center in the local clinic lacks proper queue management and hygiene."
        ],
        'Electricity': [
            "Power outage for 6 hours daily without pre-announced schedule. Affecting work and daily life.",
            "Voltage fluctuations damaging electrical appliances in our society. Need voltage stabilizer check.",
            "Live electrical wires hanging loose from the transformer post. High risk of shock.",
            "Electric meter running extremely fast. Suspect meter fault or tampering."
        ],
        'Sanitation & Solid Waste': [
            "Garbage has not been collected for 3 days. Foul smell and health hazard.",
            "Sewage overflow on the street due to drainage blockage. Request immediate cleaning.",
            "Waste segregation not being followed by collectors. Need awareness and enforcement.",
            "Illegal dumping of industrial/construction waste on the open plot behind our society.",
            "Public waste bins are broken and trash is scattered all over the road."
        ],
        'Town Planning & Building': [
            "Illegal construction of a commercial building in a residential zone without approvals.",
            "Encroachment on public road/footpath by local shop owners, blocking pedestrian access.",
            "Zoning violation: Industrial operations started in residential complex causing disturbance.",
            "Unauthorized floor being built on top of an existing building, putting structure at risk."
        ],
        'Revenue & Taxation': [
            "Property tax assessment calculation is incorrect. Visited office thrice with no help.",
            "Delays in issuing trade license for new establishment despite submitting all documents.",
            "Water tax billing discrepancy: Billed twice the actual amount. Requesting verification.",
            "No response on birth/death certificate registration correction request filed online."
        ],
        'Transport & Traffic': [
            "Illegal parking on the narrow main street is causing daily gridlocks and traffic jams.",
            "Traffic signal at the main junction is not functioning for the last three days.",
            "Local municipal bus service is highly irregular. Buses do not stop at designated shelters.",
            "Speed breakers needed near the school zone due to rash driving accidents."
        ],
        'Environment & Parks': [
            "Severe noise pollution from loudspeaker usage at night beyond permissible hours.",
            "Illegal cutting/felling of mature green trees along the highway without forest permit.",
            "Local public park has dried grass, broken benches, and is littered with plastic bottles.",
            "Stray animal menace: Pack of stray dogs chasing vehicles and pedestrians at night."
        ],
        'Housing & Urban Development': [
            "Dilapidated condition of public housing blocks. Plaster falling from the ceiling.",
            "Slum redevelopment block lacks basic drinking water pipelines and sewer lines.",
            "Local community hall booked for a private event is charging illegal extra cleaning fees.",
            "Night shelter home for the homeless lacks clean blankets and basic drinking water."
        ],
        'Education & Welfare': [
            "Municipal school building has broken window panes and toilets are unusable for girls.",
            "Grievance regarding delay in disbursal of girls education scholarship scheme.",
            "Mid-day meal hygiene is poor in the ward school. Insects found in the served food.",
            "Local welfare center for senior citizens remains closed during scheduled hours."
        ],
        'Governance & Anti-Corruption': [
            "Local officer demanded a bribe of 5000 INR to process the water connection file.",
            "RTI application filed 45 days ago has not received any reply from the Public Information Officer.",
            "Officer misconduct: Public officials behaving rudely and refusing to take public applications.",
            "Irregularity in road repair tender allotment. Contract given to blacklisted firm."
        ]
    }
    
    statuses = ['Awaiting Review', 'Pending', 'Under Review', 'Action Taken', 'Delayed', 'Reopened', 'Closed']
    status_weights = [0.10, 0.22, 0.22, 0.16, 0.10, 0.08, 0.12]
    
    officers = User.query.filter_by(role='officer').all()
    complaints_created = []
    
    for _ in range(count):
        dept = random.choice(departments)
        dept_services = [s for s in services if s.department_id == dept.id]
        service = random.choice(dept_services) if dept_services else None
        
        # Generate random dates within last 3 months
        days_ago = random.randint(1, 120)
        submitted_at = utc_now() - timedelta(days=days_ago)
        
        # Determine status
        status = random.choices(statuses, weights=status_weights)[0]
        
        location = random.choice(LOCATION_CATALOG)
        city_name = location['city']
        location_lat, location_lng = random_nearby_coords(location['lat'], location['lng'])

        descriptions = sample_descriptions_map.get(dept.name, ["Civic issue reported in the ward."])
        description = random.choice(descriptions)

        # Create complaint
        complaint = Complaint(
            tracking_id=generate_tracking_id(),
            service_id=service.id if service else None,
            department_id=dept.id,
            description=description,
            status=status,
            priority='High' if random.random() < 0.25 else 'Normal',
            escalation_level=random.choice([0, 0, 1, 2]) if status in ['Delayed', 'Reopened'] else 0,
            reopen_count=random.randint(1, 3) if status == 'Reopened' else 0,
            ai_sentiment=random.choice(['negative', 'neutral', 'negative', 'negative', 'positive']),
            ai_urgent=random.random() < 0.2,
            state=location['state'],
            district=location['district'],
            city=location['city'],
            location_lat=location_lat,
            location_lng=location_lng,
            submitted_at=submitted_at,
            updated_at=submitted_at
        )

        if complaint.ai_urgent:
            complaint.priority = 'High'

        if service:
            complaint.sla_due_at = submitted_at + timedelta(days=service.sla_days or 7)
        
        # Assign to officer if not pending or awaiting review
        if status not in ['Pending', 'Awaiting Review'] and officers:
            dept_officers = [o for o in officers if o.department_id == dept.id]
            if dept_officers:
                complaint.assigned_to = random.choice(dept_officers).id
        
        # Add resolution data if closed
        if status == 'Closed':
            complaint.resolved_at = submitted_at + timedelta(days=random.randint(3, 30))
            complaint.updated_at = complaint.resolved_at
            complaint.resolution_notes = "Complaint resolved. Appropriate action taken."
            if random.random() < 0.65:
                complaint.citizen_rating = random.randint(2, 5)
                complaint.citizen_feedback = random.choice([
                    'Issue resolved satisfactorily.',
                    'Partial resolution. Further monitoring needed.',
                    'Response was delayed but finally addressed.',
                    'Team was responsive once escalated.'
                ])
                complaint.feedback_submitted_at = complaint.resolved_at + timedelta(hours=random.randint(2, 72))

        if status == 'Delayed':
            complaint.delayed_at = submitted_at + timedelta(days=random.randint(7, 16))
            complaint.resolution_notes = f"Auto-marked delayed near {city_name} due to SLA breach."

        if status == 'Reopened':
            complaint.resolution_notes = (
                f"Complaint reopened by citizen in {city_name} after initial closure was unsatisfactory."
            )
        
        db.session.add(complaint)
        complaints_created.append(complaint)
        print(f"  Created complaint: {complaint.tracking_id} ({status})")
    
    db.session.commit()
    return complaints_created


def seed_audit_logs(users):
    """Create sample audit logs."""
    actions = [
        'LOGIN_SUCCESS',
        'COMPLAINT_SUBMITTED',
        'STATUS_UPDATE',
        'NOTES_ADDED',
        'COMPLAINT_ASSIGNED'
    ]
    
    for _ in range(30):
        user = random.choice(users) if users else None
        action = random.choice(actions)
        
        log = AuditLog.create_entry(
            user_id=user.id if user else None,
            username=user.username if user else 'anonymous',
            role=user.role if user else 'guest',
            action=action,
            details=f'Sample {action.lower()} entry',
            ip_address=f'192.168.1.{random.randint(1, 255)}'
        )
        print(f"  Created audit log: {action} by {log.username}")


def print_summary(users, complaints):
    """Print seeding summary."""
    print("\n" + "="*60)
    print("  SEEDING COMPLETE")
    print("="*60)
    print("\nCreated Users:")
    print("-" * 40)
    for username, password, role in users:
        print(f"  Username: {username}")
        print(f"  Password: {password}")
        print(f"  Role: {role}")
        print()
    
    print("\nSample Complaint Tracking IDs:")
    print("-" * 40)
    for complaint in complaints[:5]:
        print(f"  {complaint.tracking_id} - {complaint.status}")
    
    print("\n" + "="*60)
    print("  IMPORTANT: Store demo credentials securely or rotate them immediately.")
    print("="*60)


def _parse_complaint_count(raw_value):
    """Parse CLI complaint count with safe bounds."""
    if raw_value is None:
        return 20

    try:
        count = int(raw_value)
    except (TypeError, ValueError):
        raise ValueError('complaint count must be an integer.')

    if count < 1:
        raise ValueError('complaint count must be at least 1.')
    if count > 500:
        raise ValueError('complaint count must be 500 or below for seeding safety.')

    return count


def main():
    """Main seeding function."""
    parser = argparse.ArgumentParser(description='Seed initial CivikIndia data for development/testing.')
    parser.add_argument(
        '--complaints',
        type=int,
        default=None,
        help='Number of demo complaints to generate. Takes precedence over --target-range.'
    )
    parser.add_argument(
        '--target-range',
        choices=['compact', 'medium', 'large'],
        default='compact',
        help='Preset complaint count: compact=20, medium=120, large=150.'
    )
    parser.add_argument(
        '--clear-existing-complaints',
        action='store_true',
        help='Delete existing complaints before generating new ones.'
    )
    parser.add_argument(
        '--seed-audit-logs',
        action='store_true',
        help='Create sample audit logs after seeding complaints.'
    )
    args = parser.parse_args()

    print("\n" + "="*60)
    print("  CivikIndia Database Seeder")
    print("="*60 + "\n")

    target_count = {
        'compact': 20,
        'medium': 120,
        'large': 150
    }.get(args.target_range, 20)

    if args.complaints is not None:
        try:
            complaint_count = _parse_complaint_count(args.complaints)
        except ValueError as exc:
            raise SystemExit(f"Error: {exc}")
    else:
        complaint_count = target_count
    
    # Create app context
    env = os.environ.get('FLASK_ENV', 'development')
    app = create_app(env)
    
    with app.app_context():
        if args.clear_existing_complaints:
            deleted = Complaint.query.delete()
            db.session.commit()
            print(f"Cleared {deleted} existing complaints.")

        print("Creating departments...")
        departments = seed_departments()
        
        print("\nCreating services...")
        services = seed_services(departments)
        
        print("\nCreating SLA policies...")
        seed_sla_policies(departments)

        print("\nCreating escalation contacts...")
        seed_escalation_contacts(departments)
        
        print("\nCreating users...")
        users = seed_users(departments)
        
        print("\nCreating complaints...")
        complaints = seed_complaints(departments, services, count=complaint_count)
        
        print("\nCreating audit logs...")
        if args.seed_audit_logs:
            all_users = User.query.all()
            seed_audit_logs(all_users)
        else:
            print("  Skipping audit logs. Use --seed-audit-logs to include.")
        
        print_summary(users, complaints)


if __name__ == '__main__':
    main()
