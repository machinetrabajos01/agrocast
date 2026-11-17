"""AGROCAST — web information system for agricultural input purchase planning.

Public pages present the project documentation (problem, CRISP-ML(Q), models and system design).
Operational modules (farms, purchase plans, model inference, monitoring and administration)
require authentication and are authorized by role on the server.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

import pandas as pd  # noqa: E402
from flask import Flask, abort, g, jsonify, render_template, request  # noqa: E402

from agrosys import init_system  # noqa: E402
from agrosys.db import DECISION_LABELS, Farm, FoodProduct, Incident, Inference, PurchasePlan, db  # noqa: E402
from agrosys.prices import latest_daily_date  # noqa: E402
from agrosys.security import can, permission_required  # noqa: E402
from agrosys.services import latest_index_month, plan_projection, record_event  # noqa: E402

REPORTS = ROOT / "reports"
DATA = ROOT / "data" / "processed"

app = Flask(__name__, template_folder="web/templates", static_folder="web/static")
secret = os.getenv("FLASK_SECRET_KEY")
if not secret and os.getenv("RENDER"):
    raise RuntimeError("FLASK_SECRET_KEY must be configured in the deployed environment.")
if not os.getenv("DATABASE_URL", "").strip() and os.getenv("RENDER"):
    raise RuntimeError("DATABASE_URL must be configured in the deployed environment.")
app.secret_key = secret or "local-development-only-key"
init_system(app)


def load_results() -> dict:
    path = REPORTS / "resultados.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_products() -> pd.DataFrame:
    path = DATA / "producto_resumen.csv"
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def load_prices() -> pd.DataFrame:
    path = DATA / "sipsa_precios.csv"
    return pd.read_csv(path, parse_dates=["fecha"]) if path.exists() else pd.DataFrame()


@app.context_processor
def inject_globals():
    return {
        "brand": "AGROCAST",
        "page_title": "Agricultural Input Purchase Planning",
        "results": load_results(),
        "logged_in": bool(g.get("user")),
        "user_email": g.user.email if g.get("user") else None,
        "category_labels": {"Fertilizantes": "Fertilizers", "Fungicidas": "Fungicides", "Herbicidas": "Herbicides",
                            "Insecticidas": "Insecticides"},
    }


# ---------------------------------------------------------------- public documentation

@app.route("/")
def home():
    return render_template("home.html")


@app.route("/project/problem")
def problem():
    return render_template("project_problem.html")


@app.route("/project/crisp-mlq")
def crisp():
    return render_template("project_crisp.html")


@app.route("/project/system-design")
def system_design():
    return render_template("system_design.html")


MODEL_INFO = {
    "ets": {
        "name": "ETS", "full": "Exponential Smoothing State Space",
        "type": "Statistical time-series model", "focus": "Level, trend and uncertainty",
        "description": "ETS decomposes the historical signal into level and trend components and selects the specification using corrected Akaike information criteria.",
        "strength": "Strong baseline for smooth trends and changing levels.",
        "risk": "Can react slowly to abrupt structural changes.",
    },
    "sarima": {
        "name": "SARIMA", "full": "Seasonal AutoRegressive Integrated Moving Average",
        "type": "Statistical time-series model", "focus": "Autocorrelation and seasonality",
        "description": "SARIMA models temporal dependence after differencing and evaluates a grid of non-seasonal and seasonal orders using AICc. It obtained the lowest validation MASE and is the model integrated in the information system.",
        "strength": "Captures temporal dependence and recurring patterns.",
        "risk": "Sensitive to specification and can be computationally expensive.",
    },
    "ridge": {
        "name": "Ridge Regression", "full": "Regularized direct regression with external variables",
        "type": "Machine learning regression model", "focus": "Lagged signal + exogenous variables",
        "description": "Ridge trains a separate model for each forecast horizon using lagged index changes, TRM and international urea and DAP changes, with standardized features.",
        "strength": "Transparent ML model with controlled multicollinearity.",
        "risk": "Performance depends on the stability and future availability of external predictors.",
    },
}


@app.route("/models")
def models():
    return render_template("models.html", model_info=MODEL_INFO)


@app.route("/models/<model>")
def model_detail(model):
    if model not in MODEL_INFO:
        abort(404)
    return render_template("model_detail.html", model=MODEL_INFO[model], model_key=model)


@app.route("/documentation")
def documentation():
    return render_template("documentation.html")


@app.route("/api/results")
def api_results():
    return jsonify(load_results())


@app.route("/health")
def health():
    reason = None
    try:
        db.session.execute(db.text("select 1"))
        # The application tables must be readable too, not only the connection.
        db.session.query(Farm.id).limit(1).all()
        database = "ok"
    except Exception as exc:
        db.session.rollback()
        database = "unavailable"
        # Short diagnostic without credentials or host names.
        message = str(getattr(exc, "orig", exc)).splitlines()[0]
        reason = f"{type(getattr(exc, 'orig', exc)).__name__}: {message.split('@')[-1].split('FATAL:')[-1].strip()[:120]}"
        app.logger.error("Health check failed: %s", reason)
    body = {"status": "ok" if database == "ok" else "degraded", "database": database,
            "results_available": bool(load_results())}
    if reason:
        body["reason"] = reason
    return jsonify(body), 200 if database == "ok" else 503


# ---------------------------------------------------------------- authenticated workspace

@app.route("/dashboard")
@permission_required("market.view")
def dashboard():
    user = g.user
    my_plans = PurchasePlan.query.filter_by(owner_id=user.id).order_by(PurchasePlan.updated_at.desc()).all()
    open_plans = [p for p in my_plans if p.is_open]
    ctx = {
        "farms": Farm.query.filter_by(owner_id=user.id).count(),
        "plans": my_plans[:6],
        "open_plans": len(open_plans),
        "pending_evaluation": sum(p.status == "draft" for p in my_plans),
        "planned_value": sum(p.planned_cost for p in open_plans),
        "projections": {p.id: plan_projection(p) for p in my_plans[:6]},
        "my_inferences": Inference.query.filter_by(user_id=user.id).count(),
        "my_incidents": Incident.query.filter(Incident.reporter_id == user.id,
                                              Incident.status.in_(("open", "in_progress"))).count(),
        "last_month": latest_index_month(),
        "decision_labels": DECISION_LABELS,
        "food_products": FoodProduct.query.count(),
        "food_daily": latest_daily_date(),
    }
    if can("monitoring.view"):
        ctx.update(
            all_plans=PurchasePlan.query.count(),
            all_inferences=Inference.query.count(),
            open_incidents=Incident.query.filter(Incident.status.in_(("open", "in_progress"))).count(),
            recent=Inference.query.order_by(Inference.created_at.desc()).limit(6).all(),
        )
    return render_template("dashboard.html", **ctx)


@app.route("/api/retrain", methods=["POST"])
@permission_required("models.manage")
def retrain():
    record_event("model.pipeline_started")
    try:
        cmd = [sys.executable, "-m", "src.pipeline", "--sin-preparar"]
        completed = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=900)
        if completed.returncode != 0:
            record_event("model.pipeline_failed", "error", stderr=completed.stderr[-1500:])
            return jsonify({"ok": False, "error": completed.stderr[-3000:]}), 500
        record_event("model.pipeline_completed")
        return jsonify({"ok": True, "message": "The evaluation pipeline completed. Package a new version with "
                                               "src.train_model before activating it."})
    except subprocess.TimeoutExpired:
        record_event("model.pipeline_failed", "error", reason="timeout")
        return jsonify({"ok": False, "error": "The pipeline exceeded the 15-minute limit."}), 504


INPUT_GROUPS = ("Fertilizantes", "Fungicidas", "Herbicidas", "Insecticidas")


def load_input_products() -> pd.DataFrame:
    products = load_products()
    return products[products["grupo"].isin(INPUT_GROUPS)] if not products.empty else products


@app.route("/platform/what-to-buy")
@permission_required("market.view")
def what_to_buy():
    products = load_input_products()
    if products.empty:
        return render_template("table_page.html", title="What to buy today", intro="No processed product dataset is available.", columns=[], rows=[])
    order = {"buy": 0, "wait": 1, "normal": 2}
    filtered = products.assign(_o=products["recomendacion"].map(order)).sort_values(["_o", "posicion_24m"]).head(60)
    columns = ["producto", "grupo", "presentacion", "precio", "posicion_24m", "var_12m_pct", "recomendacion", "motivo"]
    labels = {"producto": "Product", "grupo": "Group", "presentacion": "Presentation", "precio": "National price",
              "posicion_24m": "Position in 2-year range (0 = lowest)", "var_12m_pct": "Change 12 months %",
              "recomendacion": "Signal", "motivo": "Reason"}
    rows = filtered[columns].fillna("").to_dict("records")
    return render_template("table_page.html", title="What to buy today",
                           intro=f"Fertilizers and pesticides ranked by purchase signal, from the latest SIPSA-I month ({str(products['fecha_precio'].max())[:7]}).",
                           columns=columns, labels=labels, rows=rows, currency_cols=["precio"])


@app.route("/platform/my-market")
@permission_required("market.view")
def my_market():
    products = load_input_products()
    groups = (products.groupby("grupo", as_index=False).agg(precio=("precio", "median"), n=("precio", "size"))
              .sort_values("precio")) if not products.empty else pd.DataFrame()
    return render_template("market.html", groups=groups.to_dict("records"))


@app.route("/platform/cheapest-location")
@permission_required("market.view")
def cheapest_location():
    df = load_prices()
    if df.empty:
        return render_template("table_page.html", title="Where inputs are cheapest", intro="No price dataset is available.", columns=[], rows=[])
    latest = df["fecha"].max()
    sub = df[df["fecha"].eq(latest) & df["departamento"].ne("Nacional")].sort_values("precio").head(80)
    cols = ["producto", "presentacion", "departamento", "precio"]
    labels = {"producto": "Product", "presentacion": "Presentation", "departamento": "Department", "precio": "Price"}
    return render_template("table_page.html", title="Where inputs are cheapest",
                           intro=f"Lowest department prices of agricultural inputs in the latest SIPSA-I month: {latest:%B %Y}. Food prices by market are in Food prices today.",
                           columns=cols, labels=labels, rows=sub[cols].fillna("").to_dict("records"), currency_cols=["precio"])


@app.route("/platform/price-evolution")
@permission_required("market.view")
def price_evolution():
    products = load_input_products()
    selected = request.args.get("product", "")
    names = products["producto"].dropna().drop_duplicates().sort_values().tolist() if not products.empty else []
    if selected not in names:
        selected = names[0] if names else ""
    history = load_prices()
    hist = (history[history["producto"].eq(selected) & history["departamento"].eq("Nacional")]
            .groupby("fecha", as_index=False)["precio"].median().tail(48)) if selected and not history.empty else pd.DataFrame(columns=["fecha", "precio"])
    forecast = pd.DataFrame()
    pp = DATA / "producto_pronostico.csv"
    if pp.exists() and selected:
        p = pd.read_csv(pp)
        forecast = p[p["producto"].eq(selected)].sort_values("horizonte")
    return render_template("price_evolution.html", names=names, selected=selected, history=hist.to_dict("records"), forecast=forecast.to_dict("records"))


@app.route("/forecast/six-month")
@permission_required("market.view")
def six_month():
    return render_template("six_month.html")


@app.route("/forecast/alerts")
@permission_required("market.view")
def alerts():
    products = load_products()
    products = load_input_products()
    alert_rows = products[products["recomendacion"].isin(["buy", "wait"])] if not products.empty else pd.DataFrame()
    return render_template("alerts.html", rows=alert_rows.head(40).fillna("").to_dict("records"))


@app.route("/forecast/reports")
@permission_required("market.view")
def reports():
    files = [p.name for p in REPORTS.glob("*") if p.is_file()]
    figures = [p.name for p in (REPORTS / "figures").glob("*") if p.is_file()]
    return render_template("reports.html", files=files, figures=figures)


@app.route("/forecast/performance")
@permission_required("market.view")
def performance():
    return render_template("performance.html")


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1")
