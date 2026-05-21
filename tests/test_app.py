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

    response = client.post(
        "/shorten",
        json={"url": "https://google.com"}
    )

    assert response.status_code == 201

    data = response.get_json()

    assert "short_code" in data
    assert "short_url" in data

def test_shorten_missing_url():
    client = app.test_client()

    response = client.post(
        "/shorten",
        json={}
    )

    assert response.status_code == 400

def test_redirect_to_url():
    client = app.test_client()

    # First, create a short URL
    response = client.post(
        "/shorten",
        json={"url": "https://google.com"}
    )

    assert response.status_code == 201

    data = response.get_json()
    short_code = data["short_code"]

    # Now, test the redirection
    response = client.get(f"/{short_code}")

    assert response.status_code == 302
    assert response.headers["Location"] == "https://google.com"