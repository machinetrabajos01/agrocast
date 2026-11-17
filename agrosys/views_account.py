"""Legal pages and the rights of the data owner (Colombian Law 1581 of 2012): access, update, export and deletion."""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

from flask import Response, abort, flash, g, redirect, render_template, request, session, url_for

from .db import (DriftCheck, EventLog, Farm, Incident, IndexObservation, Inference, PurchasePlan, ShoppingList, User,
                 db, utcnow)
from .security import hash_password, login_required, password_problems, verify_password
from .services import record_event

LEGAL_PAGES = {"privacy": "Privacy and personal data policy", "terms": "Terms of use", "cookies": "Cookie policy"}
POLICY_UPDATED = date(2026, 11, 17)


def _plain(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _row(obj, skip=()):
    return {c.name: _plain(getattr(obj, c.key)) for c in obj.__table__.columns if c.name not in skip}


def export_user_data(user: User) -> dict:
    plans = PurchasePlan.query.filter_by(owner_id=user.id).all()
    lists = ShoppingList.query.filter_by(owner_id=user.id).all()
    return {
        "exported_at": utcnow().isoformat(),
        "controller": "AGROCAST (academic project, University of Cundinamarca)",
        "account": _row(user, skip=("password_hash",)),
        "farms": [_row(f) for f in Farm.query.filter_by(owner_id=user.id).all()],
        "purchase_plans": [{**_row(p), "items": [_row(i) for i in p.items]} for p in plans],
        "inferences": [_row(i) for i in Inference.query.filter_by(user_id=user.id).all()],
        "support_reports": [_row(i) for i in Incident.query.filter_by(reporter_id=user.id).all()],
        "shopping_lists": [{**_row(sl), "items": [{**_row(it), "product": it.product.name} for it in sl.items]}
                           for sl in lists],
        "activity_log": [_row(e) for e in EventLog.query.filter_by(user_id=user.id)
                         .order_by(EventLog.created_at.desc()).limit(1000).all()],
    }


def delete_user_account(user: User):
    """Removes the personal data of the user. Shared records keep no link to the person."""
    for plan in PurchasePlan.query.filter_by(owner_id=user.id).all():
        Inference.query.filter_by(plan_id=plan.id).update({Inference.plan_id: None})
        db.session.delete(plan)
    db.session.flush()
    Farm.query.filter_by(owner_id=user.id).delete()
    for sl in ShoppingList.query.filter_by(owner_id=user.id).all():
        db.session.delete(sl)
    Incident.query.filter_by(reporter_id=user.id).delete()
    Incident.query.filter_by(assigned_to=user.id).update({Incident.assigned_to: None})
    Inference.query.filter_by(user_id=user.id).update({Inference.user_id: None})
    EventLog.query.filter_by(user_id=user.id).update({EventLog.user_id: None})
    IndexObservation.query.filter_by(entered_by=user.id).update({IndexObservation.entered_by: None})
    DriftCheck.query.filter_by(run_by=user.id).update({DriftCheck.run_by: None})
    db.session.delete(user)
    db.session.commit()


def register_routes(app):
    @app.route("/legal/<page>")
    def legal(page):
        if page not in LEGAL_PAGES:
            abort(404)
        return render_template(f"legal/{page}.html", title=LEGAL_PAGES[page], updated=POLICY_UPDATED, pages=LEGAL_PAGES)

    @app.route("/account")
    @login_required
    def account():
        return render_template("account/index.html", title="My account")

    @app.route("/account/profile", methods=["POST"])
    @login_required
    def account_profile():
        name = " ".join((request.form.get("full_name") or "").split())
        department = " ".join((request.form.get("department") or "").split())[:60]
        if not 3 <= len(name) <= 80:
            flash("Full name must have between 3 and 80 characters.", "danger")
        else:
            g.user.full_name, g.user.department = name, department or None
            db.session.commit()
            record_event("account.updated")
            flash("Your data was updated.", "success")
        return redirect(url_for("account"))

    @app.route("/account/password", methods=["POST"])
    @login_required
    def account_password():
        current, new = request.form.get("current", ""), request.form.get("new", "")
        problems = password_problems(new)
        if not verify_password(g.user, current):
            flash("The current password is not correct.", "danger")
        elif problems:
            flash("The new password needs " + " and ".join(problems) + ".", "danger")
        elif new != request.form.get("confirm", ""):
            flash("The new passwords do not match.", "danger")
        else:
            g.user.password_hash = hash_password(new)
            db.session.commit()
            record_event("account.password_changed")
            flash("Password changed.", "success")
        return redirect(url_for("account"))

    @app.route("/account/export")
    @login_required
    def account_export():
        data = export_user_data(g.user)
        record_event("account.exported")
        return Response(json.dumps(data, ensure_ascii=False, indent=2), mimetype="application/json",
                        headers={"Content-Disposition": 'attachment; filename="agrocast_my_data.json"',
                                 "Cache-Control": "no-store"})

    @app.route("/account/delete", methods=["POST"])
    @login_required
    def account_delete():
        user = g.user
        if request.form.get("confirm_text", "").strip().upper() != "DELETE" or not verify_password(user, request.form.get("password", "")):
            flash("Type DELETE and your current password to confirm.", "danger")
            return redirect(url_for("account"))
        if user.role == "admin" and User.query.filter_by(role="admin", is_active=True).count() <= 1:
            flash("You are the only administrator. Assign another administrator before deleting your account.", "danger")
            return redirect(url_for("account"))
        role = user.role
        delete_user_account(user)
        g.user = None
        session.clear()
        record_event("account.deleted", role=role)
        flash("Your account and personal data were deleted.", "info")
        return redirect(url_for("home"))
