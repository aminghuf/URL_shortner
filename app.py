"""
Production-Grade Flask URL Shortener
-------------------------------------
PostgreSQL backend (SQLAlchemy), Redis caching, async bulk import with
ThreadPoolExecutor, transaction-wrapped batch inserts, click tracking,
rate limiting, CORS, and health check endpoints.
"""

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

# ---------------------------------------------------------------------------
# App & Configuration
# ---------------------------------------------------------------------------

app = Flask(__name__, template_folder="templates")

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///urls.db")
# Fix postgres:// → postgresql:// for SQLAlchemy 1.x+/psycopg2 compat
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_size": 10,
    "max_overflow": 20,
}

# Redis
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Rate limiting config
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))    # seconds
RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", "100"))          # requests per window

# Bulk import worker pool
MAX_WORKERS = int(os.getenv("BULK_IMPORT_WORKERS", "4"))

# Short code alphabet
SHORT_CODE_ALPHABET = string.ascii_letters + string.digits
SHORT_CODE_LENGTH = 6

# CORS – allow all origins by default; restrict in production
CORS(app)

db = SQLAlchemy(app)

# ---------------------------------------------------------------------------
# Redis Client (gracefully absent if unreachable)
# ---------------------------------------------------------------------------

_redis_client: redis_lib.Redis | None = None


def get_redis() -> redis_lib.Redis | None:
    """Return the shared Redis client, or None if Redis is unavailable."""
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = redis_lib.from_url(
                REDIS_URL, decode_responses=True, socket_connect_timeout=2, socket_timeout=2
            )
            _redis_client.ping()
        except Exception:
            _redis_client = None
    return _redis_client


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class URLMapping(db.Model):
    __tablename__ = "url_mappings"

    id = db.Column(db.Integer, primary_key=True)
    short_code = db.Column(db.String(10), unique=True, nullable=False, index=True)
    long_url = db.Column(db.Text, nullable=False)
    click_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    clicks = db.relationship("Click", backref="url_mapping", lazy="dynamic", cascade="all, delete-orphan")


class Click(db.Model):
    __tablename__ = "clicks"

    id = db.Column(db.Integer, primary_key=True)
    url_mapping_id = db.Column(db.Integer, db.ForeignKey("url_mappings.id"), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    user_agent = db.Column(db.Text, nullable=True)
    referrer = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)


with app.app_context():
    db.create_all()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def generate_short_code(length: int = SHORT_CODE_LENGTH) -> str:
    """Generate a random 6-character alphanumeric short code."""
    return "".join(random.choices(SHORT_CODE_ALPHABET, k=length))


def normalize_url(url: str) -> str:
    """Ensure URL has a scheme; default to https."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def is_valid_url(url: str) -> bool:
    """Basic URL validation."""
    return bool(re.match(r"^https?://[^\s/$.?#].[^\s]*$", url, re.IGNORECASE))


# ---------------------------------------------------------------------------
# Rate Limiting (in-memory sliding window per IP)
# ---------------------------------------------------------------------------

_rate_store: dict[str, list[float]] = {}


def _clean_rate_store() -> None:
    """Evict stale entries from the in-memory rate store."""
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW
    stale_ips = [ip for ip, timestamps in list(_rate_store.items()) if not timestamps or timestamps[-1] < cutoff]
    for ip in stale_ips:
        del _rate_store[ip]


def is_rate_limited(ip: str) -> bool:
    """Return True if the IP has exceeded the rate limit."""
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW

    if ip not in _rate_store:
        _rate_store[ip] = []

    # Keep only timestamps inside the current window
    _rate_store[ip] = [ts for ts in _rate_store[ip] if ts > cutoff]

    if len(_rate_store[ip]) >= RATE_LIMIT_MAX:
        return True

    _rate_store[ip].append(now)
    return False


# ---------------------------------------------------------------------------
# Routes – Health & Home
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    """Simple health check (legacy)."""
    return jsonify({"status": "ok"}), 200


@app.route("/api/health")
def api_health():
    """Kubernetes-ready health probe with dependency checks."""
    db_ok = False
    redis_ok = False
    try:
        db.session.execute(db.select(func.now()))
        db_ok = True
    except Exception:
        db_ok = False

    r = get_redis()
    try:
        if r is not None:
            r.ping()
            redis_ok = True
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
    """Render the main landing page."""
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Routes – Shorten
# ---------------------------------------------------------------------------

@app.route("/shorten", methods=["POST"])
def shorten_url():
    """Create a short URL from a JSON payload with url field."""
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

    # Check if URL already exists (avoid duplicates)
    existing = URLMapping.query.filter_by(long_url=long_url).first()
    if existing:
        return jsonify({
            "short_code": existing.short_code,
            "short_url": request.host_url.rstrip("/") + "/" + existing.short_code,
            "created": False,
        }), 200

    # Generate unique short code
    short_code = generate_short_code()
    while URLMapping.query.filter_by(short_code=short_code).first():
        short_code = generate_short_code()

    url_mapping = URLMapping(short_code=short_code, long_url=long_url)
    db.session.add(url_mapping)
    db.session.commit()

    # Cache in Redis for fast redirect lookups
    r = get_redis()
    if r is not None:
        try:
            r.setex(f"url:{short_code}", 86400, long_url)  # 24h TTL
        except Exception:
            pass

    return jsonify({
        "short_code": short_code,
        "short_url": request.host_url.rstrip("/") + "/" + short_code,
        "created": True,
    }), 201


# ---------------------------------------------------------------------------
# Routes – Redirect (with click tracking)
# ---------------------------------------------------------------------------

@app.route("/<short_code>")
def redirect_to_url(short_code):
    """Redirect short code to its long URL, tracking the click."""
    # 1. Try Redis cache first
    r = get_redis()
    long_url = None
    if r is not None:
        try:
            long_url = r.get(f"url:{short_code}")
        except Exception:
            pass

    # 2. Fall back to database
    if not long_url:
        url_mapping = URLMapping.query.filter_by(short_code=short_code).first()
        if not url_mapping:
            return jsonify({"error": "URL not found"}), 404
        long_url = url_mapping.long_url

        # Populate cache for next time
        if r is not None:
            try:
                r.setex(f"url:{short_code}", 86400, long_url)
            except Exception:
                pass
    else:
        # If we got it from cache, still need the DB row for click tracking
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


# ---------------------------------------------------------------------------
# Routes – Stats
# ---------------------------------------------------------------------------

@app.route("/stats/<short_code>")
def stats(short_code):
    """Return click statistics for a short code."""
    url_mapping = URLMapping.query.filter_by(short_code=short_code).first()
    if not url_mapping:
        return jsonify({"error": "URL not found"}), 404

    total_clicks = url_mapping.click_count

    # Recent clicks (last 24h)
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


# ---------------------------------------------------------------------------
# Routes – Bulk Import (CSV / Text upload with worker pool)
# ---------------------------------------------------------------------------

def _validate_and_prepare_url(row: dict) -> dict | None:
    """Validate a single row and return a mapping dict or None."""
    url = (row.get("url") or row.get("long_url") or "").strip()
    if not url:
        return None
    url = normalize_url(url)
    if not is_valid_url(url):
        return None
    code = generate_short_code()
    return {"short_code": code, "long_url": url}


def _generate_unique_code(code_set: set) -> str:
    """Generate a short code that isn't in the reserved set."""
    code = generate_short_code()
    while code in code_set:
        code = generate_short_code()
    return code


def _process_urls_chunk(chunk: list[dict], existing_codes: set) -> list[dict]:
    """Worker: validate URLs and assign unique short codes."""
    results = []
    local_codes = set(existing_codes)  # copy
    for row in chunk:
        url = (row.get("url") or row.get("long_url") or "").strip()
        if not url:
            continue
        url = normalize_url(url)
        if not is_valid_url(url):
            continue
        code = _generate_unique_code(local_codes)
        local_codes.add(code)
        results.append({"short_code": code, "long_url": url})
    return results


@app.route("/bulk-import", methods=["POST"])
def bulk_import():
    """
    Accept a multipart upload (CSV or plain text, one URL per line).
    Processes URLs concurrently using a ThreadPoolExecutor and performs
    transaction-wrapped batch inserts.
    """
    client_ip = request.remote_addr or "unknown"
    if is_rate_limited(client_ip):
        return jsonify({"error": "Rate limit exceeded. Try again later."}), 429

    # Read the uploaded file
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file uploaded. Use form field 'file'."}), 400

    filename = file.filename or ""
    raw_text = file.read().decode("utf-8", errors="replace")

    # Parse the content
    rows: list[dict] = []
    if filename.endswith(".csv"):
        reader = csv.DictReader(io.StringIO(raw_text))
        for row in reader:
            rows.append(row)
    else:
        # Treat as plain text, one URL per line
        for line in raw_text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                rows.append({"url": line})

    if not rows:
        return jsonify({"error": "No valid URLs found in the uploaded file."}), 400

    total = len(rows)

    # Gather existing short codes from DB to avoid collisions
    existing_codes: set[str] = set()
    try:
        result = db.session.execute(db.select(URLMapping.short_code))
        existing_codes = {row[0] for row in result}
    except Exception:
        pass  # If query fails, we'll still generate unique codes via DB check

    # Split rows into chunks for the worker pool
    chunk_size = max(1, len(rows) // MAX_WORKERS)
    chunks = [rows[i : i + chunk_size] for i in range(0, len(rows), chunk_size)]

    processed: list[dict] = []
    errors: list[str] = []

    # Process with ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_process_urls_chunk, chunk, existing_codes): chunk for chunk in chunks}
        for future in as_completed(futures):
            try:
                result = future.result()
                processed.extend(result)
            except Exception as exc:
                errors.append(f"Worker error: {exc}")

    if not processed:
        return jsonify({
            "error": "No valid URLs could be processed.",
            "total_received": total,
            "errors": errors[:10],
        }), 400

    # Deduplicate by long_url within this batch
    seen: dict[str, str] = {}
    unique_batch: list[dict] = []
    for item in processed:
        if item["long_url"] not in seen:
            seen[item["long_url"]] = item["short_code"]
            unique_batch.append(item)

    # Check against existing DB URLs
    existing_urls: set[str] = set()
    try:
        result = db.session.execute(db.select(URLMapping.long_url))
        existing_urls = {row[0] for row in result}
    except Exception:
        pass

    to_insert = [item for item in unique_batch if item["long_url"] not in existing_urls]

    if not to_insert:
        return jsonify({
            "message": "All URLs already exist.",
            "total_received": total,
            "inserted": 0,
        }), 200

    # Transaction-wrapped batch insert
    inserted_count = 0
    try:
        db.session.bulk_insert_mappings(URLMapping, to_insert)
        db.session.commit()
        inserted_count = len(to_insert)

        # Populate Redis cache for new entries
        r = get_redis()
        if r is not None:
            try:
                pipe = r.pipeline()
                for item in to_insert:
                    pipe.setex(f"url:{item['short_code']}", 86400, item["long_url"])
                pipe.execute()
            except Exception:
                pass
    except Exception as exc:
        db.session.rollback()
        return jsonify({
            "error": "Database insert failed",
            "detail": str(exc),
            "total_received": total,
        }), 500

    return jsonify({
        "message": f"Successfully imported {inserted_count} URL(s).",
        "total_received": total,
        "inserted": inserted_count,
        "duplicates_skipped": len(rows) - len(to_insert),
        "errors": errors[:10] if errors else [],
    }), 201
