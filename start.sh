#!/bin/bash
set -e

IMAGE="ghcr.io/mariogranaci/discord-ponto-bot"
SERVICE="discord-bot"
CONTAINER="discord-ponto-bot"

# INITIAL CONFIGURATION
BACKUP_DIR=~/discord-ponto-bot/backups
mkdir -p "$BACKUP_DIR"

if [ -f ~/discord-ponto-bot/backup.sh ]; then
    chmod +x ~/discord-ponto-bot/backup.sh
    echo "Permission granted for backup.sh"
fi
if [ -f ~/discord-ponto-bot/restore_latest.sh ]; then
    chmod +x ~/discord-ponto-bot/restore_latest.sh
    echo "Permission granted for restore_latest.sh"
fi

# Configure cron for weekly backup (Sundays at 2 AM)
CRON_JOB='0 2 * * 0 /bin/bash -c "/home/ubuntu/discord-ponto-bot/backup.sh >> /home/ubuntu/discord-ponto-bot/backups/backup_\$(date +\%Y\%m\%d).log 2>&1"'
TMP_CRON=$(mktemp)
crontab -l 2>/dev/null > "$TMP_CRON"
grep -Fq "$CRON_JOB" "$TMP_CRON" || echo "$CRON_JOB" >> "$TMP_CRON"
crontab "$TMP_CRON"
rm "$TMP_CRON"
echo "Weekly backup task ensured."

# DEPLOYMENT
echo "Starting deployment..."

# 1. Snapshot current image as rollback target
echo "Snapshotting current image as rollback target..."
docker tag $IMAGE:latest $IMAGE:rollback 2>/dev/null && \
    echo "Rollback snapshot saved." || \
    echo "No existing image to snapshot (first deploy)."

# 2. Ensure DB is up
echo "Ensuring database is up..."
docker compose up -d db

# 3. Pull and deploy new bot image
echo "Pulling latest bot image..."
docker compose pull $SERVICE

echo "Build updated at:"
docker inspect $IMAGE:latest | grep Created

echo "Recreating bot container with latest image..."
docker compose up -d --force-recreate --no-deps $SERVICE

# 4. Health check
echo "Waiting for bot to become healthy..."
TIMEOUT=60
INTERVAL=5
SUCCESS=0

for i in $(seq 1 $((TIMEOUT / INTERVAL))); do
    if docker logs $CONTAINER 2>&1 | grep -q "INFO:database:Database schema ready"; then
        SUCCESS=1
        break
    fi
    echo "  attempt $i/$((TIMEOUT / INTERVAL))..."
    sleep $INTERVAL
done

# 5. Rollback if unhealthy
if [ $SUCCESS -eq 0 ]; then
    echo "Health check failed! Initiating rollback..."

    if docker image inspect $IMAGE:rollback &>/dev/null; then
        echo "Restoring previous image..."
        docker tag $IMAGE:rollback $IMAGE:latest
        docker compose up -d --force-recreate --no-deps $SERVICE
        echo "Rollback complete. Previous version restored."
    else
        echo "No rollback snapshot available."
    fi

    exit 1
fi

echo "Deploy complete!"