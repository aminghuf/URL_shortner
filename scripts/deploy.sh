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
echo "--- Building Docker images ---"
docker-compose build

# 3) Run docker-compose up -d
echo ""
echo "--- Starting services ---"
docker-compose up -d

# 4) Prune old images
echo ""
echo "--- Pruning old Docker images ---"
docker image prune -f

echo ""
echo "====== Deployment complete ======"
