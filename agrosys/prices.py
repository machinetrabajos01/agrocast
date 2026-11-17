"""Price calculations on the DANE SIPSA wholesale prices stored in the database."""
from __future__ import annotations

import statistics
import unicodedata
from datetime import timedelta

from sqlalchemy import func

from .db import DataRefresh, FoodForecast, FoodPrice, FoodProduct, db

DAILY_MAX_AGE_DAYS = 10     # a daily price older than this is not "today's" price any more


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower().strip()


def catalog(query: str = "", group: str = ""):
    products = FoodProduct.query.order_by(FoodProduct.food_group, FoodProduct.name).all()
    q = normalize(query)
    found = [p for p in products if (not q or q in normalize(p.name)) and (not group or p.food_group == group)]
    if q:   # names that start with the search come first ("tomate" before "salsa de tomate")
        found.sort(key=lambda p: (not normalize(p.name).startswith(q), normalize(p.name)))
    return found


def groups():
    return [g for (g,) in db.session.query(FoodProduct.food_group).distinct().order_by(FoodProduct.food_group)]


def latest_daily_date():
    return db.session.query(func.max(FoodPrice.price_date)).filter(FoodPrice.frequency == "daily").scalar()


def cities():
    rows = db.session.query(FoodPrice.city).filter(FoodPrice.city.isnot(None),
                                                   FoodPrice.frequency.in_(("daily", "monthly"))).distinct().all()
    return sorted({r[0] for r in rows if r[0]})


def current_prices(product: FoodProduct, city: str = ""):
    """Latest prices of a product by market: today's bulletin when it exists, otherwise the latest month."""
    global_daily = latest_daily_date()
    source, day = None, None
    if product.has_daily and global_daily:
        day = db.session.query(func.max(FoodPrice.price_date)).filter_by(product_id=product.id, frequency="daily").scalar()
        if day and day >= global_daily - timedelta(days=DAILY_MAX_AGE_DAYS):
            source = "daily"
    if source is None:
        day = db.session.query(func.max(FoodPrice.price_date)).filter_by(product_id=product.id, frequency="monthly").scalar()
        source = "monthly" if day else None
    if source is None:
        return {"source": None, "date": None, "rows": [], "city_matched": False}
    rows = (FoodPrice.query.filter_by(product_id=product.id, frequency=source, price_date=day)
            .order_by(FoodPrice.price).all())
    city_matched = False
    if city:
        local = [r for r in rows if normalize(r.city) == normalize(city)]
        if local:
            rows, city_matched = local, True
    return {"source": source, "date": day, "rows": rows, "city_matched": city_matched}


def forecast(product: FoodProduct):
    return FoodForecast.query.filter_by(product_id=product.id).order_by(FoodForecast.horizon).all()


def calculate(shopping_list):
    """Cost of every item at the cheapest market and at the median market, with the forecast signal."""
    lines, totals = [], {"cheapest": 0.0, "median": 0.0, "saving": 0.0, "priced": 0, "missing": 0}
    for item in shopping_list.items:
        p = item.product
        cur = current_prices(p, shopping_list.city or "")
        qty = float(item.quantity)
        line = {"item": item, "product": p, "quantity": qty, "source": cur["source"], "date": cur["date"],
                "city_matched": cur["city_matched"], "outlook": p.outlook, "markets": len(cur["rows"])}
        if cur["rows"]:
            prices = [float(r.price) for r in cur["rows"]]
            best = cur["rows"][0]
            med = statistics.median(prices)
            line.update(cheapest=best, cheapest_price=float(best.price), median_price=med, max_price=max(prices),
                        cost_cheapest=qty * float(best.price), cost_median=qty * med,
                        saving=qty * (med - float(best.price)))
            totals["cheapest"] += line["cost_cheapest"]
            totals["median"] += line["cost_median"]
            totals["saving"] += line["saving"]
            totals["priced"] += 1
        else:
            totals["missing"] += 1
        lines.append(line)
    return lines, totals


def product_detail(product: FoodProduct):
    history = (FoodPrice.query.filter_by(product_id=product.id, frequency="national")
               .order_by(FoodPrice.price_date).all())[-60:]
    daily_trend = (db.session.query(FoodPrice.price_date, func.avg(FoodPrice.price))
                   .filter_by(product_id=product.id, frequency="daily")
                   .group_by(FoodPrice.price_date).order_by(FoodPrice.price_date).all())
    return {"history": history, "daily_trend": daily_trend, "current": current_prices(product),
            "forecast": forecast(product), "outlook": product.outlook}


def last_refresh():
    return DataRefresh.query.filter_by(source="food_prices", status="ok").order_by(DataRefresh.refreshed_at.desc()).first()


def bulk_current(products):
    """Cheapest current price of many products with two queries (catalog page)."""
    out = {}
    global_daily = latest_daily_date()
    by_product = {}
    if global_daily:
        for r in FoodPrice.query.filter(FoodPrice.frequency == "daily",
                                        FoodPrice.price_date >= global_daily - timedelta(days=DAILY_MAX_AGE_DAYS)).all():
            by_product.setdefault(("daily", r.product_id), []).append(r)
    last_month = db.session.query(func.max(FoodPrice.price_date)).filter(FoodPrice.frequency == "monthly").scalar()
    if last_month:
        for r in FoodPrice.query.filter(FoodPrice.frequency == "monthly",
                                        FoodPrice.price_date >= last_month - timedelta(days=95)).all():
            by_product.setdefault(("monthly", r.product_id), []).append(r)
    for p in products:
        for source in ("daily", "monthly"):
            rows = by_product.get((source, p.id))
            if rows:
                day = max(r.price_date for r in rows)
                today = sorted((r for r in rows if r.price_date == day), key=lambda r: r.price)
                out[p.id] = {"source": source, "date": day, "best": today[0], "markets": len(today),
                             "median": statistics.median(float(r.price) for r in today)}
                break
    return out
