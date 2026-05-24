#!/usr/bin/env bash
# =============================================================
# Daily MySQL Backup Script for CivikIndia
# Add to cron: 0 2 * * * /var/www/civikindia/deploy/backup.sh
# =============================================================

set -e

# Configuration
BACKUP_DIR="/var/backups/civikindia"
DATE=$(date +%Y-%m-%d_%H-%M-%S)
FILENAME="civikindia_backup_${DATE}.sql.gz"
RETENTION_DAYS=30

# Load environment variables
if [[ -f /var/www/civikindia/.env ]]; then
    source /var/www/civikindia/.env
else
    echo "Error: .env file not found"
    exit 1
fi

# Create backup directory
mkdir -p "${BACKUP_DIR}"

# Check if MySQL credentials are set
if [[ -z "${MYSQL_USER}" || -z "${MYSQL_PASSWORD}" || -z "${MYSQL_DB}" ]]; then
    echo "Error: MySQL credentials not configured"
    exit 1
fi

# Perform backup
echo "Starting backup at $(date)"

if mysqldump \
    -u "${MYSQL_USER}" \
    -p"${MYSQL_PASSWORD}" \
    --single-transaction \
    --routines \
    --triggers \
    --lock-tables=false \
    "${MYSQL_DB}" 2>/dev/null | gzip > "${BACKUP_DIR}/${FILENAME}"; then
    
    # Check if backup file was created and has content
    if [[ -s "${BACKUP_DIR}/${FILENAME}" ]]; then
        echo "✓ Backup successful: ${BACKUP_DIR}/${FILENAME}"
        
        # Get file size
        FILESIZE=$(du -h "${BACKUP_DIR}/${FILENAME}" | cut -f1)
        echo "  Size: ${FILESIZE}"
    else
        echo "✗ Backup failed: Empty backup file"
        rm -f "${BACKUP_DIR}/${FILENAME}"
        exit 1
    fi
else
    echo "✗ Backup failed: mysqldump error"
    exit 1
fi

# Clean up old backups
echo "Cleaning up backups older than ${RETENTION_DAYS} days..."
DELETED=$(find "${BACKUP_DIR}" -name "*.sql.gz" -mtime +${RETENTION_DAYS} -print -delete | wc -l)
echo "✓ Removed ${DELETED} old backup files"

# Display backup statistics
echo ""
echo "Backup Statistics:"
echo "  Total backups: $(find ${BACKUP_DIR} -name '*.sql.gz' | wc -l)"
echo "  Total size: $(du -sh ${BACKUP_DIR} | cut -f1)"
echo ""
echo "Backup completed at $(date)"
