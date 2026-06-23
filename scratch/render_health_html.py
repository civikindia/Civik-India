import os
os.environ["PYTHONPATH"] = "."
from app import create_app, db
from app.models import User

app = create_app('testing')
with app.app_context():
    db.create_all()
    # Seed admin user
    admin = User(username='admin', role='admin', is_active=True)
    admin.set_password('admin')
    db.session.add(admin)
    db.session.commit()
    
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = admin.id
        sess['username'] = admin.username
        sess['role'] = admin.role
        
    response = client.get('/admin/health')
    html = response.data.decode('utf-8')
    
    # Extract the Redis card HTML
    start_idx = html.find('REDIS')
    if start_idx != -1:
        # Get surrounding 1000 characters
        with open('scratch/redis_card.txt', 'w', encoding='utf-8') as f:
            f.write(html[start_idx-200:start_idx+1000])
        print("Success, wrote to scratch/redis_card.txt")
    else:
        print("Could not find Redis card in HTML")
