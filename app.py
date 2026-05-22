from flask import Flask, redirect, request, render_template
import random
from string import ascii_letters, digits
import os
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL",
    "sqlite:///:memory:"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

class URLMapping(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    short_code = db.Column(db.String(10), unique=True, nullable=False)
    long_url = db.Column(db.Text, nullable=False)

with app.app_context():
    db.create_all()

@app.route("/health")
def health():
    return {"status": "ok"}, 200

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/shorten", methods=["POST"])
def shorten_url():
    data = request.get_json(silent=True) or {}
    long_url = data.get("url")

    if not long_url:
        return {"error": "URL is required"}, 400

    short_code = generate_short_code()
    while URLMapping.query.filter_by(short_code=short_code).first():
        short_code = generate_short_code()

    url_mapping = URLMapping(short_code=short_code, long_url=long_url)
    db.session.add(url_mapping)
    db.session.commit()

    return {
        "short_code": short_code,
        "short_url": f"http://localhost:8080/{short_code}"
    }, 201

@app.route("/<short_code>")
def redirect_to_url(short_code):
    url_mapping = URLMapping.query.filter_by(short_code=short_code).first()
    long_url = url_mapping.long_url if url_mapping else None
    if not long_url:
        return {"error": "URL not found"}, 404
    return redirect(long_url)

def generate_short_code():
    return ''.join(random.choices(ascii_letters + digits, k=6))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)