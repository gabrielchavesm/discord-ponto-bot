#!/bin/bash
set -e

# Directory where backups will be stored.
BACKUP_DIR=~/discord-ponto-bot/backups
mkdir -p "$BACKUP_DIR"

# File name with timestamp
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/backup_$TIMESTAMP.sql"

echo "[$(date)] Creating backup: $BACKUP_FILE"
docker exec -t discord-ponto-db pg_dump -U postgres ponto > "$BACKUP_FILE"

# Compress and delete backups older than 30 days.
gzip "$BACKUP_FILE"
find "$BACKUP_DIR" -name "backup_*.sql.gz" -mtime +30 -delete

echo "[$(date)] Backup complete."