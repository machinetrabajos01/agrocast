"""Analyst and administrator routes: index records, forecast lab, monitoring, model versions,
operational logs, indicators and the authenticated inference API."""
from __future__ import annotations

import csv
import io
import statistics
from datetime import timedelta

from flask import Response, abort, flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func

from src import inference as ml

from .db import (CATEGORY_LABELS, DECISION_LABELS, DriftCheck, EventLog, Farm, IndexObservation, Incident,
                 Inference, ModelVersion, PurchasePlan, User, db, utcnow)
from .security import can, permission_required
from .services import (BusinessRuleError, active_model, first_of_month, latest_index_month, pending_outcomes,
                       prediction_vs_actual, record_event, run_drift_checks, run_inference)


def register_routes(app):

    # ------------------------------------------------------------ index records (CRUD)

    @app.route("/data/index", methods=["GET", "POST"])
    @permission_required("data.manage")
    def index_records():
        series = request.args.get("series", "total")
        if series not in ml.SERIES_LABELS:
            series = "total"
        if request.method == "POST":
            f = request.form
            try:
                s = f.get("series")
                if s not in ml.SERIES_LABELS:
                    raise BusinessRuleError("Choose a valid series.")
                month = first_of_month(f.get("month", ""))
                value = float(f.get("value", "0"))
                if not 1 <= value <= 10000:
                    raise BusinessRuleError("The index value must be between 1 and 10,000.")
                last = db.session.query(func.max(IndexObservation.month)).filter_by(series=s).scalar()
                if last and month > last and (month.year - last.year) * 12 + month.month - last.month > 1:
                    raise BusinessRuleError(f"Register months in order: the next expected month is after {last:%Y-%m}.")
                row = db.session.get(IndexObservation, (s, month))
                action = "updated" if row else "created"
                if row:
                    row.value, row.source, row.entered_by = value, (f.get("source") or "UPRA")[:60], g.user.id
                else:
                    db.session.add(IndexObservation(series=s, month=month, value=value,
                                                    source=(f.get("source") or "UPRA")[:60], entered_by=g.user.id))
                db.session.commit()
                record_event(f"data.index_{action}", series=s, month=month.isoformat(), value=value)
                flash(f"Observation {s} {month:%Y-%m} {action}.", "success")
                return redirect(url_for("index_records", series=s))
            except (BusinessRuleError, ValueError) as e:
                db.session.rollback()
                flash(str(e) if isinstance(e, BusinessRuleError) else "The value must be a number.", "danger")
        rows = (IndexObservation.query.filter_by(series=series)
                .order_by(IndexObservation.month.desc()).limit(36).all())
        totals = dict(db.session.query(IndexObservation.series, func.count()).group_by(IndexObservation.series).all())
        last = latest_index_month()
        next_month = f"{last.year + last.month // 12}-{last.month % 12 + 1:02d}" if last else ""
        return render_template("analyst/index_records.html", rows=rows, series=series, labels=ml.SERIES_LABELS,
                               totals=totals, last=last, next_month=next_month)

    @app.route("/data/index/<series>/<month>/delete", methods=["POST"])
    @permission_required("data.manage")
    def index_record_delete(series, month):
        row = db.session.get(IndexObservation, (series, first_of_month(month))) or abort(404)
        last = db.session.query(func.max(IndexObservation.month)).filter_by(series=series).scalar()
        if row.month != last:
            flash("Only the most recent month can be deleted, so the series stays continuous.", "danger")
        else:
            db.session.delete(row)
            db.session.commit()
            record_event("data.index_deleted", "warning", series=series, month=month)
            flash("Observation deleted.", "success")
        return redirect(url_for("index_records", series=series))

    # ------------------------------------------------------------ forecast lab

    @app.route("/forecast/lab", methods=["GET", "POST"])
    @permission_required("inference.lab")
    def forecast_lab():
        result = None
        last = latest_index_month()
        if request.method == "POST":
            f = request.form
            try:
                series = f.get("series")
                if series not in ml.SERIES_LABELS:
                    raise BusinessRuleError("Choose a valid series.")
                horizon = int(f.get("horizon", 6))
                source = "manual_entry" if f.get("source") == "manual_entry" else "system_records"
                manual = {}
                if source == "manual_entry":
                    for i in range(1, 4):
                        m, v = f.get(f"m{i}", "").strip(), f.get(f"v{i}", "").strip()
                        if m and v:
                            manual[first_of_month(m).strftime("%Y-%m")] = float(v)
                result = run_inference(series, horizon, source, manual or None)
                flash("Inference completed and stored in the history.", "success")
            except (BusinessRuleError, ValueError) as e:
                db.session.rollback()
                flash(str(e) if isinstance(e, BusinessRuleError) else "Check the numeric values.", "danger")
        return render_template("analyst/forecast_lab.html", result=result, labels=ml.SERIES_LABELS, last=last,
                               decision_labels=DECISION_LABELS, model=ModelVersion.query.filter_by(is_active=True).first())

    @app.route("/api/v1/forecast", methods=["POST"])
    @permission_required("inference.lab")
    def api_forecast():
        data = request.get_json(silent=True) or {}
        try:
            series = data.get("series", "total")
            if series not in ml.SERIES_LABELS:
                raise BusinessRuleError("series must be total, fertilizantes or plaguicidas.")
            inf = run_inference(series, int(data.get("horizon", 6)),
                                "manual_entry" if data.get("manual_values") else "system_records",
                                data.get("manual_values"))
            return jsonify({"inference_id": inf.id, **inf.output_payload})
        except (BusinessRuleError, ValueError, TypeError) as e:
            db.session.rollback()
            return jsonify({"error": str(e)}), 422

    # ------------------------------------------------------------ monitoring

    @app.route("/monitoring", methods=["GET", "POST"])
    @permission_required("monitoring.view")
    def monitoring():
        if request.method == "POST":
            try:
                run_drift_checks()
                flash("Input drift and performance checks completed.", "success")
            except BusinessRuleError as e:
                flash(str(e), "danger")
            return redirect(url_for("monitoring"))
        since = utcnow() - timedelta(days=30)
        times = [r[0] for r in db.session.query(Inference.response_ms).filter(Inference.created_at >= since).all()]
        stats = {
            "inferences_30d": len(times),
            "avg_ms": round(statistics.mean(times), 1) if times else None,
            "p95_ms": round(sorted(times)[int(0.95 * (len(times) - 1))], 1) if times else None,
            "errors_30d": EventLog.query.filter(EventLog.level == "error", EventLog.created_at >= since).count(),
            "rejected_30d": EventLog.query.filter(EventLog.event == "inference.rejected", EventLog.created_at >= since).count(),
            "pending": pending_outcomes(),
        }
        compared = prediction_vs_actual()
        if compared:
            stats["mape"] = round(statistics.mean(abs(r["error_pct"]) for r in compared), 2)
            stats["coverage80"] = round(100 * sum(r["inside_80"] for r in compared) / len(compared), 1)
        checks = DriftCheck.query.order_by(DriftCheck.created_at.desc()).limit(12).all()
        by_day = (db.session.query(func.date(Inference.created_at), func.count(), func.avg(Inference.response_ms))
                  .group_by(func.date(Inference.created_at)).order_by(func.date(Inference.created_at).desc()).limit(14).all())
        return render_template("analyst/monitoring.html", stats=stats, compared=compared[:50], checks=checks,
                               by_day=list(reversed(by_day)), labels=ml.SERIES_LABELS)

    # ------------------------------------------------------------ indicators and reports

    @app.route("/indicators")
    @permission_required("plans.view_all")
    def indicators():
        plans = PurchasePlan.query.all()
        status_counts = dict(db.session.query(PurchasePlan.status, func.count()).group_by(PurchasePlan.status).all())
        decided = [p for p in plans if p.decision]
        follow = sum(p.decision == p.recommendation for p in decided)
        rec_counts = dict(db.session.query(Inference.recommendation, func.count()).group_by(Inference.recommendation).all())
        closed = [p for p in plans if p.status == "purchased" and p.actual_cost is not None]
        savings = sum(p.planned_cost - p.actual_cost for p in closed)
        by_category = {}
        for p in plans:
            c = by_category.setdefault(p.category, {"plans": 0, "planned": 0.0})
            c["plans"] += 1
            c["planned"] += p.planned_cost
        return render_template("analyst/indicators.html", total_plans=len(plans), status_counts=status_counts,
                               follow_rate=round(100 * follow / len(decided), 1) if decided else None,
                               decided=len(decided), rec_counts=rec_counts, closed=len(closed), savings=savings,
                               by_category=by_category, category_labels=CATEGORY_LABELS,
                               decision_labels=DECISION_LABELS, users=User.query.count(), farms=Farm.query.count(),
                               open_incidents=Incident.query.filter(Incident.status.in_(("open", "in_progress"))).count())

    @app.route("/history.csv")
    @permission_required("inference.view_all")
    def history_csv():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "created_at", "user", "plan_id", "model_version", "series", "horizon", "source",
                    "origin_month", "target_month", "predicted_value", "change_pct", "recommendation", "response_ms"])
        for r in Inference.query.order_by(Inference.created_at.desc()).all():
            w.writerow([r.id, r.created_at.isoformat(), r.user.email if r.user else "", r.plan_id or "",
                        r.model_version.version, r.series, r.horizon, r.input_source, r.origin_month,
                        r.target_month, r.predicted_value, r.change_pct, r.recommendation, r.response_ms])
        record_event("report.inference_csv")
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=inference_history.csv"})

    # ------------------------------------------------------------ model versions

    @app.route("/admin/models", methods=["GET", "POST"])
    @permission_required("models.manage")
    def admin_models():
        if request.method == "POST":
            version = request.form.get("version", "")
            target = ModelVersion.query.filter_by(version=version).first() or abort(404)
            try:
                ml.load_artifact(target.version)
            except ml.InferenceError as e:
                flash(str(e), "danger")
                return redirect(url_for("admin_models"))
            ModelVersion.query.update({ModelVersion.is_active: False})
            db.session.flush()
            target.is_active = True
            db.session.commit()
            record_event("model.activated", version=version)
            flash(f"Model version {version} is now serving predictions.", "success")
            return redirect(url_for("admin_models"))
        versions = ModelVersion.query.order_by(ModelVersion.created_at.desc()).all()
        usage = dict(db.session.query(Inference.model_version_id, func.count()).group_by(Inference.model_version_id).all())
        return render_template("admin/models.html", versions=versions, usage=usage,
                               artifacts=ml.available_versions())

    # ------------------------------------------------------------ operational log

    @app.route("/admin/logs")
    @permission_required("logs.view")
    def admin_logs():
        level = request.args.get("level", "")
        q = EventLog.query
        if level in ("info", "warning", "error"):
            q = q.filter_by(level=level)
        rows = q.order_by(EventLog.created_at.desc()).limit(200).all()
        counts = dict(db.session.query(EventLog.level, func.count()).group_by(EventLog.level).all())
        return render_template("admin/logs.html", rows=rows, level=level, counts=counts)
