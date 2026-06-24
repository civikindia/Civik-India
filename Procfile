web: python deploy/bootstrap.py && gunicorn wsgi:app --workers ${WEB_CONCURRENCY:-1} --worker-class gevent --timeout ${GUNICORN_TIMEOUT:-30} --bind 0.0.0.0:${PORT:-8000}
worker: celery -A app.celery worker --loglevel=info
beat: celery -A app.celery beat --loglevel=info
