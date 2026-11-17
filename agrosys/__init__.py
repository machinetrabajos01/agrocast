"""AGROCAST web information system: persistence, security, business processes and monitoring."""
from __future__ import annotations

import logging
import os
import secrets
import time
from datetime import timedelta

from flask import g, render_template, request
from markupsafe import Markup
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from .db import DATABASE_URL, IS_POSTGRES, db
from .security import PERMISSIONS, can, check_csrf, csrf_token, load_current_user


def init_system(app):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sqlite_path = os.getenv("SQLITE_PATH") or os.path.join(root, "instance", "agrocast.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL or "sqlite:///" + sqlite_path
    if IS_POSTGRES:
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 280,
                                                   "pool_size": 5, "max_overflow": 2}
    else:
        os.makedirs(os.path.join(root, "instance"), exist_ok=True)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "0") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    if os.getenv("RENDER"):
        # Render terminates TLS in its proxy; trust one hop so request.is_secure and redirects use https.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db.init_app(app)

    from . import pgc, seed, views_account, views_auth, views_crisp, views_monitor, views_ops, views_prices
    from .services import record_event
    views_auth.register_routes(app)
    views_ops.register_routes(app)
    views_monitor.register_routes(app)
    views_crisp.register_routes(app)
    views_prices.register_routes(app)
    views_account.register_routes(app)
    pgc.register_routes(app)
    seed.register_commands(app)

    @app.before_request
    def _before():
        g.started = time.perf_counter()
        g.csp_nonce = secrets.token_urlsafe(16)
        if request.endpoint == "static":
            return
        load_current_user()
        check_csrf()

    @app.after_request
    def _after(response):
        if request.endpoint != "static" and "started" in g:
            elapsed = int((time.perf_counter() - g.started) * 1000)
            response.headers["X-Response-Time-ms"] = str(elapsed)
            # Slow requests are stored so response times can be reviewed in the operational log.
            if elapsed > 3000 and response.status_code < 500:
                record_event("http.slow_request", "warning", status_code=response.status_code, duration_ms=elapsed)
        nonce = g.get("csp_nonce", "")
        response.headers.setdefault("Content-Security-Policy", (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
            "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'none'"))
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if g.get("user") and request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"   # personal pages are not kept in shared caches
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    @app.errorhandler(HTTPException)
    def _http_error(exc):
        if exc.code in (403, 404, 400, 429):
            if exc.code == 403:
                record_event("auth.forbidden", "warning", status_code=403)
            return render_template("error.html", code=exc.code, message=exc.description), exc.code
        return exc

    @app.errorhandler(Exception)
    def _server_error(exc):
        db.session.rollback()
        elapsed = int((time.perf_counter() - g.get("started", time.perf_counter())) * 1000)
        app.logger.exception("Unhandled error")
        record_event("http.error", "error", status_code=500, duration_ms=elapsed,
                     error=type(exc).__name__, message=str(exc)[:500])
        return render_template("error.html", code=500,
                               message="An unexpected error occurred. It was logged; please report it in Support."), 500

    @app.context_processor
    def _inject():
        return {"current_user": g.get("user"), "can": can, "csrf_token": csrf_token, "permissions": PERMISSIONS,
                "csp_nonce": g.get("csp_nonce", ""),
                "csrf_field": lambda: Markup(f'<input type="hidden" name="_csrf" value="{csrf_token()}">')}
