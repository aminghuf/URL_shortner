#!/usr/bin/env bash
set -euo pipefail

SCRIPT="$(readlink -f "${BASH_SOURCE[0]}")"
PROJECT_DIR="$(cd "$(dirname "$SCRIPT")/.." && pwd)"
cd "$PROJECT_DIR"

echo "====== URL Shortener Deployment ======"
echo "Project dir: $PROJECT_DIR"
echo "Timestamp:   $(date)"

# 1) Pull latest git
echo ""
echo "--- Pulling latest git ---"
git pull origin main || git pull origin master

# 2) Build Docker images
echo ""
echo "--- Building Docker image ---"
docker build -t url-shortener-app:latest .

# Build nginx
echo "--- Building Nginx image ---"
docker build -t url-shortener-nginx:latest ./nginx

# 3) Remove old containers
echo ""
echo "--- Removing old containers ---"
docker rm -f url-shortener-app url-shortener-nginx url_shortner_app_1 url_shortner_nginx_1 2>/dev/null || true

# 4) Start PostgreSQL and Redis if not running
echo ""
echo "--- Ensuring infrastructure ---"
docker start url_shortner_postgres_1 2>/dev/null || \
  docker run -d --restart unless-stopped --name url_shortner_postgres_1 \
    -e POSTGRES_DB=urlshortener \
    -e POSTGRES_USER=urlshortener \
    -e POSTGRES_PASSWORD=urlshortener_secret \
    -v url_shortner_postgres_data:/var/lib/postgresql/data \
    --network url_shortner_default \
    postgres:16-alpine 2>/dev/null || true

docker start url_shortner_redis_1 2>/dev/null || \
  docker run -d --restart unless-stopped --name url_shortner_redis_1 \
    -p 6379:6379 \
    --network url_shortner_default \
    redis:7-alpine 2>/dev/null || true

# Wait for PostgreSQL
echo "--- Waiting for PostgreSQL ---"
for i in $(seq 1 15); do
  if docker exec url_shortner_postgres_1 pg_isready -U urlshortener 2>/dev/null; then
    echo "PostgreSQL ready!"
    break
  fi
  echo "Waiting... ($i/15)"
  sleep 2
done

# 5) Start the app container
echo ""
echo "--- Starting app container ---"
docker run -d --restart unless-stopped --name url-shortener-app \
  -p 8000:8000 \
  -e "DATABASE_URL=postgresql://urlshortener:urlshortener_secret@url_shortner_postgres_1:5432/urlshortener" \
  -e "REDIS_URL=redis://url_shortner_redis_1:6379/0" \
  -e BULK_IMPORT_WORKERS="4" \
  -e FLASK_ENV="production" \
  --network url_shortner_default \
  url-shortener-app:latest

# 6) Start nginx
echo ""
echo "--- Starting nginx container ---"
docker run -d --restart unless-stopped --name url-shortener-nginx \
  -p 8888:80 \
  --network url_shortner_default \
  url-shortener-nginx:latest

# 7) Health check
echo ""
echo "--- Health check ---"
sleep 5
curl -sf http://localhost:8000/api/health && echo "App: Healthy" || echo "App: Degraded"
curl -sf http://localhost:8888/api/health && echo "Nginx: Healthy" || echo "Nginx: Degraded"

# 8) Prune old images
echo ""
echo "--- Pruning old Docker images ---"
docker image prune -f

echo ""
echo "====== Deployment complete ======"
