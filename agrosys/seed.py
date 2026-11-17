"""Database setup commands.

    flask --app app init-db            create tables (SQLite only; Postgres uses the Supabase migration)
    flask --app app seed               load index history, product prices, food prices and the model version
    flask --app app update-prices      download the latest DANE food prices and load them (daily job)
    flask --app app create-user        create an account with a given role (asks for the password)
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime

import click
import pandas as pd

from src import config as c
from src.inference import MODELS_DIR

from .db import CATEGORIES, IS_POSTGRES, IndexObservation, ModelVersion, ProductPrice, User, db
from .security import hash_password, password_problems

SERIES_COLUMNS = {"total": "T_Índice", "fertilizantes": "T_Fertilizantes", "plaguicidas": "T_Plaguicidas"}


def seed_index():
    df = pd.read_csv(c.PROCESSED / "indice_mensual.csv", parse_dates=["fecha"])
    existing = {(s, m) for s, m in db.session.query(IndexObservation.series, IndexObservation.month).all()}
    added = 0
    for series, col in SERIES_COLUMNS.items():
        for _, r in df[["fecha", col]].dropna().iterrows():
            key = (series, r["fecha"].date())
            if key not in existing:
                db.session.add(IndexObservation(series=series, month=key[1], value=round(float(r[col]), 4), source="UPRA"))
                added += 1
    db.session.commit()
    return added


def seed_products():
    df = pd.read_csv(c.PROCESSED / "producto_resumen.csv")
    df = df[df["grupo"].isin(CATEGORIES)]
    current = {(p.category, p.product, p.presentation): p for p in ProductPrice.query.all()}
    for _, r in df.iterrows():
        key = (r["grupo"], r["producto"], r["presentacion"])
        month = datetime.strptime(r["fecha_precio"], "%Y-%m-%d").date()
        if key in current:
            current[key].price, current[key].price_month = float(r["precio"]), month
        else:
            db.session.add(ProductPrice(category=key[0], product=key[1], presentation=key[2],
                                        price_month=month, price=float(r["precio"])))
    db.session.commit()
    return len(df)


def seed_model_versions():
    for card_path in sorted(MODELS_DIR.glob("sarima_v*.json")):
        card = json.loads(card_path.read_text(encoding="utf-8"))
        if ModelVersion.query.filter_by(version=card["version"]).first():
            continue
        db.session.add(ModelVersion(
            version=card["version"], algorithm=card["algorithm"], artifact=card_path.with_suffix(".joblib").name,
            data_cutoff=date.fromisoformat(card["data_cutoff"]),
            trained_at=datetime.fromisoformat(card["trained_at"]),
            metrics={"selection": card["selection"], "series": card["series"]},
            notes="Model selected in Activity 1 (lowest validation MASE).",
            is_active=not ModelVersion.query.filter_by(is_active=True).first()))
        db.session.flush()
    db.session.commit()


def seed_food():
    """Loads the food price snapshots of data/processed/food_*.csv (no download)."""
    from src.food_prices import load_into_db, load_snapshots
    frames = load_snapshots()
    if len(frames) < 5:
        return {"skipped": "food snapshots not found; run python -m src.food_prices build"}
    return load_into_db(frames, source_note="snapshot in repository")


def register_commands(app):

    @app.cli.command("init-db")
    def init_db():
        if IS_POSTGRES:
            click.echo("Postgres detected: apply supabase/migrations/*.sql instead (see docs/technical_manual.md).")
            return
        db.create_all()
        click.echo("SQLite tables created.")

    @app.cli.command("seed")
    def seed():
        if not IS_POSTGRES:
            db.create_all()
        click.echo(f"Index observations added: {seed_index()}")
        click.echo(f"Product prices loaded: {seed_products()}")
        click.echo(f"Food prices: {seed_food()}")
        seed_model_versions()
        click.echo("Model versions registered: " + ", ".join(v.version for v in ModelVersion.query.all()))

    @app.cli.command("update-prices")
    def update_prices():
        from src.food_prices import build, load_into_db
        click.echo(f"Food prices: {load_into_db(build())}")

    @app.cli.command("create-user")
    @click.option("--email", required=True)
    @click.option("--name", required=True)
    @click.option("--role", type=click.Choice(["admin", "analyst", "producer"]), default="producer")
    @click.option("--password", default=lambda: os.getenv("NEW_USER_PASSWORD", ""), hide_input=True)
    def create_user(email, name, role, password):
        if not password:
            password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
        problems = password_problems(password)
        if problems:
            raise click.ClickException("The password needs " + " and ".join(problems))
        email = email.strip().lower()
        user = User.query.filter_by(email=email).first()
        if user:
            user.role, user.full_name, user.password_hash, user.is_active = role, name, hash_password(password), True
        else:
            db.session.add(User(email=email, full_name=name, role=role, password_hash=hash_password(password)))
        db.session.commit()
        click.echo(f"{'Updated' if user else 'Created'} {role} account {email}")
