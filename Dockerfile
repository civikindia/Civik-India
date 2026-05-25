# CivikIndia — Civic Grievance & Accountability Portal
# Production Dockerfile

FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_ENV=production \
    APP_HOME=/app \
    PORT=8000 \
    WEB_CONCURRENCY=1 \
    GUNICORN_TIMEOUT=120

# Set work directory
WORKDIR ${APP_HOME}

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-libmysqlclient-dev \
    libmagic1 \
    gcc \
    pkg-config \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir gunicorn pymysql

# Copy project
COPY . .

# Create local fallback upload directory
RUN mkdir -p uploads

# Create non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser ${APP_HOME}
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT}/healthz || exit 1

# Run idempotent bootstrap, then Gunicorn with free-tier-safe defaults.
CMD ["sh", "-c", "python deploy/bootstrap.py && gunicorn wsgi:app --workers ${WEB_CONCURRENCY:-1} --threads 4 --bind 0.0.0.0:${PORT:-8000} --timeout ${GUNICORN_TIMEOUT:-120} --access-logfile - --error-logfile -"]
