"""Food wholesale prices from DANE SIPSA: daily bulletin, monthly series of every product, forecasts and database load.

Sources (official, public):
  * Daily bulletin annex  https://www.dane.gov.co/files/operaciones/SIPSA/anex-SIPSADiario-<ddmmmyyyy>.xlsx
    Fresh vegetables, fruits, tubers and plantains in 16 wholesale markets, $/kg, every business day.
  * Yearly wholesale series  (see config.URL_MAYORISTAS*) Monthly average price of every product in every market.

Usage:
  python -m src.food_prices build       download sources and rebuild the snapshots data/processed/food_*.csv
  python -m src.food_prices update-db   build and load the result into the database (daily scheduled job)
"""
from __future__ import annotations

import argparse
import io
import json
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests
from joblib import Parallel, delayed

from . import config as c
from .alimentos import GRUPOS, leer_archivo, presentacion
from .ingest import UA, descargar_mayoristas
from .productos import evaluar
from .models import ets, naive

DAILY_DIR = c.RAW / "sipsa_daily"
DAILY_URL = "https://www.dane.gov.co/files/operaciones/SIPSA/anex-SIPSADiario-{day:02d}{month}{year}.xlsx"
MONTHS_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
DAILY_DAYS = 45              # days of daily bulletins kept
MARKET_MONTHS = 13           # months of market-level monthly prices kept
HORIZON = 3                  # months ahead for food forecasts
ORIGINS = 12                 # validation origins per product
MIN_MONTHS = 36              # minimum continuous history to forecast
DAILY_GROUPS = {"verduras y hortalizas": "Verduras y hortalizas", "frutas frescas": "Frutas",
                "tuberculos, raices y platanos": "Tubérculos y plátanos"}
# The daily bulletin names the variety that predominates in the markets with a generic name ("Tomate*").
# Each one is matched with the monthly series of that variety, so a product has its daily price, history and forecast.
DAILY_TO_MONTHLY = {
    "Aguacate": "Aguacate papelillo", "Arracacha": "Arracacha amarilla", "Banano": "Banano criollo",
    "Fríjol verde": "Fríjol verde cargamanto", "Guayaba": "Guayaba pera", "Mandarina": "Mandarina común",
    "Manzana royal gala": "Manzana royal gala importada", "Naranja": "Naranja Valencia",
    "Papa criolla": "Papa criolla limpia", "Papa negra": "Papa parda pastusa", "Piña": "Piña gold",
    "Tomate": "Tomate chonto", "Yuca": "Yuca ICA",
}
OUT = {
    "daily": c.PROCESSED / "food_daily.csv",
    "national": c.PROCESSED / "food_monthly_national.csv",
    "market": c.PROCESSED / "food_monthly_market.csv",
    "forecast": c.PROCESSED / "food_forecast.csv",
    "outlook": c.PROCESSED / "food_outlook.csv",
}


def _norm(text):
    plain = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", plain).strip().lower()


def clean_name(text):
    return re.sub(r"\s+", " ", str(text).replace("*", "")).strip()


# ---------------------------------------------------------------- daily bulletin

def daily_url(d: date) -> str:
    return DAILY_URL.format(day=d.day, month=MONTHS_ES[d.month - 1], year=d.year)


def download_daily(days=DAILY_DAYS, today=None):
    """Downloads the bulletins of the last `days` days that are not on disk yet (holidays return 404)."""
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    today = today or date.today()
    found = []
    for k in range(days):
        d = today - timedelta(days=k)
        if d.weekday() == 6:     # no wholesale bulletin on Sundays
            continue
        path = DAILY_DIR / f"{d.isoformat()}.xlsx"
        if not path.exists():
            try:
                r = requests.get(daily_url(d), headers=UA, timeout=60)
            except requests.RequestException:
                continue
            if r.status_code != 200 or not r.content.startswith(b"PK"):
                continue
            path.write_bytes(r.content)
        found.append(path)
    for old in DAILY_DIR.glob("*.xlsx"):          # keep the folder small
        if old not in found and old.stem < (today - timedelta(days=days)).isoformat():
            old.unlink()
    return sorted(found)


def parse_daily(path) -> pd.DataFrame:
    """One row per product and market with the price of that day ($/kg)."""
    d = pd.read_excel(path, header=None, dtype=object)
    day = date.fromisoformat(path.stem)
    header = d.iloc[2]
    markets = {col: clean_name(header[col]) for col in range(1, d.shape[1], 2) if isinstance(header[col], str)}
    rows, group = [], None
    for i in range(4, len(d)):
        name = d.iat[i, 0]
        if not isinstance(name, str):
            continue
        key = _norm(name)
        if key in DAILY_GROUPS:
            group = DAILY_GROUPS[key]
            continue
        if group is None or key.startswith(("*", "var%", "n.d")):
            continue
        product = clean_name(name)
        product = DAILY_TO_MONTHLY.get(product, product)
        for col, market in markets.items():
            price = pd.to_numeric(d.iat[i, col], errors="coerce")
            if pd.notna(price) and price > 0:
                rows.append({"price_date": day, "food_group": group, "product": product, "market": market,
                             "city": market.split(",")[0].strip(), "price": float(price)})
    return pd.DataFrame(rows)


def build_daily():
    paths = download_daily()
    frames = [parse_daily(p) for p in paths]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=["price_date", "food_group", "product", "market", "city", "price", "unit"])
    daily = pd.concat(frames, ignore_index=True)
    daily["price_date"] = pd.to_datetime(daily["price_date"])
    daily = daily.drop_duplicates(["price_date", "product", "market"], keep="last")
    daily["unit"] = "1 kilogramo"
    return daily.sort_values(["price_date", "product", "market"]).reset_index(drop=True)


# ---------------------------------------------------------------- monthly series of every product

def _department(market, sources):
    if market in sources:
        return sources[market]
    inside = re.search(r"\(([^)]+)\)", market)
    return inside.group(1).strip().title() if inside else None


def build_monthly():
    descargar_mayoristas(forzar=True)
    parts, sources = [], {}
    for path in sorted(c.MAYORISTAS.glob("*.xlsx")):
        rows, src = leer_archivo(path)
        parts.append(rows)
        sources.update(src)
    s = pd.concat(parts, ignore_index=True)
    s["fecha"] = pd.to_datetime(s["fecha"], errors="coerce").dt.to_period("M").dt.to_timestamp()
    s["precio"] = pd.to_numeric(s["precio"], errors="coerce")
    s = s.dropna(subset=["fecha", "precio"])
    s = s[s["precio"] > 0].copy()
    s["food_group"] = s["grupo"].map(lambda g: GRUPOS.get(_norm(g)))
    s = s.dropna(subset=["food_group"])
    s["product"] = s["producto"].map(clean_name)
    s["market"] = s["mercado"].map(clean_name)
    s["unit"] = s["product"].map(presentacion)
    # A few products were published under another group in earlier years: keep the group of the latest row.
    s = s.sort_values("fecha")
    s["food_group"] = s.groupby("product")["food_group"].transform("last")

    national = (s.groupby(["fecha", "food_group", "product", "unit"], as_index=False)
                .agg(price=("precio", "median"), n_markets=("precio", "size"))
                .rename(columns={"fecha": "month"}))
    national["price"] = national["price"].round(2)

    recent = s[s["fecha"] >= s["fecha"].max() - pd.DateOffset(months=MARKET_MONTHS - 1)].copy()
    recent["city"] = recent["market"].str.split(",").str[0].str.strip()
    recent["department"] = recent["mercado"].map(lambda m: _department(str(m).strip(), sources))
    market = (recent.groupby(["fecha", "food_group", "product", "unit", "market", "city", "department"], dropna=False,
                             as_index=False)["precio"].mean()
              .rename(columns={"fecha": "month", "precio": "price"}))
    market["price"] = market["price"].round(2)
    return national.sort_values(["product", "month"]), market.sort_values(["product", "month", "market"])


# ---------------------------------------------------------------- forecasts and purchase signal

def recommendation(position, change, lo80, hi80, price):
    if hi80 < price:
        return "wait", f"The price is expected to fall within {HORIZON} months."
    if position >= 0.67 and change <= -2:
        return "wait", "It is close to its highest price of the last two years and is expected to fall."
    if lo80 > price:
        return "buy", f"The price is expected to rise within {HORIZON} months."
    if position <= 0.33 and change >= -1:
        return "buy", "It is close to its lowest price of the last two years and no fall is expected."
    return "normal", "The price is in the middle of its two-year range, with no clear signal."


def _series(national):
    last = national["month"].max()
    out = {}
    for (group, product, unit), g in national.groupby(["food_group", "product", "unit"]):
        y = g.set_index("month")["price"].sort_index().asfreq("MS").interpolate(limit=2)
        if y.index[-1] < last - pd.DateOffset(months=2):
            continue
        gaps = np.flatnonzero(y.isna().to_numpy())
        if len(gaps):
            y = y.iloc[gaps[-1] + 1:]
        if len(y) >= MIN_MONTHS:
            out[(group, product, unit)] = y
    return out


def _forecast_one(key, y):
    model, mae, q = evaluar(y, h=HORIZON, origenes=ORIGINS, meses=HORIZON)
    pred = np.asarray({"naive": naive, "ets": ets}[model](y, HORIZON))
    months = pd.date_range(y.index[-1] + pd.DateOffset(months=1), periods=HORIZON, freq="MS")
    group, product, unit = key
    fc = [{"product": product, "horizon": i + 1, "month": months[i].date(), "value": round(float(pred[i]), 2),
           "lo80": round(float(pred[i] * np.exp(-q[i])), 2), "hi80": round(float(pred[i] * np.exp(q[i])), 2)}
          for i in range(HORIZON)]
    price = float(y.iloc[-1])
    window = y.tail(24)
    span = float(window.max() - window.min())
    position = (price - float(window.min())) / span if span > 0 else 0.5
    end = fc[-1]
    change = (end["value"] / price - 1) * 100
    code, reason = recommendation(position, change, end["lo80"], end["hi80"], price)
    outlook = {"product": product, "food_group": group, "unit": unit, "last_month": y.index[-1].date(),
               "last_price": round(price, 2), "min_24m": round(float(window.min()), 2),
               "max_24m": round(float(window.max()), 2), "position_24m": round(position, 3),
               "change_12m_pct": round((price / float(y.iloc[-13]) - 1) * 100, 2) if len(y) > 12 else None,
               "change_3m_pct": round(change, 2), "model": model, "mae_model": round(mae[model], 2),
               "mae_naive": round(mae["naive"], 2), "recommendation": code, "reason": reason}
    return fc, outlook


def build_forecasts(national):
    series = _series(national)
    results = Parallel(n_jobs=-1)(delayed(_forecast_one)(k, y) for k, y in series.items())
    forecast = pd.DataFrame([f for fc, _ in results for f in fc])
    outlook = pd.DataFrame([o for _, o in results])
    return forecast, outlook


def build():
    c.PROCESSED.mkdir(parents=True, exist_ok=True)
    daily = build_daily()
    national, market = build_monthly()
    forecast, outlook = build_forecasts(national)
    frames = {"daily": daily, "national": national, "market": market, "forecast": forecast, "outlook": outlook}
    for name, frame in frames.items():
        frame.to_csv(OUT[name], index=False)
    return frames


def load_snapshots():
    parse = {"daily": ["price_date"], "national": ["month"], "market": ["month"], "forecast": ["month"],
             "outlook": ["last_month"]}
    return {k: pd.read_csv(p, parse_dates=parse[k]) for k, p in OUT.items() if p.exists()}


# ---------------------------------------------------------------- database load (used by the daily job and by seed)

def _to_date(v):
    return v.date() if hasattr(v, "date") else v


def load_into_db(frames: dict, source_note: str = "DANE SIPSA") -> dict:
    """Replaces the stored food prices with the given frames inside one transaction."""
    from agrosys.db import DataRefresh, FoodForecast, FoodOutlook, FoodPrice, FoodProduct, db

    daily, national, market = frames["daily"], frames["national"], frames["market"]
    forecast, outlook = frames["forecast"], frames["outlook"]

    catalog = {}
    for df, has_daily in ((national, False), (market, False), (daily, True)):
        for r in df[["product", "food_group", "unit"]].drop_duplicates("product").itertuples(index=False):
            entry = catalog.setdefault(r.product, {"food_group": r.food_group, "unit": r.unit, "has_daily": False})
            entry["has_daily"] = entry["has_daily"] or has_daily
    existing = {p.name: p for p in FoodProduct.query.all()}
    for name, info in catalog.items():
        p = existing.get(name)
        if p is None:
            p = FoodProduct(name=name, food_group=info["food_group"], unit=info["unit"], has_daily=info["has_daily"])
            db.session.add(p)
            existing[name] = p
        else:
            p.food_group, p.unit, p.has_daily = info["food_group"], info["unit"], info["has_daily"] or p.has_daily
    db.session.flush()
    ids = {name: p.id for name, p in existing.items()}

    def rows(df, frequency, date_col, market_col=None):
        out = []
        for r in df.to_dict("records"):
            out.append({"product_id": ids[r["product"]], "frequency": frequency, "price_date": _to_date(r[date_col]),
                        "market": r[market_col] if market_col else "National",
                        "city": r.get("city") if market_col else None,
                        "department": (r.get("department") if isinstance(r.get("department"), str) else None),
                        "price": float(r["price"])})
        return out

    FoodPrice.query.delete()
    payload = rows(national, "national", "month") + rows(market, "monthly", "month", "market") + \
        rows(daily, "daily", "price_date", "market")
    for k in range(0, len(payload), 5000):
        db.session.execute(FoodPrice.__table__.insert(), payload[k:k + 5000])

    FoodForecast.query.delete()
    db.session.execute(FoodForecast.__table__.insert(), [
        {"product_id": ids[r["product"]], "horizon": int(r["horizon"]), "month": _to_date(r["month"]),
         "value": r["value"], "lo80": r["lo80"], "hi80": r["hi80"]} for r in forecast.to_dict("records")] or [])
    FoodOutlook.query.delete()
    outlook_rows = []
    for r in outlook.to_dict("records"):
        outlook_rows.append({
            "product_id": ids[r["product"]], "last_month": _to_date(r["last_month"]), "last_price": r["last_price"],
            "min_24m": r["min_24m"], "max_24m": r["max_24m"], "position_24m": r["position_24m"],
            "change_12m_pct": None if pd.isna(r["change_12m_pct"]) else r["change_12m_pct"],
            "change_3m_pct": r["change_3m_pct"], "model": r["model"], "mae_model": r["mae_model"],
            "mae_naive": r["mae_naive"], "recommendation": r["recommendation"], "reason": r["reason"]})
    if outlook_rows:
        db.session.execute(FoodOutlook.__table__.insert(), outlook_rows)

    summary = {"products": len(ids), "daily_rows": len(daily), "monthly_rows": len(market),
               "national_rows": len(national), "forecast_products": len(outlook),
               "last_daily": str(daily["price_date"].max().date()) if len(daily) else None,
               "last_month": str(national["month"].max().date()) if len(national) else None}
    db.session.add(DataRefresh(source="food_prices", status="ok", detail=summary, note=source_note))
    db.session.commit()
    return summary


# ---------------------------------------------------------------- other official data refreshed by the daily job

def update_index_from_api():
    """Adds to the database the UPRA index months published on Datos Abiertos that are not stored yet."""
    from agrosys.db import DataRefresh, IndexObservation, db
    columns = {"total": "indice_total", "fertilizantes": "total_fertilizantes", "plaguicidas": "total_plaguicidas"}
    r = requests.get(c.URL_UPRA_API, headers=UA, timeout=120)
    r.raise_for_status()
    api = pd.read_csv(io.StringIO(r.text), parse_dates=["fecha"]).set_index("fecha").sort_index()
    existing = {(s, m) for s, m in db.session.query(IndexObservation.series, IndexObservation.month).all()}
    added = []
    for series, col in columns.items():
        last = max((m for s, m in existing if s == series), default=None)
        for month, value in pd.to_numeric(api[col], errors="coerce").dropna().items():
            if value <= 0 or (last and month.date() <= last):
                continue
            db.session.add(IndexObservation(series=series, month=month.date(), value=round(float(value), 4),
                                            source="UPRA Datos Abiertos (automatic)"))
            added.append(f"{series} {month:%Y-%m}")
    db.session.add(DataRefresh(source="upra_index", status="ok", detail={"added": added}, note="Datos Abiertos gwbi-fnzs"))
    db.session.commit()
    return added


def update_input_prices():
    """Refreshes the SIPSA-I input prices used to price purchase plans (latest national median per product)."""
    from agrosys.db import CATEGORIES, DataRefresh, ProductPrice, db
    from .ingest import _bajar
    from .prepare import preparar_sipsa
    _bajar(c.URL_SIPSA, c.SIPSA_XLSX, forzar=True)
    precios, _ = preparar_sipsa()
    nat = precios[(precios["departamento"] == "Nacional") & precios["grupo"].isin(CATEGORIES)]
    latest = nat.sort_values("fecha").groupby(["grupo", "producto", "presentacion"]).tail(1)
    current = {(p.category, p.product, p.presentation): p for p in ProductPrice.query.all()}
    changed = 0
    for r in latest.itertuples(index=False):
        key = (r.grupo, r.producto, r.presentacion)
        if key in current and (current[key].price_month != r.fecha.date() or float(current[key].price) != float(r.precio)):
            current[key].price, current[key].price_month = float(r.precio), r.fecha.date()
            changed += 1
    db.session.add(DataRefresh(source="input_prices", status="ok",
                               detail={"updated": changed, "latest_month": str(latest["fecha"].max().date())},
                               note="DANE SIPSA-I"))
    db.session.commit()
    return changed


def purge_old_logs(days=365):
    """Retention rule of the privacy policy: usage records older than twelve months are deleted."""
    from agrosys.db import EventLog, db
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    removed = EventLog.query.filter(EventLog.created_at < cutoff).delete()
    db.session.commit()
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["build", "update-db"])
    args = ap.parse_args()
    frames = build()
    print(json.dumps({k: len(v) for k, v in frames.items()}))
    if args.command == "update-db":
        from app import app
        with app.app_context():
            print("food prices", load_into_db(frames))
            for name, job in (("index", update_index_from_api), ("input prices", update_input_prices),
                              ("log retention", purge_old_logs)):
                try:
                    print(name, job())
                except Exception as exc:  # one source failing must not stop the others
                    from agrosys.db import DataRefresh, db
                    db.session.rollback()
                    db.session.add(DataRefresh(source=name.replace(" ", "_"), status="error",
                                               detail={"error": type(exc).__name__}, note=str(exc)[:300]))
                    db.session.commit()
                    print(name, "failed:", exc)


if __name__ == "__main__":
    main()
