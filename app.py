"""AgroCast Flask application.

A professional academic platform for agricultural input price intelligence.
The forecasting engine remains in src/ and this web layer presents its real
results, methodology, model comparison and decision-support tools.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from functools import wraps
from pathlib import Path

import pandas as pd
from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for, flash
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
DATA = ROOT / "data" / "processed"
WEB_DATA = ROOT / "web" / "data"

app = Flask(__name__, template_folder="web/templates", static_folder="web/static")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "agrocast-academic-demo-change-me")
app.config["DEMO_EMAIL"] = os.getenv("DEMO_EMAIL", "demo@agrocast.local")
app.config["DEMO_PASSWORD"] = os.getenv("DEMO_PASSWORD", "Agro2026!")


def load_results() -> dict:
    path = REPORTS / "resultados.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_products() -> pd.DataFrame:
    path = DATA / "producto_resumen.csv"
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def load_market() -> pd.DataFrame:
    path = DATA / "mercado_mensual.csv"
    return pd.read_csv(path, parse_dates=["fecha"]) if path.exists() else pd.DataFrame()


def load_prices() -> pd.DataFrame:
    path = DATA / "sipsa_precios.csv"
    return pd.read_csv(path, parse_dates=["fecha"]) if path.exists() else pd.DataFrame()


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_globals():
    results = load_results()
    return {
        "brand": "AGROCAST",
        "page_title": "Agricultural Price Intelligence",
        "results": results,
        "logged_in": bool(session.get("user")),
        "user_email": session.get("user"),
    }


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        if email == app.config["DEMO_EMAIL"] and password == app.config["DEMO_PASSWORD"]:
            session["user"] = email
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Invalid credentials. Use the academic demo account shown below.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/dashboard")
@login_required
def dashboard():
    products = load_products()
    buy = products[products.get("recomendacion", pd.Series(dtype=str)).eq("comprar")] if not products.empty else pd.DataFrame()
    return render_template("dashboard.html", products=products, buy_count=len(buy))


@app.route("/project/problem")
def problem():
    return render_template("project_problem.html")


@app.route("/project/crisp-mlq")
def crisp():
    return render_template("project_crisp.html")


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
        "description": "SARIMA models temporal dependence after differencing and evaluates a grid of non-seasonal and seasonal orders using AICc.",
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


@app.route("/api/results")
def api_results():
    return jsonify(load_results())


@app.route("/api/retrain", methods=["POST"])
@login_required
def retrain():
    try:
        cmd = [sys.executable, "-m", "src.pipeline", "--sin-preparar"]
        completed = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=900)
        if completed.returncode != 0:
            return jsonify({"ok": False, "error": completed.stderr[-3000:]}), 500
        return jsonify({"ok": True, "message": "The full modeling and evaluation pipeline completed successfully."})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "The pipeline exceeded the 15-minute demonstration limit."}), 504


@app.route("/platform/what-to-buy")
@login_required
def what_to_buy():
    products = load_products()
    if products.empty:
        return render_template("table_page.html", title="What to Buy Today", intro="No processed product dataset is available.", columns=[], rows=[])
    filtered = products.sort_values(["recomendacion", "posicion_24m"], ascending=[True, True]).head(40)
    columns = ["producto", "grupo", "presentacion", "precio", "posicion_24m", "var_12m_pct", "recomendacion", "motivo"]
    rows = filtered[columns].fillna("").to_dict("records")
    return render_template("table_page.html", title="What to Buy Today", intro="Decision support based on current price position, recent variation and the product-level forecast rule.", columns=columns, rows=rows, currency_cols=["precio"])


@app.route("/platform/my-market")
@login_required
def my_market():
    products = load_products()
    groups = products.groupby("grupo", as_index=False)["precio"].median().sort_values("precio") if not products.empty else pd.DataFrame()
    return render_template("market.html", groups=groups.to_dict("records"))


@app.route("/platform/cheapest-location")
@login_required
def cheapest_location():
    df = load_prices()
    if df.empty:
        return render_template("table_page.html", title="Where It Is Cheapest", intro="No price dataset is available.", columns=[], rows=[])
    latest = df["fecha"].max()
    sub = df[df["fecha"].eq(latest)].sort_values("precio").head(60)
    cols = ["producto", "departamento", "presentacion", "precio"]
    return render_template("table_page.html", title="Where It Is Cheapest", intro=f"Lowest observed records in the latest available month: {latest:%B %Y}.", columns=cols, rows=sub[cols].fillna("").to_dict("records"), currency_cols=["precio"])


@app.route("/platform/price-evolution")
@login_required
def price_evolution():
    products = load_products()
    selected = request.args.get("product", "")
    names = products["producto"].dropna().drop_duplicates().sort_values().tolist() if not products.empty else []
    if not selected and names:
        selected = names[0]
    history = load_prices()
    hist = history[history["producto"].eq(selected)].groupby("fecha", as_index=False)["precio"].median().tail(48) if selected and not history.empty else pd.DataFrame(columns=["fecha","precio"])
    forecast = pd.DataFrame()
    pp = DATA / "producto_pronostico.csv"
    if pp.exists() and selected:
        p = pd.read_csv(pp)
        forecast = p[p["producto"].eq(selected)].sort_values("horizonte")
    return render_template("price_evolution.html", names=names[:250], selected=selected, history=hist.to_dict("records"), forecast=forecast.to_dict("records"))


@app.route("/forecast/six-month")
@login_required
def six_month():
    return render_template("six_month.html")


@app.route("/forecast/alerts")
@login_required
def alerts():
    products = load_products()
    alert_rows = products[products["recomendacion"].isin(["comprar", "esperar"])] if not products.empty else pd.DataFrame()
    return render_template("alerts.html", rows=alert_rows.head(40).fillna("").to_dict("records"))


@app.route("/forecast/reports")
@login_required
def reports():
    files = [p.name for p in REPORTS.glob("*") if p.is_file()]
    figures = [p.name for p in (REPORTS/"figures").glob("*") if p.is_file()]
    return render_template("reports.html", files=files, figures=figures)


@app.route("/forecast/performance")
@login_required
def performance():
    return render_template("performance.html")


@app.route("/documentation")
def documentation():
    return render_template("documentation.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "results_available": bool(load_results())})


if __name__ == "__main__":
    app.run(debug=True)
