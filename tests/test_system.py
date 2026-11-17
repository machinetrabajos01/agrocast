"""Integration tests of the information system: authentication, roles, business rules and inference.

They run against a temporary SQLite database, so they never touch the production database.
"""
import os
import re
import tempfile

import pytest

os.environ["DATABASE_URL"] = ""
os.environ["SQLITE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["FLASK_SECRET_KEY"] = "test-secret"

from app import app as flask_app  # noqa: E402
from agrosys.db import (EventLog, FoodProduct, Inference, ModelVersion, PurchasePlan, ShoppingList, User, db)  # noqa: E402
from agrosys.security import hash_password  # noqa: E402
from agrosys.seed import seed_food, seed_index, seed_model_versions, seed_products  # noqa: E402

PASSWORD = "Secret123"


@pytest.fixture(scope="module")
def app():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        seed_index()
        seed_products()
        seed_model_versions()
        seed_food()
        for email, role in (("admin@test.co", "admin"), ("analyst@test.co", "analyst")):
            db.session.add(User(full_name=role.title() + " User", email=email, role=role,
                                password_hash=hash_password(PASSWORD)))
        db.session.commit()
    yield flask_app


def csrf(client, path="/login"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def login(client, email, password=PASSWORD):
    return client.post("/login", data={"email": email, "password": password, "_csrf": csrf(client)})


def register(client, email, **extra):
    data = {"full_name": "Test Producer", "email": email, "password": PASSWORD, "confirm": PASSWORD,
            "accept_policy": "yes", "_csrf": csrf(client, "/register"), **extra}
    return client.post("/register", data=data)


def test_public_documentation_and_protected_routes(app):
    c = app.test_client()
    assert c.get("/").status_code == 200
    assert b"Project documentation" in c.get("/").data
    assert c.get("/project/system-design").status_code == 200
    for path in ("/dashboard", "/plans", "/farms", "/monitoring", "/admin/users", "/history"):
        r = c.get(path)
        assert r.status_code == 302 and "/login" in r.headers["Location"], path


def test_post_without_csrf_is_rejected(app):
    c = app.test_client()
    assert c.post("/login", data={"email": "x@y.co", "password": "x"}).status_code == 400


def test_registration_cannot_assign_admin_role(app):
    c = app.test_client()
    r = register(c, "intruder@test.co", role="admin")
    assert r.status_code == 302
    with app.app_context():
        user = User.query.filter_by(email="intruder@test.co").one()
        assert user.role == "producer"
        assert user.password_hash != PASSWORD and user.password_hash.startswith("scrypt")
    assert c.get("/admin/users").status_code == 403
    assert c.get("/monitoring").status_code == 403


def test_wrong_password_is_logged(app):
    c = app.test_client()
    r = login(c, "admin@test.co", "wrong-password1")
    assert b"Invalid email or password" in r.data
    with app.app_context():
        assert EventLog.query.filter_by(event="auth.failed_login").count() >= 1


def test_complete_purchase_workflow(app):
    c = app.test_client()
    register(c, "farmer@test.co")
    token = csrf(c, "/farms")
    c.post("/farms", data={"name": "La Esperanza", "department": "Cundinamarca", "main_crop": "Potato",
                           "area_ha": "12.5", "_csrf": token})
    with app.app_context():
        farm_id = db.session.execute(db.text("select id from farm where name='La Esperanza'")).scalar()
    r = c.post("/plans/new", data={"title": "Fertilization 2027", "farm_id": farm_id, "category": "Fertilizantes",
                                   "planned_month": "2027-02", "_csrf": token})
    plan_id = int(r.headers["Location"].rstrip("/").split("/")[-1])
    # Evaluation without items violates a business rule.
    c.post(f"/plans/{plan_id}/evaluate", data={"_csrf": token})
    with app.app_context():
        assert db.session.get(PurchasePlan, plan_id).status == "draft"
        product_id = db.session.execute(db.text("select id from product_price where category='Fertilizantes' limit 1")).scalar()
    c.post(f"/plans/{plan_id}/items", data={"product_price_id": product_id, "quantity": "20", "_csrf": token})
    c.post(f"/plans/{plan_id}/evaluate", data={"_csrf": token})
    with app.app_context():
        plan = db.session.get(PurchasePlan, plan_id)
        assert plan.status == "evaluated" and plan.recommendation in ("buy_now", "defer", "as_planned")
        inf = db.session.get(Inference, plan.last_inference_id)
        assert inf.series == "fertilizantes" and inf.horizon == 6 and inf.user.email == "farmer@test.co"
        assert inf.model_version.version == ModelVersion.query.filter_by(is_active=True).one().version
        other = next(d for d in ("buy_now", "defer", "as_planned") if d != plan.recommendation)
        item_id = plan.items[0].id
    # A decision against the model needs a justification.
    c.post(f"/plans/{plan_id}/decide", data={"decision": other, "decision_note": "", "_csrf": token})
    with app.app_context():
        assert db.session.get(PurchasePlan, plan_id).status == "evaluated"
    c.post(f"/plans/{plan_id}/decide", data={"decision": "buy_now",
                                             "decision_note": "Supplier offered a discount this month", "_csrf": token})
    with app.app_context():
        assert db.session.get(PurchasePlan, plan_id).status == "approved"
    c.post(f"/plans/{plan_id}/purchase", data={f"actual_{item_id}": "150000", "_csrf": token})
    with app.app_context():
        plan = db.session.get(PurchasePlan, plan_id)
        assert plan.status == "purchased" and plan.actual_cost == 20 * 150000

    # Another producer cannot see this plan; the analyst can read it but not modify it.
    c2 = app.test_client()
    register(c2, "neighbor@test.co")
    assert c2.get(f"/plans/{plan_id}").status_code == 403
    c3 = app.test_client()
    login(c3, "analyst@test.co")
    assert c3.get(f"/plans/{plan_id}").status_code == 200
    assert c3.post(f"/plans/{plan_id}/cancel", data={"_csrf": csrf(c3, "/dashboard")}).status_code == 403


def test_analyst_lab_monitoring_and_admin_restrictions(app):
    c = app.test_client()
    login(c, "analyst@test.co")
    token = csrf(c, "/forecast/lab")
    r = c.post("/api/v1/forecast", json={"series": "total", "horizon": 3}, headers={"X-CSRF-Token": token})
    assert r.status_code == 200 and r.get_json()["horizon"] == 3
    r = c.post("/api/v1/forecast", json={"series": "total", "horizon": 9}, headers={"X-CSRF-Token": token})
    assert r.status_code == 422
    assert c.post("/monitoring", data={"_csrf": token}).status_code == 302
    assert c.get("/monitoring").status_code == 200
    assert c.get("/admin/users").status_code == 403
    assert c.get("/farms").status_code == 403


def test_admin_cannot_change_own_role(app):
    c = app.test_client()
    login(c, "admin@test.co")
    token = csrf(c, "/admin/users")
    with app.app_context():
        admin_id = User.query.filter_by(email="admin@test.co").one().id
    c.post(f"/admin/users/{admin_id}", data={"role": "producer", "is_active": "1", "_csrf": token})
    with app.app_context():
        assert db.session.get(User, admin_id).role == "admin"
    for path in ("/admin/users", "/admin/models", "/admin/logs", "/indicators", "/history"):
        assert c.get(path).status_code == 200, path


def test_crisp_phase_pages_are_public(app):
    c = app.test_client()
    for slug in ("business-data", "data-engineering", "model-engineering", "evaluation", "deployment", "monitoring"):
        r = c.get(f"/crisp-ml/{slug}")
        assert r.status_code == 200, slug
    assert c.get("/crisp-ml/unknown").status_code == 404
    assert c.get("/crisp-ml/figure/partitions.png").status_code == 200
    assert c.get("/crisp-ml/figure/..%2Fapp.png").status_code == 404
    assert c.get("/crisp-ml/download/app.py").status_code == 404
    assert b"SIMULATED" in c.get("/crisp-ml/monitoring").data


def test_public_demo_accepts_new_values_and_rejects_bad_input(app):
    c = app.test_client()
    token = csrf(c, "/crisp-ml/deployment")
    ok = c.post("/crisp-ml/deployment/demo", data={"_csrf": token, "series": "fertilizantes", "horizon": "3",
                                                   "new_0": "150", "new_1": "151.5"})
    assert ok.status_code == 200
    html = ok.get_data(as_text=True)
    assert "2 typed by the user" in html and "Recommendation" in html
    bad = c.post("/crisp-ml/deployment/demo", data={"_csrf": token, "series": "total", "horizon": "6", "new_0": "-4"})
    assert bad.status_code == 422
    text = c.post("/crisp-ml/deployment/demo", data={"_csrf": token, "series": "total", "horizon": "6", "new_0": "abc"})
    assert text.status_code == 422
    assert c.post("/crisp-ml/deployment/demo", data={"series": "total"}).status_code == 400
    with app.app_context():
        events = {e.event for e in EventLog.query.all()}
    assert {"demo.forecast", "demo.rejected_input"} <= events



def test_food_catalog_finds_beans_with_price_and_forecast(app):
    c = app.test_client()
    register(c, "beans@test.co")
    html = c.get("/prices/catalog?q=frijol").get_data(as_text=True)
    assert "cargamanto rojo" in html and "$" in html
    with app.app_context():
        bean = FoodProduct.query.filter(FoodProduct.name.like("Fr%jol cargamanto rojo")).first()
        assert bean is not None and bean.outlook is not None
        bean_id = bean.id
    page = c.get(f"/prices/product/{bean_id}")
    assert page.status_code == 200 and b"Where it is cheapest" in page.data
    assert c.get("/prices/product/999999").status_code == 404


def test_shopping_list_calculation_pdf_and_ownership(app):
    owner, other = app.test_client(), app.test_client()
    register(owner, "list-owner@test.co")
    register(other, "list-other@test.co")
    token = csrf(owner, "/prices")
    owner.post("/prices/lists", data={"_csrf": token, "name": "Weekly market", "city": ""})
    with app.app_context():
        sl = ShoppingList.query.filter_by(name="Weekly market").first()
        products = FoodProduct.query.filter(FoodProduct.name.in_(["Papa criolla limpia", "Arroz de primera"])).all()
        list_id, ids = sl.id, [p.id for p in products]
    assert len(ids) == 2
    for pid in ids:
        owner.post(f"/prices/lists/{list_id}/items", data={"_csrf": token, "product_id": pid, "quantity": "4"})
    bad = owner.post(f"/prices/lists/{list_id}/items", data={"_csrf": token, "product_id": ids[0], "quantity": "-1"},
                     follow_redirects=True)
    assert b"quantity must be a number" in bad.data
    html = owner.get(f"/prices?list={list_id}").get_data(as_text=True)
    assert "Cost at the cheapest markets" in html and "You save" in html
    pdf = owner.get(f"/prices/lists/{list_id}/pdf")
    assert pdf.status_code == 200 and pdf.mimetype == "application/pdf" and pdf.data.startswith(b"%PDF")
    assert other.get(f"/prices?list={list_id}").status_code == 403
    assert other.get(f"/prices/lists/{list_id}/pdf").status_code == 403
    other_token = csrf(other, "/prices")
    assert other.post(f"/prices/lists/{list_id}/delete", data={"_csrf": other_token}).status_code == 403


def test_legal_pages_consent_and_security_headers(app):
    c = app.test_client()
    for page in ("privacy", "terms", "cookies"):
        assert c.get(f"/legal/{page}").status_code == 200
    assert c.get("/legal/other").status_code == 404
    r = c.get("/")
    assert "nonce-" in r.headers["Content-Security-Policy"] and "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert b"cookie-banner" in r.data
    data = {"full_name": "No Consent", "email": "noconsent@test.co", "password": PASSWORD, "confirm": PASSWORD,
            "_csrf": csrf(c, "/register")}
    c.post("/register", data=data)
    with app.app_context():
        assert User.query.filter_by(email="noconsent@test.co").first() is None


def test_export_and_delete_my_account(app):
    c = app.test_client()
    register(c, "leaving@test.co")
    export = c.get("/account/export")
    body = export.get_data(as_text=True)
    assert export.status_code == 200 and "leaving@test.co" in body and "password_hash" not in body
    token = csrf(c, "/account")
    c.post("/account/delete", data={"_csrf": token, "confirm_text": "DELETE", "password": "wrong"})
    with app.app_context():
        assert User.query.filter_by(email="leaving@test.co").first() is not None
    c.post("/account/delete", data={"_csrf": token, "confirm_text": "DELETE", "password": PASSWORD})
    with app.app_context():
        assert User.query.filter_by(email="leaving@test.co").first() is None
    assert c.get("/prices").status_code == 302


def test_failed_logins_are_throttled_by_email(app):
    for _ in range(5):
        c = app.test_client()   # a new client each time: clearing cookies must not reset the counter
        c.post("/login", data={"_csrf": csrf(c), "email": "admin@test.co", "password": "wrong-pass1"})
    c = app.test_client()
    r = c.post("/login", data={"_csrf": csrf(c), "email": "admin@test.co", "password": PASSWORD})
    assert r.status_code == 429
