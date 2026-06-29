# Scalable URL Shortener — Complete Line-by-Line Guide

> A production-grade URL shortening service with Flask, PostgreSQL, Redis, Nginx, and Docker.
> Built by **amin (@aminghuf)** & **shakibofski** for a university Virtualization Systems course.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Project Structure](#2-project-structure)
3. [app.py — The Flask Application](#3-apppy--the-flask-application)
4. [requirements.txt — Python Dependencies](#4-requirementstxt--python-dependencies)
5. [Dockerfile — Python Container](#5-dockerfile--python-container)
6. [nginx/Dockerfile — Nginx Container](#6-nginxdockerfile--nginx-container)
7. [nginx/nginx.conf — Nginx Configuration](#7-nginxnginxconf--nginx-configuration)
8. [docker-compose.yml — Development Orchestration](#8-docker-composeyml--development-orchestration)
9. [docker-compose.prod.yml — Production Orchestration](#9-docker-composeprodyaml--production-orchestration)
10. [templates/index.html — Frontend UI](#10-templatesindexhtml--frontend-ui)
11. [static/style.css — Frontend Styling](#11-staticstylecss--frontend-styling)
12. [tests/test_app.py — Test Suite](#12-teststest_apppy--test-suite)
13. [scripts/deploy.sh — Deployment Script](#13-scriptsdeploysh--deployment-script)
14. [scripts/vps-setup.sh — VPS Setup](#14-scriptsvps-setupsh--vps-setup)
15. [scripts/webhook_server.py — Webhook Listener](#15-scriptswebhook_serverpy--webhook-listener)
16. [scripts/urlshortener-webhook.service — Systemd Service](#16-scriptsurlshortener-webhookservice--systemd-service)
17. [README.md — Project Documentation](#17-readmemd--project-documentation)

---

## 1. Project Overview

### What It Does

The service accepts long URLs via a REST API or web UI, generates unique 6-character short codes, and provides fast HTTP 302 redirects backed by a Redis cache layer. It tracks every click (timestamp, User-Agent, referrer, IP) for analytics, supports bulk CSV import with concurrent worker pools, and exposes health probes for container orchestration.

### Architecture

```
                      Internet / Client
                             │
                             ▼
         ┌───────────────────────────────────┐
         │          Nginx (80/443)            │
         │  Rate Limiting → Reverse Proxy     │
         └───────────────┬───────────────────┘
                         │
                         ▼
         ┌───────────────────────────────────┐
         │     Flask / Gunicorn (8000)        │
         │  ┌─────────┐ ┌────────┐           │
         │  │ Shorten │ │Redirect│           │
         │  ├─────────┤ ├────────┤           │
         │  │ Click   │ │ Bulk   │           │
         │  │ Tracking│ │ Import │           │
         │  └────┬────┘ └───┬────┘           │
         │       │          │                  │
         │   ┌───┴──────────┴───┐             │
         │   │ ThreadPoolWorker │             │
         │   └──────────────────┘             │
         └────────────────┬──────────────────┘
                          │
               ┌──────────┴──────────┐
               ▼                     ▼
     ┌────────────────┐  ┌────────────────────┐
     │   PostgreSQL    │  │       Redis         │
     │  (SQLAlchemy)   │  │  (Cache + 24h TTL)  │
     │  URL Mappings   │  │  url:{code}→long_url│
     │  Click Events   │  │                     │
     └────────────────┘  └────────────────────┘
```

### Data Flow (Shorten Request)

1. Client sends `POST /shorten` with `{"url": "https://..."}` → hits Nginx (port 80)
2. Nginx checks rate limit → proxies to Flask (port 8000)
3. Flask normalises URL → validates → checks DB for duplicates
4. Generates unique 6-char code → inserts into PostgreSQL
5. Writes to Redis cache with 24h TTL
6. Returns `{"short_code": "XyZ123", "short_url": "http://host/XyZ123", "created": true}`

### Data Flow (Redirect Request)

1. Client visits `GET /<short_code>` → Nginx → Flask
2. Flask checks Redis cache first (fast path)
3. On cache miss → queries PostgreSQL
4. Records click event (User-Agent, referrer, IP) asynchronously
5. Increments click counter on the URL mapping
6. Returns HTTP 302 redirect to the original long URL

---

## 2. Project Structure

```
URL_shortner/
├── app.py                           # Flask application (527 lines)
├── requirements.txt                 # Python dependencies (7 lines)
├── Dockerfile                       # Python container build (36 lines)
├── docker-compose.yml               # Dev orchestration (94 lines)
├── docker-compose.prod.yml          # Production orchestration (116 lines)
├── README.md                        # Project documentation (275 lines)
│
├── nginx/
│   ├── Dockerfile                   # Nginx container build (23 lines)
│   ├── nginx.conf                   # Nginx configuration (182 lines)
│   └── ssl/
│       ├── fullchain.pem            # SSL certificate (placeholder)
│       └── privkey.pem              # SSL private key (placeholder)
│
├── templates/
│   └── index.html                   # Web UI landing page (258 lines)
│
├── static/
│   └── style.css                    # Frontend stylesheet (314 lines)
│
├── tests/
│   └── test_app.py                  # Pytest test suite (60 lines)
│
└── scripts/
    ├── deploy.sh                    # Manual deployment script (93 lines)
    ├── vps-setup.sh                 # VPS bootstrap script (79 lines)
    ├── webhook_server.py            # GitHub webhook listener (93 lines)
    └── urlshortener-webhook.service # Systemd unit file (19 lines)
```

---

## 3. app.py — The Flask Application

This is the heart of the project: **527 lines** covering models, rate limiting, URL shortening, redirects, click tracking, bulk CSV import with thread pools, and health probes.

### Section 1: Imports (Lines 1–16)

```python
import csv
import io
import os
import random
import re
import string
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import redis as redis_lib
from flask import Flask, jsonify, redirect, render_template, request
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
```

| Line(s) | Import | Purpose |
|---------|--------|---------|
| 1 | `csv` | Parse CSV files for bulk import |
| 2 | `io` | Wrap text as file-like objects for CSV reader |
| 3 | `os` | Read environment variables (DATABASE_URL, REDIS_URL, etc.) |
| 4 | `random` | Generate random characters for short codes |
| 5 | `re` | Validate URL format with regex |
| 6 | `string` | Use `string.ascii_letters + string.digits` for code generation |
| 7 | `time` | Sliding-window rate limiter timestamps |
| 8 | `ThreadPoolExecutor, as_completed` | Parallel worker pool for bulk CSV processing |
| 9 | `datetime, timezone` | UTC timestamps for click events and health checks |
| 11 | `redis as redis_lib` | Redis client library for caching layer |
| 12 | Flask components | Web framework, JSON responses, HTTP redirects, HTML templates, request parsing |
| 13 | `CORS` | Cross-Origin Resource Sharing support |
| 14 | `SQLAlchemy` | ORM for PostgreSQL database operations |
| 15 | `func` | SQL functions like `func.now()` for health checks |

### Section 2: App & Configuration (Lines 17–33)

```python
app = Flask(__name__, template_folder="templates")

from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///urls.db")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

BULK_IMPORT_WORKERS = int(os.getenv("BULK_IMPORT_WORKERS", "4"))
MAX_WORKERS = min(BULK_IMPORT_WORKERS, 20)

RATE_LIMIT = int(os.getenv("RATE_LIMIT", "30"))
RATE_LIMIT_WINDOW = 60

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)
```

| Lines | Code | Explanation |
|-------|------|-------------|
| 21 | `Flask(__name__, template_folder="templates")` | Creates the Flask application instance. `__name__` tells Flask to look for resources relative to this file. `template_folder` points to the `templates/` directory |
| 24-25 | `ProxyFix(app.wsgi_app, x_proto=1, x_host=1)` | Wraps the WSGI app to trust `X-Forwarded-Proto` and `X-Forwarded-Host` headers — essential when running behind Cloudflare or Nginx so that `request.host_url` and `url_for` generate correct HTTPS URLs |
| 27-29 | `DATABASE_URL` | Reads the database connection string from environment. Falls back to SQLite for local dev. The `postgres://` → `postgresql://` replacement is a compatibility fix — SQLAlchemy 1.4+ only accepts the `postgresql://` scheme |
| 31 | `REDIS_URL` | Redis connection string from env, defaults to localhost:6379/0 (DB index 0) |
| 33-34 | `BULK_IMPORT_WORKERS` | Configurable worker count for the thread pool, capped at 20 to avoid resource exhaustion |
| 36-37 | `RATE_LIMIT`, `RATE_LIMIT_WINDOW` | Per-IP rate limit: 30 requests per 60-second window (configurable via env var) |
| 39-41 | SQLAlchemy config | Sets the database URI, disables the modification tracker (saves memory), and initialises the ORM |

### Section 3: Database Models (Lines 43–70)

```python
class URLMapping(db.Model):
    __tablename__ = "url_mappings"
    id = db.Column(db.Integer, primary_key=True)
    short_code = db.Column(db.String(10), unique=True, nullable=False, index=True)
    long_url = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, server_default=func.now())
    click_count = db.Column(db.Integer, default=0)
    clicks = db.relationship("Click", backref="url_mapping", lazy="dynamic")

class Click(db.Model):
    __tablename__ = "clicks"
    id = db.Column(db.Integer, primary_key=True)
    url_mapping_id = db.Column(db.Integer, db.ForeignKey("url_mappings.id"), nullable=False)
    timestamp = db.Column(db.DateTime, server_default=func.now())
    user_agent = db.Column(db.String(500), default="")
    referrer = db.Column(db.String(500), default="")
    ip_address = db.Column(db.String(45), default="")
```

| Line(s) | Model | Columns | Purpose |
|---------|-------|---------|---------|
| 43-49 | **URLMapping** | `id` (PK), `short_code` (unique, indexed), `long_url`, `created_at`, `click_count`, `clicks` (relationship) | Stores one row per shortened URL. The index on `short_code` speeds up redirect lookups significantly |
| 51-58 | **Click** | `id` (PK), `url_mapping_id` (FK → URLMapping.id), `timestamp`, `user_agent`, `referrer`, `ip_address` | Each click on a short URL creates a new Click row. Foreign key cascading not set explicitly — orphaned clicks on URL deletion would need manual cleanup |
| 49 | `db.relationship("Click", lazy="dynamic")` | Creates a one-to-many relationship from URLMapping to Click. `lazy="dynamic"` returns a Query object instead of loading all clicks — useful for `.count()` and `.filter()` without loading every row |

### Section 4: Redis Connection (Lines 72–81)

```python
_redis_client = None

def get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        _redis_client = redis_lib.from_url(
            REDIS_URL, socket_connect_timeout=2, socket_timeout=2, decode_responses=True
        )
        _redis_client.ping()
    except Exception:
        _redis_client = None
    return _redis_client
```

| Lines | Purpose |
|-------|---------|
| 72 | Module-level variable to cache the Redis client singleton |
| 74-81 | `get_redis()` function: returns the cached client, or attempts a connection. `socket_connect_timeout=2` prevents hanging if Redis is down. `decode_responses=True` returns strings instead of bytes. If connection or ping fails, returns `None` — the rest of the app handles `None` gracefully (degraded mode) |

### Section 5: URL Validation & Normalization (Lines 83–107)

```python
def normalize_url(url):
    url = url.strip()
    if not re.match(r'^https?://', url, re.IGNORECASE):
        url = "https://" + url
    return url

def is_valid_url(url):
    pattern = re.compile(
        r'^https?://'
        r'([\w\-]+\.)+[\w\-]+'
        r'(:\d+)?'
        r'(/[\w\-./?%&=~#@!$\'()*+,;:]*)?'
        r'$',
        re.IGNORECASE
    )
    return bool(pattern.match(url))
```

| Lines | Function | Explanation |
|-------|----------|-------------|
| 83-87 | `normalize_url()` | Strips whitespace and prepends `https://` if no scheme is present. This is user-friendly — someone pasting "example.com" gets a valid URL without needing to type the protocol |
| 89-97 | `is_valid_url()` | A regex pattern that validates URLs have a proper scheme, domain (at least one dot), optional port, and optional path with common characters. The regex is intentionally lenient — it allows query params, fragments, and special characters that real URLs may contain |

### Section 6: Short Code Generation (Lines 99–111)

```python
def generate_short_code(length=6):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))

def generate_short_code_method_2(length=6):
    import secrets
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))
```

| Lines | Function | Explanation |
|-------|----------|-------------|
| 99-102 | `generate_short_code()` | Uses `random.choices` for a fast 6-char code from 62 possible characters (a-z, A-Z, 0-9). 62^6 ≈ 56 billion combinations — collision-resistant enough for this scale |
| 104-108 | `generate_short_code_method_2()` | Alternative using `secrets.choice` (cryptographically secure random). Slower but more secure. Defined but not used in the current code — available as a drop-in replacement |

### Section 7: Rate Limiting (Lines 113–170)

```python
_rate_limit_store: dict[str, list[float]] = {}

def is_rate_limited(client_ip: str) -> bool:
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW

    r = get_redis()
    if r is not None:
        try:
            key = f"ratelimit:{client_ip}"
            pipe = r.pipeline()
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            results = pipe.execute()
            count = results[1]
            if count >= RATE_LIMIT:
                return True
            pipe.zadd(key, {str(now): now})
            pipe.expire(key, RATE_LIMIT_WINDOW * 2)
            pipe.execute()
            return False
        except Exception:
            pass

    # Fallback: in-memory store
    if client_ip not in _rate_limit_store:
        _rate_limit_store[client_ip] = []
    timestamps = _rate_limit_store[client_ip]
    while timestamps and timestamps[0] < window_start:
        timestamps.pop(0)
    if len(timestamps) >= RATE_LIMIT:
        return True
    timestamps.append(now)
    return False
```

| Lines | Component | Explanation |
|-------|-----------|-------------|
| 113 | `_rate_limit_store` | In-memory dictionary: `{client_ip: [timestamps]}`. Falls back to this if Redis is unavailable |
| 115 | `is_rate_limited()` | Main rate-limiting function. Uses a **sliding-window counter** (not a fixed clock-aligned window) |
| 118-137 | **Redis path** | Uses a **sorted set** keyed by `ratelimit:<ip>`. Each request adds a member with score = current timestamp. The pipeline: removes entries older than the window → counts remaining → if at limit, returns True → otherwise adds current timestamp and sets expiry (2× window for safety) |
| 141-149 | **In-memory fallback** | If Redis is down, uses a plain Python dict with a list of timestamps. `while` loop pops expired entries from the front (oldest first). This is O(n) per request — fine for small loads but doesn't scale to thousands of IPs |

### Section 8: Health Endpoints (Lines 172–210)

```python
@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/api/health")
def api_health():
    db_ok = False; redis_ok = False
    try:
        db.session.execute(db.select(func.now()))
        db_ok = True
    except Exception:
        db_ok = False
    r = get_redis()
    try:
        if r is not None: r.ping(); redis_ok = True
    except Exception:
        redis_ok = False
    status_code = 200 if db_ok else 503
    return jsonify({
        "status": "healthy" if db_ok else "degraded",
        "database": "up" if db_ok else "down",
        "redis": "up" if redis_ok else "down (non-critical)",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }), status_code

@app.route("/")
def home():
    return render_template("index.html")
```

| Lines | Route | Purpose |
|-------|-------|---------|
| 172-175 | `GET /health` | Simple legacy health check — returns `{"status": "ok"}`. Keeping it for backward compatibility |
| 178-203 | `GET /api/health` | Kubernetes-ready health probe. Actually tests DB connectivity with `func.now()` query (not just an HTTP check). If DB is down, returns 503 (Service Unavailable). Redis status is advisory only — marked "non-critical" because the app works without it |
| 206-209 | `GET /` | Serves the landing page web UI from `templates/index.html` |

### Section 9: URL Shortening Route (Lines 216–264)

```python
@app.route("/shorten", methods=["POST"])
def shorten_url():
    client_ip = request.remote_addr or "unknown"
    if is_rate_limited(client_ip):
        return jsonify({"error": "Rate limit exceeded. Try again later."}), 429

    data = request.get_json(silent=True) or {}
    long_url = data.get("url", "").strip()

    if not long_url:
        return jsonify({"error": "URL is required"}), 400

    long_url = normalize_url(long_url)

    if not is_valid_url(long_url):
        return jsonify({"error": "Invalid URL provided"}), 400

    existing = URLMapping.query.filter_by(long_url=long_url).first()
    if existing:
        return jsonify({
            "short_code": existing.short_code,
            "short_url": request.host_url.rstrip("/") + "/" + existing.short_code,
            "created": False,
        }), 200

    short_code = generate_short_code()
    while URLMapping.query.filter_by(short_code=short_code).first():
        short_code = generate_short_code()

    url_mapping = URLMapping(short_code=short_code, long_url=long_url)
    db.session.add(url_mapping)
    db.session.commit()

    r = get_redis()
    if r is not None:
        try:
            r.setex(f"url:{short_code}", 86400, long_url)
        except Exception:
            pass

    return jsonify({
        "short_code": short_code,
        "short_url": request.host_url.rstrip("/") + "/" + short_code,
        "created": True,
    }), 201
```

| Lines | Step | Explanation |
|-------|------|-------------|
| 219 | `client_ip = request.remote_addr or "unknown"` | Gets the client IP. Behind Nginx/Cloudflare, this is the real IP because `ProxyFix` middleware unwraps `X-Forwarded-For` |
| 220-221 | Rate limit check | Returns HTTP 429 if the client has exceeded the limit |
| 223 | `request.get_json(silent=True) or {}` | Parses JSON body. `silent=True` returns `None` on parse error instead of aborting — avoids crash on malformed JSON |
| 224 | `data.get("url", "").strip()` | Extracts the URL from the JSON payload, defaulting to empty string |
| 226-227 | Empty URL check | Returns 400 if no URL was provided |
| 229 | `normalize_url(long_url)` | Adds `https://` if missing |
| 231-232 | URL validation | Returns 400 if the URL format is invalid |
| 235-241 | **Duplicate detection** | Queries DB for existing long URL. If found, returns the existing short code with `"created": False` — idempotent behaviour prevents duplicate entries |
| 244-246 | **Short code generation** | Generates a random 6-char code, checking the DB to ensure uniqueness. The `while` loop is extremely unlikely to run more than once |
| 248-250 | DB insert | Creates a new `URLMapping` row and commits |
| 253-258 | **Redis cache write** | Stores `url:{short_code} → long_url` with a 24-hour TTL (86400 seconds). The `try/except` makes this non-blocking — if Redis is down, the app still works (just without caching) |
| 260-264 | Response | Returns 201 with the short code, full short URL, and creation flag |

### Section 10: Redirect Route (Lines 271–318)

```python
@app.route("/<short_code>")
def redirect_to_url(short_code):
    r = get_redis()
    long_url = None
    if r is not None:
        try:
            long_url = r.get(f"url:{short_code}")
        except Exception:
            pass

    if not long_url:
        url_mapping = URLMapping.query.filter_by(short_code=short_code).first()
        if not url_mapping:
            return jsonify({"error": "URL not found"}), 404
        long_url = url_mapping.long_url
        if r is not None:
            try:
                r.setex(f"url:{short_code}", 86400, long_url)
            except Exception:
                pass
    else:
        url_mapping = URLMapping.query.filter_by(short_code=short_code).first()
        if not url_mapping:
            return jsonify({"error": "URL not found"}), 404

    # Click tracking – fire-and-forget style
    try:
        click = Click(
            url_mapping_id=url_mapping.id,
            user_agent=request.headers.get("User-Agent", "")[:500],
            referrer=request.headers.get("Referer", "")[:500],
            ip_address=request.remote_addr,
        )
        db.session.add(click)
        URLMapping.query.filter_by(id=url_mapping.id).update(
            {URLMapping.click_count: URLMapping.click_count + 1}
        )
        db.session.commit()
    except Exception:
        db.session.rollback()

    return redirect(long_url, code=302)
```

| Lines | Step | Explanation |
|-------|------|-------------|
| 274-281 | **Cache-aside (check Redis first)** | Checks `url:{short_code}` in Redis. If found, avoids a DB query entirely — this is the fast path |
| 284-295 | **Cache miss → DB fallback** | Queries PostgreSQL for the mapping. If not found, returns 404. If found, **populates Redis** with `setex` so subsequent requests hit the cache |
| 296-300 | **Cache hit but need DB for stats** | If Redis returned the URL, we still need the `URLMapping` object for click tracking metadata. A second DB query is necessary — this is a known design trade-off |
| 303-316 | **Click tracking** | Creates a `Click` record with User-Agent, referrer, and IP (all truncated to 500 chars). Atomically increments `click_count`. Wrapped in `try/except` with `rollback()` — click tracking is fire-and-forget; a failed click log should never break the redirect |
| 318 | `redirect(long_url, code=302)` | HTTP 302 Found redirect to the original long URL. 302 is used (not 301) to allow click tracking on every visit — browsers cache 301 redirects aggressively |

### Section 11: Stats Route (Lines 325–347)

```python
@app.route("/stats/<short_code>")
def stats(short_code):
    url_mapping = URLMapping.query.filter_by(short_code=short_code).first()
    if not url_mapping:
        return jsonify({"error": "URL not found"}), 404

    total_clicks = url_mapping.click_count
    cutoff = datetime.now(timezone.utc)
    recent_clicks = Click.query.filter(
        Click.url_mapping_id == url_mapping.id,
        Click.timestamp >= cutoff,
    ).count()

    return jsonify({
        "short_code": short_code,
        "long_url": url_mapping.long_url,
        "total_clicks": total_clicks,
        "recent_clicks_24h": recent_clicks,
        "created_at": url_mapping.created_at.isoformat() if url_mapping.created_at else None,
    }), 200
```

| Lines | Detail | Explanation |
|-------|--------|-------------|
| 326-328 | Lookup | Fetches the URLMapping by short_code. Returns 404 if not found |
| 332 | `total_clicks` | Uses the denormalised `click_count` column — a fast integer read without counting Click rows |
| 335-339 | **Recent clicks (24h)** | Counts Click rows created in the last 24 hours. `cutoff = datetime.now(timezone.utc)` defines the window. This is a `COUNT` query on the Click table — could be slow for high-traffic URLs (see optimisation note below) |
| 341-347 | Response | Returns the full stats object. `created_at` is formatted as ISO 8601 string or `None` if missing |

**⚠️ Gotcha:** The `recent_clicks_24h` query uses `Click.timestamp >= cutoff` where `cutoff` is `datetime.now()`, not `datetime.now() - timedelta(hours=24)`. This means it returns clicks from **now to the future** (zero clicks unless clock is skewed). This is a bug — should be `datetime.now(timezone.utc) - timedelta(hours=24)`.

### Section 12: Bulk Import — Helper Functions (Lines 354–388)

```python
def _validate_and_prepare_url(row: dict) -> dict | None:
    url = (row.get("url") or row.get("long_url") or "").strip()
    if not url: return None
    url = normalize_url(url)
    if not is_valid_url(url): return None
    code = generate_short_code()
    return {"short_code": code, "long_url": url}

def _generate_unique_code(code_set: set) -> str:
    code = generate_short_code()
    while code in code_set:
        code = generate_short_code()
    return code

def _process_urls_chunk(chunk: list[dict], existing_codes: set) -> list[dict]:
    results = []
    local_codes = set(existing_codes)
    for row in chunk:
        url = (row.get("url") or row.get("long_url") or "").strip()
        if not url: continue
        url = normalize_url(url)
        if not is_valid_url(url): continue
        code = _generate_unique_code(local_codes)
        local_codes.add(code)
        results.append({"short_code": code, "long_url": url})
    return results
```

| Lines | Function | Purpose |
|-------|----------|---------|
| 354-363 | `_validate_and_prepare_url()` | Single-row URL validator. Accepts keys "url" or "long_url" from CSV. Returns a dict with `short_code` and `long_url`, or `None` if invalid |
| 366-371 | `_generate_unique_code()` | Ensures a short code doesn't collide with an existing set. Used during batch processing to avoid uniqueness violations |
| 374-388 | `_process_urls_chunk()` | **Worker function** for ThreadPoolExecutor. Takes a chunk of rows and a set of existing codes. Returns validated mappings with unique codes. Each worker maintains its own `local_codes` copy to prevent intra-chunk collisions |

### Section 13: Bulk Import — Main Route (Lines 391–527)

```python
@app.route("/bulk-import", methods=["POST"])
def bulk_import():
    client_ip = request.remote_addr or "unknown"
    if is_rate_limited(client_ip):
        return jsonify({"error": "Rate limit exceeded. Try again later."}), 429

    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file uploaded. Use form field 'file'."}), 400

    filename = file.filename or ""
    raw_text = file.read().decode("utf-8", errors="replace")

    rows: list[dict] = []
    if filename.endswith(".csv"):
        reader = csv.DictReader(io.StringIO(raw_text))
        for row in reader:
            rows.append(row)
    else:
        for line in raw_text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                rows.append({"url": line})

    # ... deduplication, worker pool, batch insert ...
```

| Lines | Step | Explanation |
|-------|------|-------------|
| 391-396 | Route setup, rate limiting | Same pattern as `/shorten` |
| 399-401 | File validation | Expects a multipart form upload with form field name `file`. Returns 400 if missing |
| 403-404 | Read file content | Decodes as UTF-8 with `errors="replace"` to handle non-UTF-8 characters gracefully |
| 407-417 | **CSV vs text parsing** | If the filename ends in `.csv`, uses `csv.DictReader` to parse columns. Otherwise, treats as plain text — one URL per line, skipping blank lines and comment lines starting with `#` |
| 422 | `total = len(rows)` | Records total rows received (used in response) |
| 424-430 | **Get existing codes** | Fetches all short codes from DB into a Python set to minimise DB round-trips during batch generation |
| 433-434 | **Chunking** | Splits rows into `chunk_size`-sized batches for the worker pool. At least 1 chunk even for small files |
| 440-447 | **ThreadPoolExecutor** | Creates a pool with `MAX_WORKERS` threads. Submits each chunk to `_process_urls_chunk()`. Uses `as_completed()` to collect results as workers finish (better than waiting for all) |
| 449-454 | No valid URLs | If all rows were invalid, returns 400 with error details |
| 457-462 | **Dedup within batch** | Uses a `seen` dict keyed by `long_url` to remove intra-batch duplicates, keeping the first occurrence |
| 464-470 | **Check against existing DB** | Fetches all existing long URLs from DB to avoid re-inserting duplicates |
| 472 | `to_insert = [item for item in unique_batch if item["long_url"] not in existing_urls]` | List comprehension filtering out URLs already in the database |
| 475-483 | **Build results** | Creates a results list with status "success" or "duplicate" for frontend display |
| 485-491 | All duplicates | If every URL already exists, returns 200 with a message |
| 494-509 | **Batch insert** | Uses `db.session.bulk_insert_mappings(URLMapping, to_insert)` — far faster than inserting one row at a time. Then populates Redis cache using a **pipeline** for efficiency (single network round-trip for all entries) |
| 510-517 | Error handling | On insert failure, rolls back the session and returns 500 |
| 519-526 | Success response | Returns 201 with counts: total_received, inserted, duplicates_skipped |

---

## 4. requirements.txt — Python Dependencies

```
flask>=3.0
flask-sqlalchemy>=3.1
flask-cors>=4.0
psycopg2-binary>=2.9
redis>=5.0
pytest>=7.0
gunicorn>=21.2
```

| Package | Min Version | Purpose |
|---------|-------------|---------|
| `flask` | 3.0 | Web framework — routing, request handling, templating |
| `flask-sqlalchemy` | 3.1 | SQLAlchemy ORM integration for Flask (models, sessions) |
| `flask-cors` | 4.0 | Cross-Origin Resource Sharing headers for API access from other domains |
| `psycopg2-binary` | 2.9 | PostgreSQL adapter — binary distribution (no compile step) |
| `redis` | 5.0 | Redis client — caching, rate limiting sorted sets |
| `pytest` | 7.0 | Test runner — test discovery, assertions |
| `gunicorn` | 21.2 | Production WSGI server — multi-worker HTTP serving |

**⚠️ Gotcha:** `psycopg2-binary` is the binary version, suitable for development but not recommended for production per the psycopg2 docs. The `Dockerfile` also installs `libpq-dev` which would allow compiling `psycopg2` (non-binary) as an alternative.

---

## 5. Dockerfile — Python Container

```dockerfile
FROM python:3.11-slim

LABEL org.opencontainers.image.source="https://github.com/aminghuf/URL_shortner"
LABEL org.opencontainers.image.description="Scalable URL Shortener with Distributed Orchestration"
LABEL org.opencontainers.image.version="1.0.0"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FLASK_ENV=production

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN addgroup --system app && adduser --system --ingroup app app --home /app && \
    chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "4", "--threads", "2", \
     "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
```

| Line(s) | Instruction | Explanation |
|---------|-------------|-------------|
| 1 | `FROM python:3.11-slim` | Base image — official Python 3.11 on Debian slim (smaller than full Debian) |
| 3-5 | `LABEL` | OCI annotations — standardised metadata for container registries (source repo, description, version) |
| 7-9 | `ENV` | `PYTHONDONTWRITEBYTECODE=1` prevents writing `.pyc` files (keeps image smaller). `PYTHONUNBUFFERED=1` ensures logs stream immediately (essential for Docker log collection). `FLASK_ENV=production` disables debug mode |
| 11 | `WORKDIR /app` | Sets the working directory — all subsequent COPY, RUN, CMD use this as base |
| 13-16 | `RUN apt-get update ...` | Installs system packages: `gcc` + `build-essential` for compiling Python extensions, `libpq-dev` for psycopg2, `curl` for health checks. `--no-install-recommends` reduces bloat. `rm -rf /var/lib/apt/lists/*` cleans the apt cache in the same layer (layer-size optimisation) |
| 18-20 | `COPY requirements.txt .` + `pip install` | **Layer caching optimisation**: copying only requirements.txt first means Docker caches the `pip install` layer. If requirements.txt doesn't change, Docker reuses this cached layer instead of reinstalling every build |
| 22 | `COPY . .` | Copies the entire project into the image |
| 25-27 | **Non-root user** | Creates a system group `app` and user `app` with home at `/app`. `chown -R` makes `/app` writable. `USER app` switches from root — best practice for security (if the app is compromised, the attacker doesn't have root) |
| 29 | `EXPOSE 8000` | Documents that the container listens on port 8000 (informational, doesn't publish the port) |
| 32-34 | `HEALTHCHECK` | Docker-native health check. Runs `curl -f http://localhost:8000/api/health` every 30s. After 3 failures, Docker marks the container as unhealthy |
| 36 | `CMD` | Launches **Gunicorn** (production WSGI server) with 4 workers, 2 threads per worker. `--timeout 120` kills workers stuck for 2+ minutes. `--access-logfile -` and `--error-logfile -` send logs to stdout/stderr (standard Docker pattern). `app:app` means "import `app` from `app.py`, use Flask instance named `app`" |

**⚠️ Gotcha:** The `COPY . .` at line 22 copies everything including `requirements.txt` again. Since `requirements.txt` was already copied at line 19, this second copy overwrites it in the same layer — no real harm, but slightly redundant.

---

## 6. nginx/Dockerfile — Nginx Container

```dockerfile
FROM nginx:1.25-alpine

RUN rm -f /etc/nginx/conf.d/default.conf

COPY nginx.conf /etc/nginx/nginx.conf

COPY ssl/fullchain.pem /etc/nginx/ssl/fullchain.pem
COPY ssl/privkey.pem /etc/nginx/ssl/privkey.pem
RUN chmod 600 /etc/nginx/ssl/privkey.pem

RUN mkdir -p /var/log/nginx && \
    touch /var/log/nginx/access.log /var/log/nginx/error.log && \
    chmod 640 /var/log/nginx/*.log

EXPOSE 80 443

CMD ["nginx", "-g", "daemon off;"]
```

| Line(s) | Instruction | Explanation |
|---------|-------------|-------------|
| 1 | `FROM nginx:1.25-alpine` | Official Nginx 1.25 on Alpine Linux — very small image (~23 MB compressed) |
| 4 | `RUN rm -f /etc/nginx/conf.d/default.conf` | Removes the default Nginx config that ships with the image. Our config goes to `/etc/nginx/nginx.conf` — the `conf.d/` approach was not used |
| 6 | `COPY nginx.conf /etc/nginx/nginx.conf` | Copies our custom Nginx configuration to the default config path |
| 8-10 | SSL certificates | Copies the certificate chain and private key. `chmod 600` ensures only root can read the private key — critical for security |
| 12-15 | Log directories | Creates log directory and files with appropriate permissions. `640` (owner read-write, group read) is reasonable for log files |
| 17 | `EXPOSE 80 443` | Documents HTTP and HTTPS ports |
| 19 | `CMD ["nginx", "-g", "daemon off;"]` | Runs Nginx in the foreground (Docker expects PID 1 to stay alive). `-g` passes directives to the global config; `daemon off;` prevents Nginx from forking into the background |

---

## 7. nginx/nginx.conf — Nginx Configuration

This 182-line file defines Nginx as a reverse proxy, rate limiter, and SSL terminator.

### Events Block (Lines 1–4)

```nginx
events {
    worker_connections 1024;
    multi_accept on;
}
```

| Directive | Value | Explanation |
|-----------|-------|-------------|
| `worker_connections` | 1024 | Max simultaneous connections per worker process. With 1 worker by default, the total capacity is 1024 connections |
| `multi_accept` | on | Accepts multiple new connections at once instead of one at a time — reduces latency under burst traffic |

### HTTP Block — Gzip (Lines 10–24)

```nginx
gzip on;
gzip_vary on;
gzip_proxied any;
gzip_comp_level 6;
gzip_min_length 256;
gzip_types text/plain text/css text/javascript application/javascript application/json ...
```

| Directive | Value | Explanation |
|-----------|-------|-------------|
| `gzip` | on | Enables gzip compression for HTTP responses |
| `gzip_vary` | on | Adds `Vary: Accept-Encoding` header — important for CDNs/proxies |
| `gzip_proxied` | any | Compresses responses even to proxied requests |
| `gzip_comp_level` | 6 | Balance between compression ratio and CPU usage (1=fast, 9=smallest) |
| `gzip_min_length` | 256 | Don't compress responses smaller than 256 bytes (waste of CPU) |
| `gzip_types` | list | Only compress these text-based MIME types. Notably missing: images, PDFs (already compressed) |

### HTTP Block — Rate Limiting (Lines 26–28)

```nginx
limit_req_zone $binary_remote_addr zone=url_shortener_limit:10m rate=30r/s;
limit_conn_zone $binary_remote_addr zone=addr:10m;
```

| Directive | Explanation |
|-----------|-------------|
| `limit_req_zone` | Defines a shared memory zone `url_shortener_limit` (10 MB) keyed by client IP (`$binary_remote_addr` — compact representation). 10 MB holds ~160,000 IP entries. Rate: 30 requests per second |
| `limit_conn_zone` | Defines another zone `addr` for concurrent connection limiting |

### HTTP Block — Upstream (Lines 31–33)

```nginx
upstream url_shortener_backend {
    server url_shortner_app_1:8000 max_fails=3 fail_timeout=30s;
}
```

| Directive | Explanation |
|-----------|-------------|
| `upstream` | Defines a backend server group named `url_shortener_backend`. Points to the Flask app container on port 8000 |
| `max_fails=3` | After 3 failed attempts, marks the server as down |
| `fail_timeout=30s` | Marks server down for 30 seconds after failures |

### HTTP Server (Lines 36–118)

The HTTP server serves on port 80, primarily behind Cloudflare proxy. It handles ACME challenges, respects Cloudflare's real IP headers, proxies to Flask, and serves custom error pages.

**Key locations:**

| Lines | Location | Purpose |
|-------|----------|---------|
| 41-44 | `/.well-known/` | Exposes ACME challenge files for Let's Encrypt SSL certificate renewal. Root is `/var/www` |
| 47-68 | `real_ip_header` + `set_real_ip_from` | Extracts the real visitor IP from Cloudflare's `CF-Connecting-IP` header. Defines all Cloudflare IP ranges (IPv4 + IPv6) so Nginx trusts them. Without this, Nginx sees Cloudflare's IP instead of the visitor's |
| 72-87 | `location /` | **Main proxy block**: forwards all requests to Flask, sets proxy headers (`Host`, `X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto` — hardcoded to `https`). Defines timeouts: connect 10s, send/read 30s. Buffering on with 4K buffers |
| 90-96 | `location /api/health` | Health check endpoint with `access_log off` — avoids filling logs with health check noise. Minimal proxy headers |
| 99-103 | `location ~ /\\.` | **Hidden file protection**: blocks access to any path starting with a dot (`.git`, `.env`, etc.). Returns 403 |
| 106-117 | **Error pages** | `error_page 429` returns a JSON rate-limit message. `error_page 502 503 504` returns a JSON "Service Unavailable" message. Using `internal;` prevents direct access to these locations |

### HTTPS Server (Lines 121–181)

The HTTPS server on port 443 provides SSL termination and additional security middleware.

| Lines | Feature | Explanation |
|-------|---------|-------------|
| 121 | `listen 443 ssl http2;` | Listens on 443 with SSL and HTTP/2 support |
| 125-131 | **SSL configuration** | Points to certificate files (`ssl/` directory mapped at build time). Limits protocols to TLSv1.2+ (TLSv1.0/1.1 are deprecated). Defines secure cipher suites. `ssl_session_cache shared:SSL:10m` caches SSL session data for faster subsequent connections |
| 133-135 | **Security headers** | `Strict-Transport-Security` (HSTS, 2-year max-age) forces HTTPS. `X-Frame-Options: SAMEORIGIN` prevents clickjacking. `X-Content-Type-Options: nosniff` prevents MIME type sniffing |
| 137 | `client_max_body_size 10M` | Limits upload size to 10 MB (for bulk CSV imports) |
| 139-141 | **Rate/connection limits** | Applies the request rate limit (30r/s burst=20) and connection limit (10 per IP). `limit_conn_status 429` returns proper HTTP 429 for connection limit violations |
| 143-156 | `location /` proxy | Same proxy configuration as HTTP (minus `X-Forwarded-Proto: https` hardcoding — uses `$scheme` instead for correctness) |

---

## 8. docker-compose.yml — Development Orchestration

```yaml
version: "3.8"

services:
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: "postgresql://${POSTGRES_USER:-urlshortener}:${POSTGRES_PASSWORD:-urlshortener_secret}@postgres:5432/${POSTGRES_DB:-urlshortener}"
      REDIS_URL: "redis://redis:6379/0"
      BULK_IMPORT_WORKERS: "4"
      FLASK_ENV: "production"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_started
    restart: unless-stopped
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: "512M"
        reservations:
          cpus: "0.2"
          memory: "256M"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/health"]
      interval: 30s; timeout: 5s; retries: 3; start_period: 10s

  nginx:
    build: ./nginx
    ports:
      - "80:80"
      - "443:443"
    depends_on:
      - app
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-urlshortener}
      POSTGRES_USER: ${POSTGRES_USER:-urlshortener}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-urlshortener_secret}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-urlshortener}"]
      interval: 10s; timeout: 5s; retries: 5

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]

volumes:
  postgres_data:
```

| Service | Key Details | Explanation |
|---------|-------------|-------------|
| **app** | Builds from local `Dockerfile`, exposes 8000, depends on `postgres:healthy` + `redis:started` | The `depends_on` with `condition: service_healthy` ensures Flask only starts after PostgreSQL is ready (not just "started"). The app has CPU/memory limits and reservations — Docker will guarantee the reserved amount and throttle beyond the limit |
| **nginx** | Builds from `./nginx/Dockerfile`, ports 80:80 and 443:443 | Reverse proxy for all external traffic. SSL ports exposed for HTTPS |
| **postgres** | `postgres:16-alpine`, named volume `postgres_data` for persistence | Health check uses `pg_isready` — the standard PostgreSQL readiness check. Alpine base keeps the image small |
| **redis** | `redis:7-alpine`, port 6379 exposed | Port exposed for debugging/connecting directly. Pipeline operations benefit from minimal latency |

**Environment Variable Substitution:** `${VARIABLE:-default}` syntax means "use the env var or fall back to this default". All four services use this pattern for configurable credentials.

---

## 9. docker-compose.prod.yml — Production Orchestration

The production variant is **standalone** (not a merge overlay). It differs from `docker-compose.yml` in these ways:

| Aspect | Dev (`docker-compose.yml`) | Production (`docker-compose.prod.yml`) |
|--------|---------------------------|----------------------------------------|
| **App image** | Builds from local `Dockerfile` | Pulls `aminghazizadeh/url-shortener:latest` from Docker Hub (`pull_policy: always` ensures latest) |
| **Container name** | Generated by compose | Explicitly set: `url_shortner_app_1` |
| **Nginx** | Builds locally (same as prod) | Builds locally (fast rebuild, no push needed) |
| **Redis ports** | Exposed on host (`6379:6379`) | Not exposed (internal network only) |

**Usage on VPS:**
```bash
docker compose -f docker-compose.prod.yml up -d
```

**⚠️ Gotcha:** The production file sets `container_name: url_shortner_app_1` which matches the name Nginx's `nginx.conf` references in its `upstream` block. If the container name changes, Nginx won't find the backend.

---

## 10. templates/index.html — Frontend UI

This is a single-page web UI with two primary functions: URL shortening and bulk CSV/TXT import.

### HTML Structure (Lines 1–59)

```html
<div class="container" id="mainContainer">
    <h1>URL Shortener</h1>

    <!-- Single URL Shortening -->
    <div class="form-group">
        <input type="url" id="longUrl" placeholder="Paste your long URL here..." required>
        <button onclick="shortenUrl()">Shorten</button>
    </div>

    <div id="resultContainer" class="result-box hidden">
        <p>Your short URL:</p>
        <a id="shortUrl" href="#" target="_blank"></a>
    </div>

    <!-- Bulk Import Section -->
    <div class="bulk-import-section">
        <h2>Bulk Import</h2>
        <div class="file-input-wrapper">
            <input type="file" id="bulkFileInput" accept=".csv,.txt" />
            <button class="btn-import" id="startImportBtn" onclick="startBulkImport()">Start Import</button>
        </div>

        <div class="progress-wrapper" id="progressWrapper" style="display: none;">
            <div class="progress-bar">
                <div class="progress-fill" id="progressFill"></div>
            </div>
            <div class="progress-status">
                <span id="progressLabel">Uploading...</span>
                <span id="progressPercent">0%</span>
            </div>
        </div>

        <div class="import-status" id="importStatus"></div>

        <div class="results-table-wrapper" id="resultsWrapper" style="display: none;">
            <table class="results-table">
                <thead><tr>
                    <th>#</th><th>Original URL</th><th>Short URL</th><th>Status</th>
                </tr></thead>
                <tbody id="resultsBody"></tbody>
            </table>
        </div>
    </div>
</div>
```

| Element | Purpose |
|---------|---------|
| `<input type="url" id="longUrl">` | Text input for pasting a long URL. `type="url"` triggers built-in browser validation |
| `<button onclick="shortenUrl()">Shorten</button>` | Triggers the single-URL shortening AJAX call |
| `#resultContainer` (hidden by default) | Shows the shortened URL result as a clickable link |
| `#bulkFileInput` | File picker filtered to `.csv, .txt` |
| `#startImportBtn` | Triggers bulk import with XMLHttpRequest (for upload progress tracking) |
| `#progressWrapper` (hidden) | Upload progress bar with percentage and label |
| `#resultsWrapper` (hidden) | Results table showing original URL, short URL, and status for each item |

### JavaScript — `shortenUrl()` Function (Lines 63–98)

```javascript
async function shortenUrl() {
    const urlInput = document.getElementById('longUrl').value;
    const resultContainer = document.getElementById('resultContainer');
    const shortUrlLink = document.getElementById('shortUrl');

    if (!urlInput) { alert("Please enter a URL first!"); return; }
    resultContainer.classList.add('hidden');

    try {
        const response = await fetch('/shorten', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: urlInput })
        });
        const data = await response.json();
        if (response.ok) {
            shortUrlLink.href = data.short_url;
            shortUrlLink.innerText = data.short_url;
            resultContainer.classList.remove('hidden');
        } else {
            alert(data.error || "Something went wrong.");
        }
    } catch (error) {
        console.error("Error connecting to server:", error);
        alert("Could not reach backend server.");
    }
}
```

| Lines | Logic | Explanation |
|-------|-------|-------------|
| 68-70 | Empty input check | Simple alert — no submission if empty |
| 73 | `resultContainer.classList.add('hidden')` | Hides previous result before making new request (prevents showing stale data) |
| 76-82 | `fetch('/shorten', ...)` | POST request with JSON body. Uses `async/await` for clean error handling |
| 86-89 | Success | Sets the link's `href` and text to the short URL, removes `hidden` class |
| 90-91 | API error | Shows the server's error message in an alert |
| 94-97 | Network error | Catches fetch failures (e.g., server down) |

### JavaScript — `startBulkImport()` Function (Lines 101–211)

```javascript
async function startBulkImport() {
    const fileInput = document.getElementById('bulkFileInput');
    const file = fileInput.files[0];
    // ... get all DOM elements ...

    if (!file) { /* show error */; return; }
    if (!file.name.match(/\.(csv|txt)$/i)) { /* show error */; return; }

    // Show progress UI
    btn.disabled = true;
    progressWrapper.style.display = 'block';
    progressFill.style.width = '0%';
    progressPercent.textContent = '0%';

    const formData = new FormData();
    formData.append('file', file);

    const xhr = new XMLHttpRequest();

    xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
            const percent = Math.round((e.loaded / e.total) * 100);
            progressFill.style.width = percent + '%';
            progressPercent.textContent = percent + '%';
        }
    });

    xhr.addEventListener('load', function () {
        // Parse response, build status message, display results table
        btn.disabled = false;
        progressFill.style.width = '100%';
        // ... parse and display ...
    });

    xhr.addEventListener('error', function () {
        // Network error handling
    });

    xhr.open('POST', '/bulk-import', true);
    xhr.send(formData);
}
```

**Why `XMLHttpRequest` instead of `fetch`?** The bulk import needs **upload progress tracking**. `fetch` doesn't natively support upload progress events; `XMLHttpRequest` does via `xhr.upload.addEventListener('progress', ...)`. This is a pragmatic choice.

| Event | Behaviour |
|-------|-----------|
| `progress` | Updates progress bar width and percentage during upload. After 100%, changes label to "Processing URLs..." |
| `load` | Response received: enables button, parses JSON, builds results table, shows summary message (success/duplicates/failures) |
| `error` | Network error: shows error message |

### JavaScript — `displayBulkResults()` Function (Lines 213–256)

Builds the results table dynamically:

```javascript
function displayBulkResults(results) {
    const tbody = document.getElementById('resultsBody');
    tbody.innerHTML = '';
    results.forEach((item, index) => {
        const tr = document.createElement('tr');
        // Column 1: row number
        // Column 2: original URL (truncated display)
        // Column 3: short URL as clickable link
        // Column 4: status badge (✓ Success / ✗ Failed / ↩ Duplicate)
        tbody.appendChild(tr);
    });
}
```

Status badges are styled with CSS classes `.success` (green), `.error` (red), `.duplicate` (orange).

---

## 11. static/style.css — Frontend Styling

A 314-line dark-themed stylesheet for the URL shortener UI. Key design decisions:

| CSS Section | Lines | Purpose |
|-------------|-------|---------|
| Body layout | 1-11 | Dark background (`#1e222b`), flexbox centring, full viewport height (`100vh`), no scroll (`overflow: hidden`) |
| Container | 13-22 | Dark card (`#282c34`) with rounded corners (12px), subtle shadow, max-width 450px |
| Form group | 25-29 | Flexbox row with 10px gap for input + button |
| Input styling | 31-44 | Dark input field (`#353b48`), no border, custom placeholder colour |
| Button | 46-59 | Blue primary button (`#4b7bec`), hover darkens to `#3867d6`, bold text, rounded |
| Result box | 62-88 | Semi-transparent dark background, break-all for long URLs, blue link colour |
| Bulk import section | 93-170 | **Separated by a top border line**. `h2` in muted uppercase. File input uses dashed border (`2px dashed #353b48`). Import button is green (`#2ed573`). Disabled state is grey with reduced opacity |
| Progress bar | 174-200 | 8px height, rounded, gradient fill (blue → green). Smooth width transitions |
| Status messages | 203-230 | Three colour variants: `.success` (green bg/border), `.error` (red), `.info` (blue). Each with 12% opacity background and 30% border |
| Results table | 233-314 | Scrollable wrapper (max 320px height). Custom scrollbar styling. Sticky header with uppercase labels. Row hover highlights with subtle blue tint. Status badges as inline coloured pills |

**⚠️ Gotcha:** `overflow: hidden` on the body (line 10) disables page scrolling entirely. If the bulk import results table has many rows, content will be clipped. The results wrapper has its own scrollbar, so the main interface stays compact.

---

## 12. tests/test_app.py — Test Suite

```python
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import app

def test_health():
    client = app.test_client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json == {"status": "ok"}

def test_shorten_url():
    client = app.test_client()
    import random
    test_url = f"https://test-{random.randint(10000,99999)}.com"
    response = client.post("/shorten", json={"url": test_url})
    assert response.status_code == 201
    data = response.get_json()
    assert "short_code" in data
    assert "short_url" in data

def test_shorten_missing_url():
    client = app.test_client()
    response = client.post("/shorten", json={})
    assert response.status_code == 400

def test_redirect_to_url():
    client = app.test_client()
    import random
    unique_url = f"https://example-{random.randint(10000,99999)}.com"
    response = client.post("/shorten", json={"url": unique_url})
    assert response.status_code == 201
    data = response.get_json()
    short_code = data["short_code"]
    response = client.get(f"/{short_code}")
    assert response.status_code == 302
    assert response.headers["Location"] == unique_url
```

| Test | What It Validates | Lines |
|------|-------------------|-------|
| `test_health()` | The `/health` endpoint returns 200 with `{"status": "ok"}` | 7-12 |
| `test_shorten_url()` | POST to `/shorten` with a valid URL returns 201 with `short_code` and `short_url` in the JSON response. Uses a random URL to avoid collisions with existing data | 14-29 |
| `test_shorten_missing_url()` | POST to `/shorten` with empty JSON body returns 400 | 31-39 |
| `test_redirect_to_url()` | End-to-end test: creates a short URL, then follows the redirect and validates HTTP 302 with correct `Location` header | 41-60 |

**⚠️ Gotcha:** Tests use the **SQLite in-memory database** by default (since `DATABASE_URL` env var isn't set in the test environment, it falls back to `sqlite:///urls.db`). This means tests write to a real SQLite file on disk. For CI pipelines, this works but the SQLite file persists between runs. A better practice would be to override `DATABASE_URL` to `sqlite:///:memory:` in the test setup.

---

## 13. scripts/deploy.sh — Deployment Script

A manual deployment script (93 lines) for deploying on a VPS without CI/CD.

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT="$(readlink -f "${BASH_SOURCE[0]}")"
PROJECT_DIR="$(cd "$(dirname "$SCRIPT")/.." && pwd)"
cd "$PROJECT_DIR"
```

**Shebang and safety flags:**
| Flag | Purpose |
|------|---------|
| `set -e` | Exit on any error (no silent failures) |
| `set -u` | Treat unset variables as errors |
| `set -o pipefail` | Fail pipeline if any command fails (not just the last) |

**Script flow:**

| Step | Lines | Command | Explanation |
|------|-------|---------|-------------|
| 1 | 15 | `git pull origin main \|\| git pull origin master` | Pulls latest code. Tries `main` first, falls back to `master` |
| 2 | 20-24 | `docker build -t url-shortener-app:latest .` | Builds the Flask image. Then builds the Nginx image from `./nginx` |
| 3 | 28-29 | `docker rm -f url-shortener-app ...` | Force-removes old containers (ignores errors if they don't exist) |
| 4 | 34-57 | `docker start \|\| docker run` PostgreSQL + Redis | Starts existing containers if stopped, or creates new ones. Waits for PostgreSQL ready with a polling loop (up to 15 attempts, 2s apart) |
| 5 | 63-70 | `docker run -d ... url-shortener-app:latest` | Starts Flask app with env vars, attached to `url_shortner_default` network |
| 6 | 75-78 | `docker run -d ... url-shortener-nginx:latest` | Starts Nginx exposing port 8888 (not 80 — to avoid conflicts) |
| 7 | 83-85 | `curl -sf http://localhost:8000/api/health` | Health check verification with brief output |
| 8 | 90 | `docker image prune -f` | Cleans up dangling images (keeps disk usage low) |

**⚠️ Gotcha:** The deploy script uses hardcoded container names and network names (`url_shortner_default`). If the Docker Compose project name changes (based on directory name), the network name changes too. Use `docker network ls` to verify.

---

## 14. scripts/vps-setup.sh — VPS Setup Script

A bootstrap script (79 lines) for initial VPS provisioning. Runs unattended.

| Step | Lines | Actions |
|------|-------|---------|
| 1 | 9-10 | `apt-get update && apt-get upgrade` — updates all system packages |
| 2 | 10 | `apt-get install -y curl git ufw` — essential utilities + firewall |
| 3 | 12-16 | **Docker installation**: Uses the official `get.docker.com` script. Enables and starts Docker |
| 4 | 18-20 | **Docker Compose plugin**: Installs `docker-compose-plugin` if not present |
| 5 | 22-27 | **Clones repo**: `git clone https://github.com/aminghuf/URL_shortner` (or pulls if already exists) |
| 6 | 29-42 | **Creates `.env` file**: Generates a random 32-char POSTGRES_PASSWORD using `/dev/urandom`, base64 encoded. Sets POSTGRES_USER, FLASK_ENV, BULK_IMPORT_WORKERS |
| 7 | 44-57 | **SSH deploy key**: Generates an Ed25519 SSH key for GitHub Actions. Adds the public key to `authorized_keys` — this is critical for CI/CD to SSH into the VPS. Prints the private key to stdout for copying to GitHub Secrets |
| 8 | 59-60 | **Firewall rules**: Opens ports 22 (SSH), 80 (HTTP), 443 (HTTPS). Enables UFW (Ubuntu's firewall) |
| 9 | 62-65 | **Starts services**: Builds nginx locally, pulls app from Docker Hub, starts all services with `docker compose -f docker-compose.prod.yml up -d` |
| 10 | 68-73 | **Health check**: Waits 10s then verifies the app is healthy |

**Key security decisions:**
- SSH deploy key with `authorized_keys` (no password auth needed for CI/CD)
- UFW firewall allowing only ports 22, 80, 443
- Random PostgreSQL password stored in `.env`
- `.env` is in `.gitignore` (should be — worth verifying)

---

## 15. scripts/webhook_server.py — GitHub Webhook Listener

A standalone Flask server (93 lines) that listens for GitHub push events and triggers deployment.

### Signature Verification (Lines 27–37)

```python
def verify_signature(payload: bytes, signature_header: str) -> bool:
    if not SECRET:
        return True
    if not signature_header:
        logger.warning("Missing signature header")
        return False
    expected = "sha256=" + hmac.new(
        SECRET.encode(), msg=payload, digestmod=hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)
```

| Lines | Logic | Explanation |
|-------|-------|-------------|
| 29-30 | No secret configured | If `WEBHOOK_SECRET` env var is empty, accept all requests (weaker security for dev setups) |
| 31-33 | Missing header | If GitHub didn't send a signature, reject |
| 34-37 | HMAC verification | Recomputes the SHA256 HMAC using the shared secret and compares with GitHub's header using `hmac.compare_digest` (constant-time comparison prevents timing attacks) |

### Webhook Route (Lines 40–81)

```python
@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.get_data()
    sig = request.headers.get("X-Hub-Signature-256", "")

    if not verify_signature(payload, sig):
        return jsonify({"status": "unauthorized"}), 401

    event = request.headers.get("X-GitHub-Event", "")
    if event != "push":
        return jsonify({"status": "ignored", "event": event})

    body = request.get_json(silent=True) or {}
    ref = body.get("ref", "")
    if ref != "refs/heads/main":
        return jsonify({"status": "ignored", "ref": ref})

    # Trigger deployment
    result = subprocess.run(
        ["bash", DEPLOY_SCRIPT], capture_output=True, text=True, timeout=300,
    )
    # ... logging and response ...
```

| Check | Lines | Purpose |
|-------|-------|---------|
| Signature verification | 45-47 | Rejects requests that don't carry a valid HMAC signature |
| Event type | 49-52 | Only processes `push` events; ignores `ping`, `pull_request`, etc. |
| Branch filter | 56-58 | Only deploys on pushes to `main` — ignores feature branches |
| Execution | 62-67 | Runs `deploy.sh` via `subprocess.run` with 300s timeout. Captures stdout/stderr for logging |

### Health Route & Entry Point (Lines 84–93)

```python
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    port = int(os.environ.get("WEBHOOK_PORT", 9999))
    host = os.environ.get("WEBHOOK_HOST", "0.0.0.0")
    app.run(host=host, port=port, debug=False)
```

Runs on port 9999 by default. The `shebang` at line 1 points to a virtual environment Python, indicating this runs as a standalone service (not via Docker).

---

## 16. scripts/urlshortener-webhook.service — Systemd Service

```ini
[Unit]
Description=URL Shortener Webhook Server
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=/root/URL_shortner
ExecStart=/root/URL_shortner/scripts/.venv/bin/python3 /root/URL_shortner/scripts/webhook_server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

| Directive | Value | Explanation |
|-----------|-------|-------------|
| `After` | `network.target docker.service` | Starts after network and Docker are ready |
| `Requires` | `docker.service` | If Docker isn't running, this service won't start |
| `Type` | `simple` | Systemd assumes the process starts immediately (no forking) |
| `User` | `root` | Runs as root (access to Docker socket for `bash deploy.sh`) |
| `Restart` | `always` | Restarts the process if it crashes or exits |
| `RestartSec` | `5` | Waits 5 seconds before restarting |

**Installation:** Copy to `/etc/systemd/system/urlshortener-webhook.service`, then `systemctl enable --now urlshortener-webhook`.

---

## 17. README.md — Project Documentation

The README (275 lines) serves as the project's official documentation and covers:

| Section | Lines | Content |
|---------|-------|---------|
| Title + Description | 1-3 | "Scalable URL Shortener with Distributed Orchestration" — one-line project summary |
| Architecture diagram | 7-49 | ASCII art showing the full stack: Internet → Nginx → Flask → PostgreSQL/Redis |
| Features list | 53-63 | 7 bullet points summarising every capability |
| Tech Stack table | 67-79 | Components with precise versions (Python 3.11, PostgreSQL 16 Alpine, Redis 7 Alpine, Nginx 1.25 Alpine) |
| Quick Start | 82-132 | Step-by-step instructions: clone → env vars → `docker compose up` → test → teardown |
| API Documentation | 135-158 | Table of all endpoints with request/response schemas and error codes |
| Environment Variables | 161-178 | Table of 7 configurable env vars with defaults and descriptions |
| CI/CD Pipeline | 181-213 | GitHub Actions workflow: test → build → push → deploy. Two jobs: `test` (basic checks) and `build-and-deploy` (SSH into VPS) |
| Resource Constraints | 216-232 | Docker resource limits table (CPU/memory per service) + rate limiting details |
| Project Structure | 236-262 | Full directory tree |
| License | 266-268 | "Educational purposes — university Virtualization Systems course" |
| Authors | 272-275 | amin (@aminghuf) + shakibofski (frontend) |

**⚠️ Gotcha:** The README references `.github/workflows/deploy.yml` for CI/CD, but this file is not present in the current repository. The CI/CD pipeline exists only conceptually and in the documentation — it would need to be created to enable automated deployments.

---

## Key Architectural Decisions Summary

| Decision | Rationale |
|----------|-----------|
| **Flask over FastAPI** | Simpler, well-understood, sufficient for this use case. Gunicorn provides production WSGI serving |
| **PostgreSQL over SQLite** | Concurrent access safety, better performance under load, required for Docker multi-service setup |
| **SQLAlchemy ORM** | Database-agnostic queries, migration support, relationship management |
| **Redis cache-aside** | 20-50x faster reads than PostgreSQL for redirects. 24h TTL balances recency vs storage |
| **Nginx reverse proxy** | Offloads rate limiting, SSL termination, static file serving from Flask |
| **ThreadPoolExecutor** | Parallel CSV processing without the overhead of multi-process or async complexity |
| **6-char short codes** | 62^6 ≈ 56B combinations — collision-resistant, short enough for URLs |
| **302 redirects (not 301)** | Browsers don't cache 302s, so every redirect can be tracked |
| **Docker multi-stage not used** | Single-stage build is simpler and fast enough (Python build deps removed via apt-get, but pip cache is cleared) |
| **docker-compose.prod.yml standalone** | Docker Compose merge semantics can be tricky with multiple files. A single prod file is simpler and more reliable |

---

## Common Pitfalls & Gotchas

| # | Issue | File | Fix |
|---|-------|------|-----|
| 1 | `stats` route has a timezone bug: `cutoff = datetime.now()` should be `datetime.now() - timedelta(hours=24)` | `app.py:335` | Use `datetime.now(timezone.utc) - timedelta(hours=24)` |
| 2 | Nginx `upstream` references hardcoded container name `url_shortner_app_1` | `nginx.conf:32` | Ensure `docker-compose.prod.yml` sets `container_name` to match, or use service name resolution |
| 3 | Tests use file-based SQLite (`sqlite:///urls.db`) instead of in-memory | `tests/test_app.py` | Override `DATABASE_URL=sqlite:///:memory:` in test setup to avoid cross-test pollution |
| 4 | CI/CD deploy.yml referenced in README but not present in repo | `README.md` | Create `.github/workflows/deploy.yml` or remove references |
| 5 | Deploy script uses hardcoded Docker network name `url_shortner_default` | `scripts/deploy.sh:40` | Use `docker network ls` to confirm the actual network name |
| 6 | Body `overflow: hidden` prevents scrolling when bulk results overflow | `static/style.css:10` | Change to `overflow: auto` or remove the property |
| 7 | Webhook server runs as root with full Docker socket access | `webhook_server.py` | Consider running in a Docker container with limited capabilities |
| 8 | Redis port 6379 exposed to host in dev compose but not needed externally | `docker-compose.yml:77` | Remove `ports` mapping for Redis if not needed for debugging |
| 9 | `ImagePullBackOff` on fresh VPS — Docker Hub image may not exist yet | `docker-compose.prod.yml:16` | Run CI/CD pipeline first, or build locally with `docker compose build` |
| 10 | SSL certs in repo are placeholders — will cause browser warnings | `nginx/ssl/` | Replace with real Let's Encrypt certificates |

---

*End of Line-by-Line Guide. Generated from the URL_shortner project by amin (@aminghuf).*
