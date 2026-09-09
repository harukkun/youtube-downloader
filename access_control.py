"""Optional password gate for the local app and its HTTPS deployment."""
import hashlib
import hmac
import os
import secrets
import threading
import time
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from flask import jsonify, redirect, render_template, request, session


def install_access_control(app):
    password = os.environ.get("APP_PASSWORD", "")
    public_origin = os.environ.get("APP_PUBLIC_ORIGIN", "").rstrip("/")
    if public_origin and (not password or not public_origin.startswith("https://")):
        raise ValueError("외부 실행에는 HTTPS APP_PUBLIC_ORIGIN과 APP_PASSWORD가 필요합니다.")
    app.config.update(SECRET_KEY=secrets.token_hex(32),
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=bool(public_origin),
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=12))
    if password:
        app.config["MAX_CONTENT_LENGTH"] = 1048576
    password_digest = hashlib.sha256(password.encode()).digest()
    attempts = {}
    lock = threading.Lock()

    def destination():
        target = request.args.get("next", "/")
        if not target.startswith("/") or target.startswith("//") or "\\" in target or any(ord(c) < 32 for c in target):
            return "/"
        return target

    @app.before_request
    def gate():
        if not password:
            return
        if public_origin and request.host != urlsplit(public_origin).netloc:
            return jsonify(error="허용되지 않은 주소입니다."), 400
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            # Browsers send Origin on form/fetch mutations; reject missing origins too.
            origin = public_origin or request.host_url.rstrip("/")
            if request.headers.get("Origin") != origin:
                return jsonify(error="페이지를 새로고침한 뒤 다시 시도해 주세요."), 403
        if request.endpoint in ("static", "favicon", "login"):
            return
        if not session.get("authenticated"):
            if request.path.startswith("/api/"):
                return jsonify(error="로그인이 필요합니다. 페이지를 새로고침해 주세요."), 401
            return redirect("/login?" + urlencode({"next": request.full_path.rstrip("?")}))

    @app.after_request
    def headers(response):
        if password:
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not password or session.get("authenticated"):
            return redirect(destination())
        error, status = None, 200
        if request.method == "POST":
            # Only the loopback-bound production server may use Caddy's overwritten header.
            client = request.headers.get("X-Real-IP", "unknown") if public_origin else request.remote_addr
            now = time.monotonic()
            with lock:
                for key in list(attempts):
                    if now - attempts[key][0] >= 300:
                        del attempts[key]
                started, count = attempts.get(client, (now, 0))
                if count >= 5 or (client not in attempts and len(attempts) >= 10000):
                    return render_template("login.html", error="시도 횟수를 초과했습니다. 5분 후 다시 시도해 주세요."), 429, {"Retry-After": "300"}
                attempts[client] = (started, count + 1)
            supplied = hashlib.sha256(request.form.get("password", "").encode()).digest()
            if hmac.compare_digest(supplied, password_digest):
                with lock:
                    attempts.pop(client, None)
                session.clear()
                session.permanent = True
                session["authenticated"] = True
                return redirect(destination())
            error, status = "비밀번호가 맞지 않습니다.", 401
        return render_template("login.html", error=error), status

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect("/login")
