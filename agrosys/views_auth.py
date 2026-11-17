"""Registration, login, logout and user administration."""
from __future__ import annotations

from datetime import timedelta

from flask import abort, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import func

from .db import ROLES, EventLog, User, db, utcnow
from .security import (EMAIL_RE, hash_password, login_user, password_problems, permission_required,
                       safe_next, verify_password)
from .services import record_event

MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15
POLICY_VERSION = "2026-11"


def _recent_failures(email: str) -> int:
    """Failed sign-ins for this email in the last minutes, stored server-side (clearing cookies does not reset it)."""
    since = utcnow() - timedelta(minutes=LOCK_MINUTES)
    events = EventLog.query.filter(EventLog.event == "auth.failed_login", EventLog.created_at >= since).all()
    return sum(1 for e in events if (e.detail or {}).get("email") == email)


def register_routes(app):

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if g.get("user"):
            return redirect(url_for("dashboard"))
        form = request.form
        if request.method == "POST":
            name = form.get("full_name", "").strip()
            email = form.get("email", "").strip().lower()
            password = form.get("password", "")
            errors = []
            if not 3 <= len(name) <= 80:
                errors.append("Full name must have between 3 and 80 characters.")
            if not EMAIL_RE.match(email) or len(email) > 120:
                errors.append("Enter a valid email address.")
            problems = password_problems(password)
            if problems:
                errors.append("The password needs " + " and ".join(problems) + ".")
            if password != form.get("confirm", ""):
                errors.append("The passwords do not match.")
            if form.get("accept_policy") != "yes":
                errors.append("You must accept the privacy policy and the terms of use to create an account.")
            if User.query.filter(func.lower(User.email) == email).first():
                errors.append("An account with this email already exists.")
            if errors:
                for e in errors:
                    flash(e, "danger")
            else:
                # Business rule: self-registration always creates a producer account. Any "role"
                # field sent by the client is ignored; only an administrator can change roles.
                user = User(full_name=name, email=email, password_hash=hash_password(password),
                            role="producer", department=form.get("department", "").strip()[:60] or None,
                            consent_at=utcnow(), consent_version=POLICY_VERSION)
                db.session.add(user)
                db.session.commit()
                record_event("auth.register", user_id=user.id, policy_version=POLICY_VERSION)
                login_user(user)
                flash("Your producer account was created. Start by registering a farm.", "success")
                return redirect(url_for("farms"))
        return render_template("auth/register.html", form=form)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.get("user"):
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            failures = max(session.get("failed_logins", 0), _recent_failures(email))
            if failures >= MAX_FAILED_LOGINS:
                record_event("auth.locked", "warning", email=email)
                flash(f"Too many failed attempts. Try again in {LOCK_MINUTES} minutes.", "danger")
                return render_template("login.html"), 429
            user = User.query.filter(func.lower(User.email) == email).first()
            if user and user.is_active and verify_password(user, password):
                login_user(user)
                user.last_login_at = utcnow()
                db.session.commit()
                record_event("auth.login", role=user.role)
                return redirect(safe_next(request.args.get("next")) or url_for("dashboard"))
            session["failed_logins"] = failures + 1
            record_event("auth.failed_login", "warning", email=email,
                         reason="inactive" if user and not user.is_active else "credentials")
            flash("Invalid email or password.", "danger")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        if g.get("user"):
            record_event("auth.logout")
        session.clear()
        flash("You have signed out.", "info")
        return redirect(url_for("home"))

    @app.route("/admin/users")
    @permission_required("users.manage")
    def admin_users():
        role = request.args.get("role", "")
        q = User.query.order_by(User.created_at.desc())
        if role in ROLES:
            q = q.filter_by(role=role)
        counts = dict(db.session.query(User.role, func.count(User.id)).group_by(User.role).all())
        return render_template("admin/users.html", users=q.all(), roles=ROLES, counts=counts, role=role)

    @app.route("/admin/users/<int:user_id>", methods=["POST"])
    @permission_required("users.manage")
    def admin_user_update(user_id):
        user = db.session.get(User, user_id) or abort(404)
        if user.id == g.user.id:
            flash("Administrators cannot change their own role or status.", "danger")
            return redirect(url_for("admin_users"))
        new_role = request.form.get("role", user.role)
        if new_role not in ROLES:
            abort(400)
        active = request.form.get("is_active") == "1"
        changes = {}
        if new_role != user.role:
            changes["role"] = [user.role, new_role]
            user.role = new_role
        if active != user.is_active:
            changes["is_active"] = [user.is_active, active]
            user.is_active = active
        db.session.commit()
        if changes:
            record_event("admin.user_updated", target_user=user.id, changes=changes)
            flash(f"{user.full_name} was updated.", "success")
        return redirect(url_for("admin_users"))
