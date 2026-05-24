"""
CivikIndia WSGI Entry Point
Usage: gunicorn -w 4 -b 127.0.0.1:8000 wsgi:app
"""
import os
from app import create_app

# Get configuration from environment or default to production
config_name = os.environ.get('FLASK_ENV', 'production')
app = create_app(config_name)

# For Gunicorn
application = app

if __name__ == '__main__':
    app.run(debug=False)
