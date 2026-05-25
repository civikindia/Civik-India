# Civik India Public Deployment Guide

This guide deploys Civik India on a low-budget stack:

- Koyeb web service first
- Render web service as fallback
- Neon PostgreSQL database
- Cloudflare R2 private evidence storage
- Google Drive private archive backup only

No secrets are stored in code. Add real values only in platform environment variables or your local `.env`.

## Required Environment Variables

Set these in Koyeb or Render:

```env
FLASK_ENV=production
SECRET_KEY=
DATABASE_URL=
MAX_UPLOAD_MB=16
ALLOWED_UPLOAD_EXTENSIONS=jpg,jpeg,png,webp,pdf,mp4,mov,mp3,wav,txt,doc,docx
EVIDENCE_ENCRYPTION_KEY=
AUDIT_HMAC_SECRET=
DEFAULT_ADMIN_PASSWORD=
DEFAULT_OFFICER_PASSWORD=
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=
R2_ENDPOINT_URL=
R2_PUBLIC_BASE_URL=
GOOGLE_DRIVE_BACKUP_ENABLED=false
GOOGLE_DRIVE_FOLDER_ID=
GOOGLE_SERVICE_ACCOUNT_JSON=
GOOGLE_APPLICATION_CREDENTIALS=
BACKUP_CRON_TOKEN=
PORT=8000
WEB_CONCURRENCY=1
GUNICORN_TIMEOUT=120
```

When deploying from the included Render Blueprint, Render auto-generates `SECRET_KEY`, `EVIDENCE_ENCRYPTION_KEY`, `AUDIT_HMAC_SECRET`, and `BACKUP_CRON_TOKEN`. Use long random values for `DEFAULT_ADMIN_PASSWORD` and `DEFAULT_OFFICER_PASSWORD`. `EVIDENCE_ENCRYPTION_KEY` can be a 64-character hex string or another long random secret if you set it manually outside the Blueprint flow.

## A. Neon PostgreSQL

1. Create a Neon project and database.
2. Copy the pooled or direct PostgreSQL connection string, not the Neon REST API endpoint.
3. Set it as `DATABASE_URL` in Koyeb or Render.
4. Keep SSL enabled in the Neon connection string.
5. After the first deployment starts, the app runs `deploy/bootstrap.py` to create tables and baseline departments/officers.
6. For schema upgrades, run the Flask migration command in a one-off shell:

```bash
flask --app wsgi:app db upgrade
```

## B. Cloudflare R2

1. Create a private R2 bucket for evidence, for example `civikindia-evidence`.
2. Create an R2 API token with object read/write access to that bucket.
3. Set:

```env
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=civikindia-evidence
R2_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
```

4. Keep the bucket private.
5. Do not add public evidence URLs. The app streams authorized downloads through Flask after checking admin/officer permissions.

## C. Google Drive Backup

Google Drive is archive storage only. It is not used for live evidence serving.

1. Create a Google Cloud service account.
2. Enable the Google Drive API.
3. Create or select a private Drive folder for backups.
4. Share that folder with the service account email.
5. Set:

```env
GOOGLE_DRIVE_BACKUP_ENABLED=true
GOOGLE_DRIVE_FOLDER_ID=
GOOGLE_SERVICE_ACCOUNT_JSON=
```

You may use `GOOGLE_APPLICATION_CREDENTIALS` instead if your host supports mounted secret files.

Run backups manually:

```bash
flask --app wsgi:app backup-evidence-to-drive
```

Optional protected cron endpoint:

```http
POST /internal/backup/evidence
Authorization: Bearer ${BACKUP_CRON_TOKEN}
```

Backups are idempotent. The Drive file ID is stored in `EvidenceFile.drive_backup_file_id`, and the app never creates public Drive links.

## D. Koyeb Deployment

1. Push the repository to GitHub.
2. In Koyeb, create a new Web Service from the GitHub repository.
3. Choose Dockerfile deployment.
4. Set all required environment variables listed above.
5. Use the Dockerfile default start command.
6. Deploy and open `/healthz`.

The Docker image runs:

```bash
python deploy/bootstrap.py
gunicorn wsgi:app --workers ${WEB_CONCURRENCY:-1} --threads 4 --bind 0.0.0.0:${PORT:-8000} --timeout ${GUNICORN_TIMEOUT:-120}
```

## E. Render Fallback

1. Use a Hobby workspace and a Starter web service.
2. Connect the GitHub repository.
3. Use the included `render.yaml`.
4. Set `DATABASE_URL` to the external Neon connection string.
5. Set all Cloudflare R2 variables.
6. Do not use Render disk for evidence files.
7. Deploy and check `/healthz`.

## F. Local Development

1. Copy `.env.example` to `.env`.
2. Leave `FLASK_ENV=development`.
3. Keep `DEV_DATABASE_URL=sqlite:///instance/civikindia_dev.db`.
4. Leave R2 variables empty to use local private storage under `uploads/`.
5. Install dependencies from `requirements.txt`.
6. Bootstrap and run the app.

For local development without R2/Drive, uploads remain private local files and evidence metadata is still saved in the database.

## Evidence Storage Behavior

- Public citizens upload evidence during complaint submission.
- Files are validated by extension, MIME type, size, and blocked executable extensions.
- Files are encrypted when `EVIDENCE_ENCRYPTION_KEY` is configured.
- R2 object keys are private and randomized: `evidence/<complaint_id>/<uuid>.<ext>.enc`.
- Database metadata is stored in `evidence_files`.
- Admin/officer downloads go through Flask permission checks.
- No public R2 or Google Drive evidence links are returned to users.

## Deployment Notes

- Production startup refuses unsafe missing variables.
- Hosted Postgres uses `DATABASE_URL`.
- `ProxyFix` is enabled in production for Koyeb/Render reverse proxies.
- Secure session cookies are enabled in production.
- `/healthz` is the deployment health check endpoint.
