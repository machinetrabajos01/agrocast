"""Persistent data model of the AGROCAST information system.

In production the tables live in the `agrocast` schema of the Supabase Postgres database
(see supabase/migrations/20261116090000_information_system.sql). For local development without
DATABASE_URL the same model is created in a SQLite file.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import JSON, CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

def _normalize_database_url(raw: str) -> str:
    """Accept the usual ways the value is pasted in hosting dashboards (quotes, spaces, KEY= prefix)."""
    url = (raw or "").strip().strip("'\"").strip()
    if url.upper().startswith("DATABASE_URL="):
        url = url.split("=", 1)[1].strip().strip("'\"")
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg://" + url[len(prefix):]
    if url and not url.startswith(("postgresql+psycopg://", "sqlite:///")):
        raise RuntimeError("DATABASE_URL must start with postgresql:// (or be empty for local SQLite). "
                           f"Received a value starting with {url[:12]!r}.")
    return url


DATABASE_URL = _normalize_database_url(os.getenv("DATABASE_URL", ""))
IS_POSTGRES = DATABASE_URL.startswith("postgres")
SCHEMA = "agrocast" if IS_POSTGRES else None
P = f"{SCHEMA}." if SCHEMA else ""
JsonType = JSON().with_variant(JSONB(), "postgresql")

db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc)


def _args(*constraints):
    return (*constraints, {"schema": SCHEMA}) if SCHEMA else constraints


ROLES = ("admin", "analyst", "producer")
CATEGORIES = ("Fertilizantes", "Fungicidas", "Herbicidas", "Insecticidas")
CATEGORY_LABELS = {"Fertilizantes": "Fertilizers", "Fungicidas": "Fungicides",
                   "Herbicidas": "Herbicides", "Insecticidas": "Insecticides"}
PLAN_STATUSES = ("draft", "evaluated", "approved", "deferred", "purchased", "cancelled")
DECISIONS = ("buy_now", "defer", "as_planned")
DECISION_LABELS = {"buy_now": "Buy now", "defer": "Defer purchase", "as_planned": "Buy as planned"}
INCIDENT_KINDS = ("bug", "data_issue", "model_issue", "access", "feedback")
INCIDENT_STATUSES = ("open", "in_progress", "resolved", "closed")


class User(db.Model):
    __tablename__ = "app_user"
    __table_args__ = _args(CheckConstraint("role in ('admin','analyst','producer')", name="app_user_role_check"))
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    full_name = db.Column(db.Text, nullable=False)
    email = db.Column(db.Text, nullable=False, unique=True)
    password_hash = db.Column(db.Text, nullable=False)
    role = db.Column(db.Text, nullable=False, default="producer")
    department = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime(timezone=True))
    consent_at = db.Column(db.DateTime(timezone=True))
    consent_version = db.Column(db.Text)


class ModelVersion(db.Model):
    __tablename__ = "model_version"
    __table_args__ = _args()
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    version = db.Column(db.Text, nullable=False, unique=True)
    algorithm = db.Column(db.Text, nullable=False)
    artifact = db.Column(db.Text, nullable=False)
    data_cutoff = db.Column(db.Date, nullable=False)
    trained_at = db.Column(db.DateTime(timezone=True), nullable=False)
    metrics = db.Column(JsonType, nullable=False, default=dict)
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class IndexObservation(db.Model):
    __tablename__ = "index_observation"
    __table_args__ = _args()
    series = db.Column(db.Text, primary_key=True)
    month = db.Column(db.Date, primary_key=True)
    value = db.Column(db.Numeric(10, 4), nullable=False)
    source = db.Column(db.Text, nullable=False, default="UPRA")
    entered_by = db.Column(db.BigInteger, ForeignKey(f"{P}app_user.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    author = db.relationship("User")


class ProductPrice(db.Model):
    __tablename__ = "product_price"
    __table_args__ = _args(UniqueConstraint("category", "product", "presentation"))
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    category = db.Column(db.Text, nullable=False)
    product = db.Column(db.Text, nullable=False)
    presentation = db.Column(db.Text, nullable=False)
    price_month = db.Column(db.Date, nullable=False)
    price = db.Column(db.Numeric(14, 2), nullable=False)

    @property
    def label(self):
        return f"{self.product} · {self.presentation}"


class Farm(db.Model):
    __tablename__ = "farm"
    __table_args__ = _args(UniqueConstraint("owner_id", "name"))
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    owner_id = db.Column(db.BigInteger, ForeignKey(f"{P}app_user.id", ondelete="CASCADE"), nullable=False)
    name = db.Column(db.Text, nullable=False)
    department = db.Column(db.Text, nullable=False)
    municipality = db.Column(db.Text)
    main_crop = db.Column(db.Text, nullable=False)
    area_ha = db.Column(db.Numeric(10, 2), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    owner = db.relationship("User")
    plans = db.relationship("PurchasePlan", back_populates="farm", passive_deletes="all")


class PurchasePlan(db.Model):
    __tablename__ = "purchase_plan"
    __table_args__ = _args()
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    owner_id = db.Column(db.BigInteger, ForeignKey(f"{P}app_user.id", ondelete="CASCADE"), nullable=False)
    farm_id = db.Column(db.BigInteger, ForeignKey(f"{P}farm.id", ondelete="RESTRICT"), nullable=False)
    title = db.Column(db.Text, nullable=False)
    category = db.Column(db.Text, nullable=False)
    planned_month = db.Column(db.Date, nullable=False)
    status = db.Column(db.Text, nullable=False, default="draft")
    recommendation = db.Column(db.Text)
    decision = db.Column(db.Text)
    decision_note = db.Column(db.Text)
    last_inference_id = db.Column(db.BigInteger, ForeignKey(f"{P}inference.id", ondelete="SET NULL",
                                                            use_alter=True, name="purchase_plan_last_inference_fk"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    closed_at = db.Column(db.DateTime(timezone=True))
    owner = db.relationship("User", lazy="joined")
    farm = db.relationship("Farm", back_populates="plans", lazy="joined")
    items = db.relationship("PlanItem", back_populates="plan", cascade="all, delete-orphan", passive_deletes=True,
                            lazy="selectin")
    last_inference = db.relationship("Inference", foreign_keys=[last_inference_id], post_update=True, lazy="joined")

    @property
    def planned_cost(self):
        return sum(float(i.quantity) * float(i.unit_price) for i in self.items)

    @property
    def actual_cost(self):
        if not self.items or any(i.actual_unit_price is None for i in self.items):
            return None
        return sum(float(i.quantity) * float(i.actual_unit_price) for i in self.items)

    @property
    def is_open(self):
        return self.status not in ("purchased", "cancelled")


class PlanItem(db.Model):
    __tablename__ = "plan_item"
    __table_args__ = _args(UniqueConstraint("plan_id", "product_price_id"))
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    plan_id = db.Column(db.BigInteger, ForeignKey(f"{P}purchase_plan.id", ondelete="CASCADE"), nullable=False)
    product_price_id = db.Column(db.BigInteger, ForeignKey(f"{P}product_price.id", ondelete="RESTRICT"), nullable=False)
    quantity = db.Column(db.Numeric(10, 2), nullable=False)
    unit_price = db.Column(db.Numeric(14, 2), nullable=False)
    actual_unit_price = db.Column(db.Numeric(14, 2))
    plan = db.relationship("PurchasePlan", back_populates="items")
    product = db.relationship("ProductPrice", lazy="joined")


class Inference(db.Model):
    __tablename__ = "inference"
    __table_args__ = _args()
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    user_id = db.Column(db.BigInteger, ForeignKey(f"{P}app_user.id", ondelete="SET NULL"))
    plan_id = db.Column(db.BigInteger, ForeignKey(f"{P}purchase_plan.id", ondelete="SET NULL"))
    model_version_id = db.Column(db.BigInteger, ForeignKey(f"{P}model_version.id", ondelete="RESTRICT"), nullable=False)
    series = db.Column(db.Text, nullable=False)
    horizon = db.Column(db.SmallInteger, nullable=False)
    input_source = db.Column(db.Text, nullable=False)
    input_payload = db.Column(JsonType, nullable=False)
    output_payload = db.Column(JsonType, nullable=False)
    origin_month = db.Column(db.Date, nullable=False)
    target_month = db.Column(db.Date, nullable=False)
    predicted_value = db.Column(db.Numeric(10, 3), nullable=False)
    change_pct = db.Column(db.Numeric(8, 2), nullable=False)
    recommendation = db.Column(db.Text, nullable=False)
    response_ms = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    user = db.relationship("User", lazy="joined")
    plan = db.relationship("PurchasePlan", foreign_keys=[plan_id])
    model_version = db.relationship("ModelVersion", lazy="joined")


class DriftCheck(db.Model):
    __tablename__ = "drift_check"
    __table_args__ = _args()
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    run_by = db.Column(db.BigInteger, ForeignKey(f"{P}app_user.id", ondelete="SET NULL"))
    model_version_id = db.Column(db.BigInteger, ForeignKey(f"{P}model_version.id", ondelete="CASCADE"), nullable=False)
    series = db.Column(db.Text, nullable=False)
    status = db.Column(db.Text, nullable=False)
    z_score = db.Column(db.Numeric(8, 3), nullable=False)
    volatility_ratio = db.Column(db.Numeric(8, 3), nullable=False)
    mae_recent = db.Column(db.Numeric(10, 3))
    detail = db.Column(JsonType, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    user = db.relationship("User")
    model_version = db.relationship("ModelVersion")


class Incident(db.Model):
    __tablename__ = "incident"
    __table_args__ = _args()
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    reporter_id = db.Column(db.BigInteger, ForeignKey(f"{P}app_user.id", ondelete="CASCADE"), nullable=False)
    assigned_to = db.Column(db.BigInteger, ForeignKey(f"{P}app_user.id", ondelete="SET NULL"))
    kind = db.Column(db.Text, nullable=False)
    severity = db.Column(db.Text, nullable=False, default="medium")
    title = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text, nullable=False)
    status = db.Column(db.Text, nullable=False, default="open")
    resolution = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    reporter = db.relationship("User", foreign_keys=[reporter_id], lazy="joined")
    assignee = db.relationship("User", foreign_keys=[assigned_to], lazy="joined")


class EventLog(db.Model):
    __tablename__ = "event_log"
    __table_args__ = _args()
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    level = db.Column(db.Text, nullable=False)
    event = db.Column(db.Text, nullable=False)
    user_id = db.Column(db.BigInteger, ForeignKey(f"{P}app_user.id", ondelete="SET NULL"))
    method = db.Column(db.Text)
    path = db.Column(db.Text)
    status_code = db.Column(db.SmallInteger)
    duration_ms = db.Column(db.Integer)
    detail = db.Column(JsonType, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    user = db.relationship("User")


# ---------------------------------------------------------------- food prices (DANE SIPSA), refreshed daily

FOOD_FREQUENCIES = ("daily", "monthly", "national")
RECOMMENDATION_LABELS = {"buy": "Buy now", "wait": "Wait", "normal": "Normal price"}


class FoodProduct(db.Model):
    __tablename__ = "food_product"
    __table_args__ = _args()
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    name = db.Column(db.Text, nullable=False, unique=True)
    food_group = db.Column(db.Text, nullable=False)
    unit = db.Column(db.Text, nullable=False, default="1 kilogramo")
    has_daily = db.Column(db.Boolean, nullable=False, default=False)
    outlook = db.relationship("FoodOutlook", uselist=False, lazy="joined")


class FoodPrice(db.Model):
    __tablename__ = "food_price"
    __table_args__ = _args(UniqueConstraint("product_id", "frequency", "price_date", "market"))
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    product_id = db.Column(db.BigInteger, ForeignKey(f"{P}food_product.id", ondelete="CASCADE"), nullable=False, index=True)
    frequency = db.Column(db.Text, nullable=False)
    price_date = db.Column(db.Date, nullable=False)
    market = db.Column(db.Text, nullable=False)
    city = db.Column(db.Text)
    department = db.Column(db.Text)
    price = db.Column(db.Numeric(14, 2), nullable=False)


class FoodForecast(db.Model):
    __tablename__ = "food_forecast"
    __table_args__ = _args(UniqueConstraint("product_id", "horizon"))
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    product_id = db.Column(db.BigInteger, ForeignKey(f"{P}food_product.id", ondelete="CASCADE"), nullable=False, index=True)
    horizon = db.Column(db.SmallInteger, nullable=False)
    month = db.Column(db.Date, nullable=False)
    value = db.Column(db.Numeric(14, 2), nullable=False)
    lo80 = db.Column(db.Numeric(14, 2), nullable=False)
    hi80 = db.Column(db.Numeric(14, 2), nullable=False)


class FoodOutlook(db.Model):
    __tablename__ = "food_outlook"
    __table_args__ = _args()
    product_id = db.Column(db.BigInteger, ForeignKey(f"{P}food_product.id", ondelete="CASCADE"), primary_key=True)
    last_month = db.Column(db.Date, nullable=False)
    last_price = db.Column(db.Numeric(14, 2), nullable=False)
    min_24m = db.Column(db.Numeric(14, 2), nullable=False)
    max_24m = db.Column(db.Numeric(14, 2), nullable=False)
    position_24m = db.Column(db.Numeric(6, 3), nullable=False)
    change_12m_pct = db.Column(db.Numeric(8, 2))
    change_3m_pct = db.Column(db.Numeric(8, 2), nullable=False)
    model = db.Column(db.Text, nullable=False)
    mae_model = db.Column(db.Numeric(14, 2), nullable=False)
    mae_naive = db.Column(db.Numeric(14, 2), nullable=False)
    recommendation = db.Column(db.Text, nullable=False)
    reason = db.Column(db.Text, nullable=False)

    @property
    def label(self):
        return RECOMMENDATION_LABELS.get(self.recommendation, self.recommendation)


class DataRefresh(db.Model):
    __tablename__ = "data_refresh"
    __table_args__ = _args()
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    source = db.Column(db.Text, nullable=False)
    status = db.Column(db.Text, nullable=False)
    detail = db.Column(JsonType, nullable=False, default=dict)
    note = db.Column(db.Text)
    refreshed_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class ShoppingList(db.Model):
    __tablename__ = "shopping_list"
    __table_args__ = _args()
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    owner_id = db.Column(db.BigInteger, ForeignKey(f"{P}app_user.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.Text, nullable=False)
    city = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    items = db.relationship("ShoppingItem", cascade="all, delete-orphan", lazy="selectin",
                            order_by="ShoppingItem.id")


class ShoppingItem(db.Model):
    __tablename__ = "shopping_item"
    __table_args__ = _args(UniqueConstraint("list_id", "product_id"))
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    list_id = db.Column(db.BigInteger, ForeignKey(f"{P}shopping_list.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = db.Column(db.BigInteger, ForeignKey(f"{P}food_product.id", ondelete="RESTRICT"), nullable=False, index=True)
    quantity = db.Column(db.Numeric(10, 2), nullable=False)
    product = db.relationship("FoodProduct", lazy="joined")
