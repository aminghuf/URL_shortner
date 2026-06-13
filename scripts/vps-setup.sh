#!/bin/bash
set -e
echo '=============================================='
echo '   URL Shortener - VPS Setup'
echo '=============================================='
echo ''
REPO_URL='https://github.com/aminghuf/URL_shortner'
REPO_DIR='$HOME/url_shortner'
echo '[1/8] Updating system...'
apt-get update -qq && apt-get upgrade -y -qq
apt-get install -y -qq curl git ufw
echo '[2/8] Installing Docker...'
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | bash
    systemctl enable docker && systemctl start docker
fi
docker --version
echo '[3/8] Docker Compose plugin...'
if ! docker compose version &>/dev/null 2>&1; then
    apt-get install -y -qq docker-compose-plugin
fi
echo '[4/8] Cloning repo...'
if [ -d "$REPO_DIR" ]; then
    cd "$REPO_DIR" && git pull origin main
else
    git clone "$REPO_URL" "$REPO_DIR"
fi
cd "$REPO_DIR"
echo '[5/8] Creating .env...'
if [ ! -f "$REPO_DIR/.env" ]; then
    RND=$(head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 32)
    {
        echo '# PostgreSQL'
        echo 'POSTGRES_USER=urlshortener'
        printf '%s=%s\n' POSTGRES_PASSWORD "$RND" >> "$REPO_DIR/.env"
        echo '# App'
        echo 'FLASK_ENV=production'
        echo 'BULK_IMPORT_WORKERS=4'
    } > "$REPO_DIR/.env"
    echo '  .env created'
else
    echo '  .env already exists'
fi
echo '[6/8] SSH deploy key...'
SSH_KEY="$HOME/.ssh/github_actions_deploy"
if [ ! -f "$SSH_KEY" ]; then
    mkdir -p "$HOME/.ssh"
    ssh-keygen -t ed25519 -C 'github-actions-deploy' -f "$SSH_KEY" -N '' -q
    cat "$SSH_KEY.pub" >> "$HOME/.ssh/authorized_keys"
    chmod 600 "$HOME/.ssh/authorized_keys"
    echo ''
    echo '===== COPY TO GITHUB SECRETS: VPS_SSH_KEY ====='
    cat "$SSH_KEY"
    echo '===== END OF KEY ====='
    echo ''
else
    echo '  SSH key already exists'
fi
echo '[7/8] Firewall...'
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp
ufw --force enable
echo '[8/8] Starting services...'
docker compose -f docker-compose.prod.yml build nginx
docker compose -f docker-compose.prod.yml pull app
docker compose -f docker-compose.prod.yml up -d
echo ''
sleep 10
if curl -sf http://localhost:8000/api/health > /dev/null 2>&1; then
    echo '>> App is healthy!'
else
    echo '>> Health check failed - logs:'
    docker compose -f docker-compose.prod.yml logs --tail=30 app
fi
echo ''
echo '=============================================='
echo '  SETUP COMPLETE!'
echo '  http://116.203.200.35:8000/api/health'
echo '  https://aminghuf.dev'
echo '=============================================='
