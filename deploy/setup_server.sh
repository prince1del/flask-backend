#!/usr/bin/env bash
# NEXORA — one-time server setup for a fresh Ubuntu 24.04 VPS (DigitalOcean BLR1 / Lightsail Mumbai).
#
# Run as root on the new server:
#   curl -fsSL <raw url of this file> -o setup_server.sh   (or scp it over)
#   sudo bash setup_server.sh <repo-url> [domain]
#
# Example:
#   sudo bash setup_server.sh git@github.com:prince1del/flask-backend.git api.example.in
#
# Safe to re-run: every step checks before it creates.
set -euo pipefail

REPO_URL="${1:?usage: setup_server.sh <repo-url> [domain]}"
DOMAIN="${2:-}"
APP_USER=nexora
APP_DIR=/opt/nexora
DATA_DIR=/var/data
ENV_FILE=/etc/nexora.env
DB_NAME=nexora
DB_USER=nexora

log() { printf '\n==> %s\n' "$*"; }

log "System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-dev build-essential git \
  nginx postgresql postgresql-contrib sqlite3 ufw \
  certbot python3-certbot-nginx tesseract-ocr libgl1 libglib2.0-0 \
  unar libarchive-tools  # RAR/ZIP SO packs

log "Swap (1GB) so pip / OCR spikes don't OOM a 2GB box"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

log "App user and folders"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash "$APP_USER"
mkdir -p "$APP_DIR" "$DATA_DIR" /var/backups/nexora
chown -R "$APP_USER:$APP_USER" "$APP_DIR" "$DATA_DIR" /var/backups/nexora

log "Code"
if [ ! -d "$APP_DIR/.git" ]; then
  sudo -u "$APP_USER" git clone "$REPO_URL" "$APP_DIR"
else
  sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only
fi

log "Python venv + requirements"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip wheel
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

log "PostgreSQL database"
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
  DB_PASS="$(openssl rand -hex 24)"
  sudo -u postgres psql -c "CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASS';"
  sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"
  NEW_DB_URL="postgresql://$DB_USER:$DB_PASS@127.0.0.1:5432/$DB_NAME"
else
  NEW_DB_URL=""
fi

log "Settings file $ENV_FILE"
if [ ! -f "$ENV_FILE" ]; then
  cp "$APP_DIR/deploy/nexora.env.example" "$ENV_FILE"
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|" "$ENV_FILE"
  sed -i "s|^JWT_SECRET_KEY=.*|JWT_SECRET_KEY=$(openssl rand -hex 32)|" "$ENV_FILE"
  sed -i "s|^SEED_PASSWORD=.*|SEED_PASSWORD=$(openssl rand -base64 12)|" "$ENV_FILE"
  sed -i "s|^HOP_ADMIN_PASSWORD=.*|HOP_ADMIN_PASSWORD=$(openssl rand -base64 12)|" "$ENV_FILE"
  [ -n "$NEW_DB_URL" ] && sed -i "s|^DATABASE_URL=.*|DATABASE_URL=$NEW_DB_URL|" "$ENV_FILE"
  if [ -n "$DOMAIN" ]; then
    sed -i "s|https://YOUR_DOMAIN|https://$DOMAIN|g" "$ENV_FILE"
  fi
  chown root:"$APP_USER" "$ENV_FILE"
  chmod 640 "$ENV_FILE"
  echo "Created $ENV_FILE — fill the API keys in it (see deploy/README.md)."
fi

log "systemd service"
cp "$APP_DIR/deploy/nexora.service" /etc/systemd/system/nexora.service
systemctl daemon-reload
systemctl enable nexora
systemctl restart nexora

log "nginx"
sed "s|YOUR_DOMAIN|${DOMAIN:-_}|g" "$APP_DIR/deploy/nginx-nexora.conf" > /etc/nginx/sites-available/nexora
ln -sf /etc/nginx/sites-available/nexora /etc/nginx/sites-enabled/nexora
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

log "Firewall"
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

log "SO-mail scan timer (every 30 minutes)"
cp "$APP_DIR/deploy/nexora-so-mail.service" /etc/systemd/system/nexora-so-mail.service
cp "$APP_DIR/deploy/nexora-so-mail.timer" /etc/systemd/system/nexora-so-mail.timer
systemctl daemon-reload
systemctl enable --now nexora-so-mail.timer

log "Daily backup (02:30 IST = 21:00 UTC)"
install -m 755 "$APP_DIR/deploy/backup_daily.sh" /usr/local/bin/nexora-backup
echo "0 21 * * * root /usr/local/bin/nexora-backup >> /var/log/nexora-backup.log 2>&1" > /etc/cron.d/nexora-backup

if [ -n "$DOMAIN" ]; then
  log "HTTPS certificate for $DOMAIN (domain's A record must already point here)"
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect || \
    echo "certbot failed — fix DNS, then run: certbot --nginx -d $DOMAIN"
fi

log "Done. Health check:"
sleep 5
curl -s -o /dev/null -w "local /health -> %{http_code}\n" http://127.0.0.1:10000/health || true
echo "Logs: journalctl -u nexora -f"
