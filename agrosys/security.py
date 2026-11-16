"""Authentication, role-based authorization and CSRF protection (all enforced on the server)."""
from __future__ import annotations

import hmac
import re
import secrets
from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import User, db

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)

# Permission matrix. Each operational route declares the permission it needs.
PERMISSIONS = {
    "market.view": {"admin", "analyst", "producer"},
    "farms.manage_own": {"producer", "admin"},
    "plans.manage_own": {"producer", "admin"},
    "plans.view_all": {"admin", "analyst"},
    "inference.plan": {"producer", "admin"},
    "inference.lab": {"analyst", "admin"},
    "inference.view_all": {"analyst", "admin"},
    "data.manage": {"analyst", "admin"},
    "monitoring.view": {"analyst", "admin"},
    "incidents.report": {"admin", "analyst", "producer"},
    "incidents.manage": {"analyst", "admin"},
    "users.manage": {"admin"},
    "models.manage": {"admin"},
    "logs.view": {"admin"},
}


def hash_password(raw: str) -> str:
    return generate_password_hash(raw, method="scrypt")


def verify_password(user: User, raw: str) -> bool:
    return check_password_hash(user.password_hash, raw)


def password_problems(raw: str) -> list[str]:
    problems = []
    if len(raw) < 8:
        problems.append("at least 8 characters")
    if not re.search(r"[A-Za-z]", raw) or not re.search(r"\d", raw):
        problems.append("letters and numbers")
    return problems


def can(permission: str, user: User | None = None) -> bool:
    user = user if user is not None else g.get("user")
    return bool(user and user.is_active and user.role in PERMISSIONS.get(permission, set()))


def load_current_user():
    g.user = None
    uid = session.get("uid")
    if uid:
        user = db.session.get(User, uid)
        if user and user.is_active and session.get("role") == user.role:
            g.user = user
        else:
            # Role changed or account disabled: the session is no longer valid.
            session.clear()


def login_user(user: User):
    session.clear()
    session["uid"] = user.id
    session["role"] = user.role
    session.permanent = True


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not g.get("user"):
            flash("Please sign in to access the platform.", "warning")
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapped


def permission_required(permission: str):
    def decorator(fn):
        @wraps(fn)
        @login_required
        def wrapped(*args, **kwargs):
            if not can(permission):
                abort(403)
            return fn(*args, **kwargs)
        return wrapped
    return decorator


def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def check_csrf():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token", "")
        if not sent or not hmac.compare_digest(sent, session.get("_csrf", "")):
            abort(400, description="Invalid or missing form token. Reload the page and try again.")


def safe_next(target: str | None) -> str | None:
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return None
