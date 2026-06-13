# Scalable URL Shortener with Distributed Orchestration

A production-grade URL shortening service built with Python/Flask, featuring distributed orchestration via Docker Compose. The service accepts long URLs via REST API or web UI, generates unique short codes, and provides fast redirects backed by an in-memory Redis cache. Click tracking, bulk CSV import with concurrent worker pools, and a statistics API are built in. Nginx provides rate limiting, reverse proxying, and security hardening.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Internet / Client                           │
└──────────────────┬──────────────────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│                          Nginx (80)                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │   Rate   │ │  Reverse │ │  Gzip    │ │ Security │ │  Static  │  │
│  │ Limiting │ │  Proxy   │ │Compression│ │ Headers  │ │  Files   │  │
│  └──────────┘ └────┬─────┘ └──────────┘ └──────────┘ └──────────┘  │
└────────────────────┼─────────────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    Flask Application (Gunicorn, port 8000)           │
│                                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │ URL      │ │ Redirect │ │ Click    │ │ Bulk     │ │ Health   │  │
│  │ Shorten  │ │ (302)    │ │ Tracking │ │ CSV Import│ │ Check    │  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────────┘  │
│       │            │            │            │                       │
│       └────────────┴────────────┴────────────┘                       │
│                           │                                          │
│                    ┌──────┴──────┐                                   │
│                    │  Worker Pool│  (ThreadPoolExecutor, max_workers)│
│                    └─────────────┘                                   │
└────────────────────┬─────────────────────────────────────────────────┘
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
┌──────────────────┐  ┌──────────────────────┐
│    PostgreSQL    │  │       Redis          │
│   (SQLAlchemy)   │  │   (Cache + TTL)      │
│                  │  │                      │
│  • URL mappings  │  │  • url:{short_code}  │
│  • Click events  │  │    → long_url        │
│  • Stats/agg     │  │    24h TTL           │
└──────────────────┘  └──────────────────────┘
```

---

## Features

- **URL Shortening** — POST a long URL and receive a unique 6-character short code. Duplicate URLs are detected and return the existing short code.
- **Redirect with Click Tracking** — Each redirect (HTTP 302) records the timestamp, User-Agent, referrer, and client IP for analytics.
- **Bulk CSV Import with Worker Pools** — Upload a CSV or plain-text file of URLs. Concurrent workers (`ThreadPoolExecutor`) validate, deduplicate, and batch-insert into PostgreSQL. Results are cached to Redis in a single pipeline.
- **Redis Caching** — Fast lookup layer with 24-hour TTL. Cache-aside pattern: check Redis first, fall back to PostgreSQL, then populate cache.
- **Rate Limiting** — Two layers: Nginx `limit_req` (30 req/s per IP) + application-level sliding-window rate limiter in Flask (per-client configurable limit).
- **Statistics API** — Retrieve total clicks, recent clicks (last 24h), and creation timestamp for any short code.
- **Health Probes** — `/api/health` endpoint that checks database connectivity and Redis availability.
- **Docker & Docker Compose** — Multi-service orchestration with health checks, resource constraints, and dependency ordering.
- **CI/CD Pipeline** — GitHub Actions runs tests, builds/pushes Docker images, and deploys to a VPS via SSH.

---

## Tech Stack

| Component       | Technology                             |
|-----------------|----------------------------------------|
| **Application** | Python 3.11, Flask 3.x, Gunicorn       |
| **Database**    | PostgreSQL 16 (Alpine), SQLAlchemy ORM |
| **Cache**       | Redis 7 (Alpine)                       |
| **Reverse Proxy** | Nginx 1.25 (Alpine)                  |
| **Containerization** | Docker, Docker Compose           |
| **CI/CD**       | GitHub Actions                        |
| **Testing**     | Pytest                                 |
| **CORS**        | Flask-CORS                             |

---

## Quick Start — Docker Compose

### Prerequisites

- Docker Engine 24+ and Docker Compose v2
- `curl` (for health check verification)
- Git

### Steps

```bash
# 1. Clone the repository
git clone https://github.com/aminghuf/URL_shortner.git
cd URL_shortner

# 2. (Optional) Customize environment variables
export POSTGRES_DB=urlshortener
export POSTGRES_USER=urlshortener
export POSTGRES_PASSWORD=urlshortener_secret
export BULK_IMPORT_WORKERS=4

# 3. Build and start all services
docker compose up --build -d

# 4. Verify health
curl -fs http://localhost:80/api/health | python3 -m json.tool

# 5. Shorten a URL
curl -X POST http://localhost:80/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'

# 6. Test redirect
curl -v http://localhost:80/AbCdEf

# 7. View stats
curl http://localhost:80/stats/AbCdEf

# 8. Tear down
docker compose down -v
```

### Default Services

| Service   | Port(s)   | Purpose                              |
|-----------|-----------|--------------------------------------|
| `nginx`   | `80`      | Reverse proxy, rate limiting, static |
| `app`     | `8000` (internal) | Flask application (Gunicorn)  |
| `postgres`| `5432` (internal) | Primary database               |
| `redis`   | `6379` (internal) | Cache layer                   |

---

## API Documentation

All endpoints are served at `http://localhost:{port}` (port `80` through Nginx, or `8000` directly).

### Endpoints

| Method | Path                    | Description                                                              | Request Body / Query                                          | Response                                                                 |
|--------|-------------------------|--------------------------------------------------------------------------|---------------------------------------------------------------|--------------------------------------------------------------------------|
| `GET`  | `/`                     | Render the landing page (web UI)                                        | —                                                             | HTML page                                                                |
| `GET`  | `/api/health`           | Health probe (DB + Redis checks)                                        | —                                                             | `{"status": "healthy", "database": "up", "redis": "up", "timestamp": "…"}` |
| `POST` | `/shorten`              | Create a short URL                                                      | `{"url": "https://example.com"}` (JSON)                       | `201`: `{"short_code": "aB3xYz", "short_url": "http://host/aB3xYz", "created": true}` |
| `GET`  | `/<short_code>`         | Redirect to the original long URL (with click tracking)                 | —                                                             | `302 Found` → `Location: <long_url>`                                     |
| `GET`  | `/stats/<short_code>`   | Get click statistics for a short code                                   | —                                                             | `{"short_code": "aB3xYz", "long_url": "…", "total_clicks": 42, "recent_clicks_24h": 3, "created_at": "…"}` |
| `POST` | `/bulk-import`          | Bulk import URLs from a CSV or plain-text file                          | Multipart form: `file=<csv_or_txt>`                           | `201`: `{"message": "Successfully imported 150 URL(s).", "total_received": 200, "inserted": 150, "duplicates_skipped": 50}` |

### Error Responses

| Status | Meaning                  | Body                                                    |
|--------|--------------------------|---------------------------------------------------------|
| `400`  | Bad Request              | `{"error": "URL is required"}`                          |
| `404`  | Not Found                | `{"error": "URL not found"}`                            |
| `429`  | Too Many Requests        | `{"error": "Rate limit exceeded. Try again later."}`    |
| `500`  | Internal Server Error    | `{"error": "Database insert failed", "detail": "…"}`    |
| `503`  | Service Unavailable      | `{"status": "degraded", "database": "down", …}`         |

---

## Environment Variables

| Variable               | Required | Default                  | Description                                         |
|------------------------|----------|--------------------------|-----------------------------------------------------|
| `DATABASE_URL`         | Yes      | `sqlite:///urls.db`      | PostgreSQL connection string (auto-converts `postgres://` to `postgresql://`) |
| `REDIS_URL`            | No       | `redis://localhost:6379/0` | Redis connection string                           |
| `BULK_IMPORT_WORKERS`  | No       | `4`                      | Max threads in the `ThreadPoolExecutor` for bulk imports |
| `FLASK_ENV`            | No       | `production`             | Flask environment mode                             |
| `POSTGRES_DB`          | Yes*     | `urlshortener`           | Database name (for `docker compose up`)            |
| `POSTGRES_USER`        | Yes*     | `urlshortener`           | Database user (for `docker compose up`)            |
| `POSTGRES_PASSWORD`    | Yes*     | `urlshortener_secret`    | Database password (for `docker compose up`)        |

> **\*** Required only when using Docker Compose.

---

## Testing

```bash
# With virtual environment active
pytest -v

# With coverage
pytest --cov=app --cov-report=term-missing
```

---

## CI/CD Pipeline

The project uses **GitHub Actions** (`.github/workflows/deploy.yml`) with two jobs:

### `test` Job

Triggered on every push to `main` and on pull requests.

1. **Checkout** — `actions/checkout@v4`
2. **Set up Python** — `actions/setup-python@v5` (Python 3.11)
3. **Install dependencies** — `pip install -r requirements.txt`
4. **Run tests** — `pytest`
5. **Build Docker image** — `docker build -t url-shortener:test .`
6. **Login to Docker Hub** — authenticates with `${{ secrets.DOCKER_HUB_USERNAME }}` and `${{ secrets.DOCKER_HUB_TOKEN }}`
7. **Tag & Push** — pushes `:latest` and `:<commit-sha>` tags to Docker Hub

### `build-and-deploy` Job

Runs after `test` completes successfully.

1. **SSH into VPS** — uses `appleboy/ssh-action` with `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY` secrets
2. **Pull** latest code and Docker image
3. **Restart** services via `docker compose -f docker-compose.prod.yml up -d`
4. **Health check** — verifies the deployment with `curl -sf http://localhost:8000/api/health`

---

## Resource Constraints & Scaling

### Docker Compose Resource Limits

| Service   | CPU Limit | Memory Limit | CPU Reservation | Memory Reservation |
|-----------|-----------|--------------|-----------------|--------------------|
| `app`     | 0.5 CPU   | 512 MB       | 0.2 CPU         | 256 MB             |
| `nginx`   | 0.2 CPU   | 128 MB       | 0.1 CPU         | 64 MB              |
| `postgres`| 0.5 CPU   | 512 MB       | 0.2 CPU         | 256 MB             |
| `redis`   | 0.3 CPU   | 256 MB       | 0.1 CPU         | 128 MB             |

### Nginx Rate Limiting

- **Application layer:** Sliding-window counter in Flask (resets per minute, uses Redis or in-memory dictionary). Configured per-client IP.
- **Proxy layer (Nginx):** `limit_req_zone` with 10 MB shared memory zone (~160,000 IPs tracked), **30 requests/second** per IP, burst of 20, and nodelay.
- **Connection limiting:** Nginx `limit_conn` at 10 concurrent connections per IP.

---

## Project Structure

```
URL_shortner/
├── app.py                      # Flask application (routes, models, caching, rate limiting)
├── requirements.txt            # Python dependencies
├── Dockerfile                  # Multi-stage Python container
├── docker-compose.yml          # Multi-service orchestration (4 containers)
├── docker-compose.prod.yml     # Production overrides
├── .gitignore
│
├── nginx/                      # Nginx reverse proxy
│   ├── Dockerfile              # Alpine-based nginx image
│   └── nginx.conf              # Rate limiting, reverse proxy, security headers
│
├── templates/
│   └── index.html              # Landing page (web UI)
│
├── static/
│   └── style.css               # Frontend styles
│
├── tests/
│   └── test_app.py             # Pytest test suite
│
└── .github/workflows/
    └── deploy.yml              # GitHub Actions CI/CD pipeline
```

---

## License

This project is for educational purposes as part of a university Virtualization Systems course.

---

## Authors

- **amin** — [@aminghuf](https://github.com/aminghuf)
- **shakibofski** — Frontend development
