"""HTTPS reverse-proxy backend; one process preserves in-memory job state."""
import os

if not os.environ.get("APP_PASSWORD") or not os.environ.get("APP_PUBLIC_ORIGIN", "").startswith("https://"):
    raise SystemExit("APP_PASSWORD와 https://공인IP 형식의 APP_PUBLIC_ORIGIN을 설정해 주세요.")

from app import app
from waitress import serve

if __name__ == "__main__":
    serve(app, host="127.0.0.1", port=8766, threads=8, max_request_body_size=12 * 1024 * 1024)
