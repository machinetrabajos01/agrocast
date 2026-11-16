"""Business services: model access, inference with history, plan rules, monitoring and logging."""
from __future__ import annotations

import logging
import time
from datetime import date

import pandas as pd
from flask import g, request
from sqlalchemy import func

from src import inference as ml

from .db import (DriftCheck, EventLog, IndexObservation, Inference, ModelVersion, PurchasePlan, db)

log = logging.getLogger("agrocast")


class BusinessRuleError(ValueError):
    """A request that violates a business rule. The message is shown to the user."""


# ---------------------------------------------------------------- logging

def record_event(event: str, level: str = "info", status_code: int | None = None,
                 duration_ms: int | None = None, **detail):
    """Store an operational event. Logging must never break the request that produced it."""
    try:
        user = g.get("user")
        db.session.add(EventLog(
            level=level, event=event, user_id=user.id if user else None,
            method=request.method if request else None, path=request.path if request else None,
            status_code=status_code, duration_ms=duration_ms, detail=detail or {}))
        db.session.commit()
    except Exception:  # pragma: no cover - defensive
        db.session.rollback()
        log.exception("Could not store event %s", event)
    getattr(log, "error" if level == "error" else "info")("%s %s", event, detail)


# ---------------------------------------------------------------- model and data

def active_model() -> ModelVersion:
    if g.get("active_model") is not None:
        return g.active_model
    model = ModelVersion.query.filter_by(is_active=True).first()
    g.active_model = model
    if not model:
        raise BusinessRuleError("There is no active model version. An administrator must activate one.")
    return model


def index_history(series: str) -> pd.Series:
    rows = (db.session.query(IndexObservation.month, IndexObservation.value)
            .filter(IndexObservation.series == series).order_by(IndexObservation.month).all())
    return pd.Series([float(v) for _, v in rows], index=pd.to_datetime([m for m, _ in rows]), dtype=float)


def latest_index_month() -> date | None:
    return db.session.query(func.max(IndexObservation.month)).filter(IndexObservation.series == "total").scalar()


def months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month)


def first_of_month(value: str) -> date:
    try:
        y, m = value.split("-")[:2]
        return date(int(y), int(m), 1)
    except (ValueError, AttributeError):
        raise BusinessRuleError("Use the YYYY-MM format for months.")


def planning_window() -> tuple[date, date]:
    """Purchase months the model can support: after the last observed month, up to six months ahead."""
    lasts = [m for (m,) in db.session.query(func.max(IndexObservation.month)).group_by(IndexObservation.series).all()]
    if not lasts:
        raise BusinessRuleError("No index observations are registered yet.")
    last = min(lasts)  # every series must be able to cover the window
    start = (pd.Timestamp(last) + pd.DateOffset(months=1)).date()
    end = (pd.Timestamp(last) + pd.DateOffset(months=6)).date()
    return start, end


# ---------------------------------------------------------------- inference

def run_inference(series: str, horizon: int, source: str = "system_records",
                  manual_values: dict[str, float] | None = None, plan: PurchasePlan | None = None) -> Inference:
    """Run the active model on the server and store the inference with user, date and version."""
    started = time.perf_counter()
    model = active_model()
    history = index_history(series)
    if source == "manual_entry":
        if not manual_values:
            raise BusinessRuleError("Enter at least one monthly value for the scenario.")
        extra = pd.Series({pd.Timestamp(first_of_month(k)): float(v) for k, v in manual_values.items()})
        if (extra <= 0).any():
            raise BusinessRuleError("Scenario values must be positive index levels.")
        history = pd.concat([history[~history.index.isin(extra.index)], extra]).sort_index()
    try:
        result = ml.forecast(history, series, horizon, model.version)
    except ml.InferenceError as exc:
        record_event("inference.rejected", "warning", series=series, horizon=horizon, reason=str(exc))
        raise BusinessRuleError(str(exc)) from exc
    advice = ml.recommend(result)
    result["recommendation"] = advice
    elapsed = int((time.perf_counter() - started) * 1000)
    payload = {"series": series, "horizon": horizon, "source": source,
               "observations": len(history), "last_month": history.index[-1].strftime("%Y-%m"),
               "manual_values": manual_values or {}}
    if plan is not None:
        payload.update(plan_id=plan.id, category=plan.category, planned_month=plan.planned_month.strftime("%Y-%m"))
    inf = Inference(user_id=g.user.id if g.get("user") else None, plan_id=plan.id if plan else None,
                    model_version_id=model.id, series=series, horizon=horizon, input_source=source,
                    input_payload=payload, output_payload=result,
                    origin_month=first_of_month(result["origin_month"]),
                    target_month=first_of_month(result["target_month"]),
                    predicted_value=result["predicted_value"], change_pct=result["change_pct"],
                    recommendation=advice["code"], response_ms=elapsed)
    db.session.add(inf)
    db.session.commit()
    record_event("inference.completed", duration_ms=elapsed, inference_id=inf.id, series=series,
                 horizon=horizon, source=source, model_version=model.version, recommendation=advice["code"])
    return inf


def evaluate_plan(plan: PurchasePlan) -> Inference:
    """Business process step: evaluate a purchase plan with the model and store the recommendation."""
    if plan.status not in ("draft", "evaluated", "deferred"):
        raise BusinessRuleError("Only draft, evaluated or deferred plans can be evaluated.")
    if not plan.items:
        raise BusinessRuleError("Add at least one product before evaluating the plan.")
    start, end = planning_window()
    if not start <= plan.planned_month <= end:
        raise BusinessRuleError(f"The planned month must be between {start:%Y-%m} and {end:%Y-%m} "
                                "(the six months the model can forecast). Edit the plan first.")
    series = ml.CATEGORY_SERIES[plan.category]
    # The horizon is measured from the last observed month of the series that represents the plan.
    last = db.session.query(func.max(IndexObservation.month)).filter(IndexObservation.series == series).scalar()
    horizon = months_between(last, plan.planned_month)
    if not 1 <= horizon <= 6:
        raise BusinessRuleError(f"The {series} index is registered up to {last:%Y-%m}; the purchase month must be "
                                "one to six months after it. Ask an analyst to register the missing months.")
    inf = run_inference(series, horizon, plan=plan)
    plan.last_inference_id = inf.id
    plan.recommendation = inf.recommendation
    plan.status = "evaluated"
    plan.decision = None
    db.session.commit()
    return inf


def _active_model_id():
    try:
        return active_model().id
    except BusinessRuleError:
        return None


def plan_projection(plan: PurchasePlan) -> dict | None:
    inf = plan.last_inference
    if not inf:
        return None
    change = float(inf.change_pct)
    cost = plan.planned_cost
    out = inf.output_payload
    return {
        "change_pct": change,
        "projected_cost": cost * (1 + change / 100),
        "difference": cost * change / 100,
        "lo_cost": cost * (1 + out["change_lo80_pct"] / 100),
        "hi_cost": cost * (1 + out["change_hi80_pct"] / 100),
        "stale": inf.model_version_id != _active_model_id(),
    }


# ---------------------------------------------------------------- monitoring

def prediction_vs_actual(limit: int = 200) -> list[dict]:
    """Join stored inferences with the observed index once the target month becomes available."""
    rows = (db.session.query(Inference, IndexObservation.value)
            .join(IndexObservation, (IndexObservation.series == Inference.series)
                  & (IndexObservation.month == Inference.target_month))
            .order_by(Inference.created_at.desc()).limit(limit).all())
    out = []
    for inf, actual in rows:
        actual = float(actual)
        path = {p["month"]: p for p in inf.output_payload.get("path", [])}
        end = path.get(inf.target_month.strftime("%Y-%m"), {})
        out.append({
            "id": inf.id, "series": inf.series, "target_month": inf.target_month, "horizon": inf.horizon,
            "predicted": float(inf.predicted_value), "actual": actual,
            "error_pct": (float(inf.predicted_value) - actual) / actual * 100,
            "inside_80": end.get("lo80", 0) <= actual <= end.get("hi80", 0),
            "version": inf.model_version.version,
        })
    return out


def pending_outcomes() -> int:
    observed = db.session.query(IndexObservation.series, IndexObservation.month).subquery()
    return (db.session.query(func.count(Inference.id))
            .outerjoin(observed, (observed.c.series == Inference.series) & (observed.c.month == Inference.target_month))
            .filter(observed.c.month.is_(None)).scalar())


def run_drift_checks() -> list[DriftCheck]:
    model = active_model()
    compared = prediction_vs_actual(1000)
    checks = []
    for series in ml.SERIES_LABELS:
        result = ml.drift_check(index_history(series), series, model.version)
        errs = [abs(r["error_pct"]) for r in compared if r["series"] == series and r["version"] == model.version]
        mae = sum(errs) / len(errs) if errs else None
        if mae is not None and mae > 10 and result["status"] == "ok":
            result["status"] = "warning"
        result["mean_abs_error_pct"] = mae
        chk = DriftCheck(run_by=g.user.id, model_version_id=model.id, series=series, status=result["status"],
                         z_score=result["z_score"], volatility_ratio=result["volatility_ratio"],
                         mae_recent=mae, detail=result)
        db.session.add(chk)
        checks.append(chk)
    db.session.commit()
    record_event("monitoring.drift_check", statuses={c.series: c.status for c in checks})
    return checks
