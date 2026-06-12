#!/root/URL_shortner/scripts/.venv/bin/python3
"""Simple Flask webhook listener that redeploys on GitHub push events."""

import hmac
import hashlib
import logging
import os
import subprocess
import sys

from flask import Flask, request, jsonify

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("webhook")

DEPLOY_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "deploy.sh"
)
SECRET = os.environ.get("WEBHOOK_SECRET", "")


def verify_signature(payload: bytes, signature_header: str) -> bool:
    """Verify GitHub HMAC-SHA256 signature. Returns True if no secret is set."""
    if not SECRET:
        return True  # No secret configured — accept all
    if not signature_header:
        logger.warning("Missing signature header")
        return False
    expected = "sha256=" + hmac.new(
        SECRET.encode(), msg=payload, digestmod=hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.get_data()
    sig = request.headers.get("X-Hub-Signature-256", "")

    if not verify_signature(payload, sig):
        logger.warning("Invalid signature")
        return jsonify({"status": "unauthorized"}), 401

    event = request.headers.get("X-GitHub-Event", "")
    if event != "push":
        logger.info("Ignoring non-push event: %s", event)
        return jsonify({"status": "ignored", "event": event})

    body = request.get_json(silent=True) or {}
    ref = body.get("ref", "")
    if ref != "refs/heads/main":
        logger.info("Ignoring push to non-main branch: %s", ref)
        return jsonify({"status": "ignored", "ref": ref})

    logger.info("Received push to main — triggering deploy")
    try:
        result = subprocess.run(
            ["bash", DEPLOY_SCRIPT],
            capture_output=True,
            text=True,
            timeout=300,
        )
        logger.info("Deploy stdout:\n%s", result.stdout)
        if result.stderr:
            logger.warning("Deploy stderr:\n%s", result.stderr)
        if result.returncode != 0:
            logger.error("Deploy failed with exit code %d", result.returncode)
            return jsonify({"status": "failed", "exit_code": result.returncode}), 500
    except subprocess.TimeoutExpired:
        logger.error("Deploy timed out after 300s")
        return jsonify({"status": "timeout"}), 504
    except Exception as e:
        logger.exception("Deploy error: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({"status": "success"})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("WEBHOOK_PORT", 9999))
    host = os.environ.get("WEBHOOK_HOST", "0.0.0.0")
    logger.info("Starting webhook server on %s:%d", host, port)
    app.run(host=host, port=port, debug=False)
