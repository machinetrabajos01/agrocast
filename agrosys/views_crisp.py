"""Public documentation of the six CRISP-ML phases and the public model demonstration."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd
from flask import abort, flash, redirect, render_template, request, send_from_directory, url_for

from src.inference import SERIES_LABELS, InferenceError, available_versions, forecast, recommend

from .services import record_event

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
CRISP = REPORTS / "crisp"
INDEX_CSV = ROOT / "data" / "processed" / "indice_mensual.csv"
SERIES_COLUMNS = {"total": "T_Índice", "fertilizantes": "T_Fertilizantes", "plaguicidas": "T_Plaguicidas"}
MAX_NEW_MONTHS = 3

PHASES = [
    ("business-data", "1", "Business & Data Understanding", "bi-compass"),
    ("data-engineering", "2", "Data Engineering", "bi-funnel"),
    ("model-engineering", "3", "Model Engineering", "bi-cpu"),
    ("evaluation", "4", "Model Evaluation", "bi-clipboard-data"),
    ("deployment", "5", "Model Deployment", "bi-rocket-takeoff"),
    ("monitoring", "6", "Monitoring & Maintenance", "bi-activity"),
]


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


@lru_cache(maxsize=1)
def _index() -> pd.DataFrame:
    return pd.read_csv(INDEX_CSV, parse_dates=["fecha"], index_col="fecha").asfreq("MS")


def _context() -> dict:
    metricas = pd.read_csv(REPORTS / "metricas.csv") if (REPORTS / "metricas.csv").exists() else pd.DataFrame()
    experiments = pd.read_csv(CRISP / "experiments.csv").fillna("") if (CRISP / "experiments.csv").exists() else pd.DataFrame()
    by_h = []
    if not metricas.empty:
        sub = metricas[metricas.horizonte > 0]
        for (m, b), g in sub.groupby(["modelo", "bloque"]):
            by_h.append({"model": m, "block": b, "mase": [round(v, 3) for v in g.sort_values("horizonte").mase]})
    return {
        "phases": PHASES,
        "ev": _json(CRISP / "evidence.json"),
        "quality": _json(REPORTS / "calidad_datos.json"),
        "mon": _json(CRISP / "monitoring_test.json"),
        "review": _json(CRISP / "domain_review.json"),
        "experiments": experiments.to_dict(orient="records"),
        "mase_by_h": by_h,
        "versions": available_versions(),
    }


def _demo_history(series: str, new_values: list[float]) -> pd.Series:
    y = _index()[SERIES_COLUMNS[series]].dropna()
    if new_values:
        months = pd.date_range(y.index[-1] + pd.DateOffset(months=1), periods=len(new_values), freq="MS")
        y = pd.concat([y, pd.Series(new_values, index=months)])
    return y


def register_routes(app):
    @app.route("/crisp-ml")
    def crisp_overview():
        return redirect(url_for("crisp_phase", phase=PHASES[0][0]))

    @app.route("/crisp-ml/<phase>")
    def crisp_phase(phase):
        slugs = [p[0] for p in PHASES]
        if phase not in slugs:
            abort(404)
        i = slugs.index(phase)
        ctx = _context()
        ctx.update(phase=PHASES[i], prev=PHASES[i - 1] if i else None, next=PHASES[i + 1] if i + 1 < len(PHASES) else None)
        if phase == "deployment":
            y = _index()[SERIES_COLUMNS["total"]].dropna()
            ctx.update(series_labels=SERIES_LABELS, cutoff=y.index[-1],
                       next_months=pd.date_range(y.index[-1] + pd.DateOffset(months=1), periods=MAX_NEW_MONTHS, freq="MS"),
                       last_values={k: round(float(_index()[c].dropna().iloc[-1]), 2) for k, c in SERIES_COLUMNS.items()},
                       demo=None, form={})
        return render_template(f"crisp/{phase.replace('-', '_')}.html", title=PHASES[i][2], **ctx)

    @app.route("/crisp-ml/deployment/demo", methods=["POST"])
    def crisp_demo():
        ctx = _context()
        i = 4
        ctx.update(phase=PHASES[i], prev=PHASES[i - 1], next=PHASES[i + 1])
        y_total = _index()[SERIES_COLUMNS["total"]].dropna()
        form = request.form.to_dict()
        series = form.get("series", "total")
        version = ctx["versions"][-1]
        demo, status = None, 200
        try:
            if series not in SERIES_COLUMNS:
                raise InferenceError("Choose one of the three index series.")
            horizon = int(form.get("horizon", "6"))
            new_values = []
            for k in range(MAX_NEW_MONTHS):
                raw = (form.get(f"new_{k}") or "").strip().replace(",", ".")
                if not raw:
                    break
                value = float(raw)
                new_values.append(value)
            last = float(_index()[SERIES_COLUMNS[series]].dropna().iloc[-1])
            for v in new_values:
                if not 0.5 * last <= v <= 2 * last:
                    raise InferenceError("New monthly values must be between 50% and 200% of the last official value.")
            result = forecast(_demo_history(series, new_values), series, horizon, version)
            demo = {"result": result, "decision": recommend(result), "new_values": new_values}
            record_event("demo.forecast", series=series, horizon=horizon, new_months=len(new_values),
                         model_version=version, change_pct=result["change_pct"], compute_ms=result["compute_ms"])
        except (InferenceError, ValueError) as exc:
            message = str(exc) if isinstance(exc, InferenceError) else "Values must be numbers (use a dot for decimals)."
            record_event("demo.rejected_input", "warning", status_code=422, reason=message[:200])
            flash(message, "danger")
            status = 422
        ctx.update(series_labels=SERIES_LABELS, cutoff=y_total.index[-1],
                   next_months=pd.date_range(y_total.index[-1] + pd.DateOffset(months=1), periods=MAX_NEW_MONTHS, freq="MS"),
                   last_values={k: round(float(_index()[c].dropna().iloc[-1]), 2) for k, c in SERIES_COLUMNS.items()},
                   demo=demo, form=form)
        return render_template("crisp/deployment.html", title=PHASES[i][2], **ctx), status

    @app.route("/crisp-ml/figure/<name>.png")
    def crisp_figure(name):
        if not name.replace("_", "").isalnum():
            abort(404)
        folder = CRISP / "figures"
        if not (folder / f"{name}.png").exists():
            abort(404)
        return send_from_directory(folder, f"{name}.png", max_age=3600)

    @app.route("/crisp-ml/download/<name>")
    def crisp_download(name):
        allowed = {"experiments.csv", "evidence.json", "monitoring_test.json", "monitoring_test_log.jsonl"}
        if name not in allowed:
            abort(404)
        return send_from_directory(CRISP, name, as_attachment=True)
