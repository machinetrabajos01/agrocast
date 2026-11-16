"""Business process routes: farms, purchase plans, model evaluation, decisions and incidents."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from .db import (CATEGORIES, CATEGORY_LABELS, DECISION_LABELS, DECISIONS, INCIDENT_KINDS, INCIDENT_STATUSES,
                 Farm, Incident, Inference, PlanItem, ProductPrice, PurchasePlan, User, db, utcnow)
from .security import can, permission_required
from .services import (BusinessRuleError, evaluate_plan, first_of_month, plan_projection, planning_window,
                       record_event)


def _number(value, field, minimum=0, maximum=10**9):
    try:
        n = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        raise BusinessRuleError(f"{field} must be a number.")
    if not (Decimal(minimum) < n <= Decimal(maximum)):
        raise BusinessRuleError(f"{field} must be greater than {minimum} and at most {maximum:,}.")
    return n


def _text(value, field, lo, hi, required=True):
    value = (value or "").strip()
    if not value and not required:
        return None
    if not lo <= len(value) <= hi:
        raise BusinessRuleError(f"{field} must have between {lo} and {hi} characters.")
    return value


def _own_farm(farm_id) -> Farm:
    farm = db.session.get(Farm, farm_id) or abort(404)
    if farm.owner_id != g.user.id and g.user.role != "admin":
        abort(403)
    return farm


def _plan(plan_id, write=False) -> PurchasePlan:
    plan = db.session.get(PurchasePlan, plan_id) or abort(404)
    owner = plan.owner_id == g.user.id
    if write and not (owner or g.user.role == "admin"):
        abort(403)
    if not (owner or can("plans.view_all")):
        abort(403)
    return plan


def register_routes(app):

    # ------------------------------------------------------------ farms (CRUD)

    @app.route("/farms", methods=["GET", "POST"])
    @permission_required("farms.manage_own")
    def farms():
        if request.method == "POST":
            f = request.form
            try:
                farm = Farm(owner_id=g.user.id, name=_text(f.get("name"), "Farm name", 2, 80),
                            department=_text(f.get("department"), "Department", 2, 60),
                            municipality=_text(f.get("municipality"), "Municipality", 2, 60, required=False),
                            main_crop=_text(f.get("main_crop"), "Main crop", 2, 60),
                            area_ha=_number(f.get("area_ha"), "Area (ha)", 0, 100000))
                db.session.add(farm)
                db.session.commit()
                record_event("farm.created", farm_id=farm.id)
                flash(f"Farm {farm.name} registered.", "success")
                return redirect(url_for("farms"))
            except BusinessRuleError as e:
                flash(str(e), "danger")
            except IntegrityError:
                db.session.rollback()
                flash("You already have a farm with that name.", "danger")
        rows = (Farm.query.filter_by(owner_id=g.user.id).order_by(Farm.created_at.desc()).all())
        return render_template("ops/farms.html", farms=rows, form=request.form)

    @app.route("/farms/<int:farm_id>/edit", methods=["GET", "POST"])
    @permission_required("farms.manage_own")
    def farm_edit(farm_id):
        farm = _own_farm(farm_id)
        if request.method == "POST":
            f = request.form
            try:
                farm.name = _text(f.get("name"), "Farm name", 2, 80)
                farm.department = _text(f.get("department"), "Department", 2, 60)
                farm.municipality = _text(f.get("municipality"), "Municipality", 2, 60, required=False)
                farm.main_crop = _text(f.get("main_crop"), "Main crop", 2, 60)
                farm.area_ha = _number(f.get("area_ha"), "Area (ha)", 0, 100000)
                db.session.commit()
                record_event("farm.updated", farm_id=farm.id)
                flash("Farm updated.", "success")
                return redirect(url_for("farms"))
            except BusinessRuleError as e:
                db.session.rollback()
                flash(str(e), "danger")
            except IntegrityError:
                db.session.rollback()
                flash("You already have a farm with that name.", "danger")
        return render_template("ops/farm_edit.html", farm=farm)

    @app.route("/farms/<int:farm_id>/delete", methods=["POST"])
    @permission_required("farms.manage_own")
    def farm_delete(farm_id):
        farm = _own_farm(farm_id)
        if farm.plans:
            flash("This farm has purchase plans. Cancel or delete them before removing the farm.", "danger")
            return redirect(url_for("farms"))
        db.session.delete(farm)
        db.session.commit()
        record_event("farm.deleted", farm_id=farm_id)
        flash("Farm removed.", "success")
        return redirect(url_for("farms"))

    # ------------------------------------------------------------ purchase plans

    @app.route("/plans")
    @permission_required("market.view")
    def plans():
        status = request.args.get("status", "")
        q = PurchasePlan.query
        scope = "all" if can("plans.view_all") and request.args.get("scope") != "mine" else "mine"
        if scope == "mine":
            q = q.filter_by(owner_id=g.user.id)
        if status:
            q = q.filter_by(status=status)
        rows = q.order_by(PurchasePlan.created_at.desc()).all()
        return render_template("ops/plans.html", plans=rows, status=status, scope=scope,
                               category_labels=CATEGORY_LABELS, decision_labels=DECISION_LABELS)

    @app.route("/plans/new", methods=["GET", "POST"])
    @permission_required("plans.manage_own")
    def plan_new():
        own_farms = Farm.query.filter_by(owner_id=g.user.id).order_by(Farm.name).all()
        if not own_farms:
            flash("Register a farm before creating a purchase plan.", "warning")
            return redirect(url_for("farms"))
        try:
            start, end = planning_window()
        except BusinessRuleError as e:
            flash(str(e), "danger")
            return redirect(url_for("plans"))
        if request.method == "POST":
            f = request.form
            try:
                farm = _own_farm(int(f.get("farm_id", 0)))
                category = f.get("category")
                if category not in CATEGORIES:
                    raise BusinessRuleError("Choose a valid input category.")
                month = first_of_month(f.get("planned_month", ""))
                if not start <= month <= end:
                    raise BusinessRuleError(f"The planned month must be between {start:%Y-%m} and {end:%Y-%m}.")
                plan = PurchasePlan(owner_id=g.user.id, farm_id=farm.id, category=category, planned_month=month,
                                    title=_text(f.get("title"), "Title", 3, 100))
                db.session.add(plan)
                db.session.commit()
                record_event("plan.created", plan_id=plan.id)
                flash("Plan created. Add the products you intend to buy.", "success")
                return redirect(url_for("plan_detail", plan_id=plan.id))
            except (BusinessRuleError, ValueError) as e:
                flash(str(e), "danger")
        return render_template("ops/plan_form.html", plan=None, farms=own_farms, categories=CATEGORIES,
                               category_labels=CATEGORY_LABELS, start=start, end=end, form=request.form)

    @app.route("/plans/<int:plan_id>/edit", methods=["GET", "POST"])
    @permission_required("plans.manage_own")
    def plan_edit(plan_id):
        plan = _plan(plan_id, write=True)
        if plan.status not in ("draft", "evaluated", "deferred"):
            flash("Approved or closed plans cannot be edited.", "danger")
            return redirect(url_for("plan_detail", plan_id=plan.id))
        start, end = planning_window()
        own_farms = Farm.query.filter_by(owner_id=plan.owner_id).order_by(Farm.name).all()
        if request.method == "POST":
            f = request.form
            try:
                farm = _own_farm(int(f.get("farm_id", 0)))
                month = first_of_month(f.get("planned_month", ""))
                if not start <= month <= end:
                    raise BusinessRuleError(f"The planned month must be between {start:%Y-%m} and {end:%Y-%m}.")
                plan.title = _text(f.get("title"), "Title", 3, 100)
                plan.farm_id = farm.id
                if month != plan.planned_month:
                    plan.planned_month = month
                    plan.status, plan.recommendation, plan.decision = "draft", None, None
                db.session.commit()
                record_event("plan.updated", plan_id=plan.id)
                flash("Plan updated.", "success")
                return redirect(url_for("plan_detail", plan_id=plan.id))
            except (BusinessRuleError, ValueError) as e:
                db.session.rollback()
                flash(str(e), "danger")
        return render_template("ops/plan_form.html", plan=plan, farms=own_farms, categories=CATEGORIES,
                               category_labels=CATEGORY_LABELS, start=start, end=end, form=request.form)

    @app.route("/plans/<int:plan_id>")
    @permission_required("market.view")
    def plan_detail(plan_id):
        plan = _plan(plan_id)
        products = (ProductPrice.query.filter_by(category=plan.category)
                    .order_by(ProductPrice.product, ProductPrice.presentation).all())
        history = (Inference.query.filter_by(plan_id=plan.id).order_by(Inference.created_at.desc()).all())
        return render_template("ops/plan_detail.html", plan=plan, products=products, history=history,
                               projection=plan_projection(plan), decisions=DECISIONS,
                               decision_labels=DECISION_LABELS, category_labels=CATEGORY_LABELS,
                               editable=plan.status in ("draft", "evaluated", "deferred"),
                               is_owner=plan.owner_id == g.user.id or g.user.role == "admin")

    @app.route("/plans/<int:plan_id>/items", methods=["POST"])
    @permission_required("plans.manage_own")
    def plan_item_add(plan_id):
        plan = _plan(plan_id, write=True)
        try:
            if plan.status not in ("draft", "evaluated", "deferred"):
                raise BusinessRuleError("Products can only be changed while the plan is not approved.")
            product = db.session.get(ProductPrice, int(request.form.get("product_price_id", 0)))
            if not product or product.category != plan.category:
                raise BusinessRuleError("Choose a product from the plan category.")
            qty = _number(request.form.get("quantity"), "Quantity", 0, 100000)
            existing = PlanItem.query.filter_by(plan_id=plan.id, product_price_id=product.id).first()
            if existing:
                existing.quantity = qty
            else:
                # Business rule: the unit price comes from the latest SIPSA record, not from the user.
                db.session.add(PlanItem(plan_id=plan.id, product_price_id=product.id, quantity=qty,
                                        unit_price=product.price))
            plan.status, plan.recommendation, plan.decision = "draft", None, None
            db.session.commit()
            record_event("plan.item_saved", plan_id=plan.id, product_price_id=product.id)
            flash("Product saved. Evaluate the plan again to update the recommendation.", "success")
        except (BusinessRuleError, ValueError) as e:
            db.session.rollback()
            flash(str(e), "danger")
        return redirect(url_for("plan_detail", plan_id=plan.id))

    @app.route("/plans/<int:plan_id>/items/<int:item_id>/delete", methods=["POST"])
    @permission_required("plans.manage_own")
    def plan_item_delete(plan_id, item_id):
        plan = _plan(plan_id, write=True)
        item = PlanItem.query.filter_by(id=item_id, plan_id=plan.id).first_or_404()
        if plan.status not in ("draft", "evaluated", "deferred"):
            flash("Products can only be changed while the plan is not approved.", "danger")
        else:
            db.session.delete(item)
            plan.status, plan.recommendation, plan.decision = "draft", None, None
            db.session.commit()
            record_event("plan.item_deleted", plan_id=plan.id, item_id=item_id)
            flash("Product removed.", "success")
        return redirect(url_for("plan_detail", plan_id=plan.id))

    @app.route("/plans/<int:plan_id>/evaluate", methods=["POST"])
    @permission_required("inference.plan")
    def plan_evaluate(plan_id):
        plan = _plan(plan_id, write=True)
        try:
            inf = evaluate_plan(plan)
            flash(f"Model evaluation completed: {DECISION_LABELS[inf.recommendation]}.", "success")
        except BusinessRuleError as e:
            db.session.rollback()
            flash(str(e), "danger")
        return redirect(url_for("plan_detail", plan_id=plan.id))

    @app.route("/plans/<int:plan_id>/decide", methods=["POST"])
    @permission_required("plans.manage_own")
    def plan_decide(plan_id):
        plan = _plan(plan_id, write=True)
        decision = request.form.get("decision")
        note = (request.form.get("decision_note") or "").strip()[:400]
        try:
            if plan.status != "evaluated" or not plan.last_inference:
                raise BusinessRuleError("The plan must be evaluated by the model before a decision is recorded.")
            if plan_projection(plan)["stale"]:
                raise BusinessRuleError("The active model version changed. Evaluate the plan again.")
            if decision not in DECISIONS:
                raise BusinessRuleError("Choose a valid decision.")
            if decision != plan.recommendation and len(note) < 10:
                raise BusinessRuleError("Explain (at least 10 characters) why the decision differs from the model.")
            plan.decision = decision
            plan.decision_note = note or None
            plan.status = "deferred" if decision == "defer" else "approved"
            db.session.commit()
            record_event("plan.decision", plan_id=plan.id, decision=decision, recommendation=plan.recommendation,
                         follows_model=decision == plan.recommendation)
            flash(f"Decision recorded: {DECISION_LABELS[decision]}.", "success")
        except BusinessRuleError as e:
            flash(str(e), "danger")
        return redirect(url_for("plan_detail", plan_id=plan.id))

    @app.route("/plans/<int:plan_id>/purchase", methods=["POST"])
    @permission_required("plans.manage_own")
    def plan_purchase(plan_id):
        plan = _plan(plan_id, write=True)
        try:
            if plan.status != "approved":
                raise BusinessRuleError("Only approved plans can be closed as purchased.")
            for item in plan.items:
                item.actual_unit_price = _number(request.form.get(f"actual_{item.id}"),
                                                 f"Actual price of {item.product.product}", 0, 10**9)
            plan.status = "purchased"
            plan.closed_at = utcnow()
            db.session.commit()
            record_event("plan.purchased", plan_id=plan.id, actual_cost=plan.actual_cost, planned_cost=plan.planned_cost)
            flash("Purchase registered. The plan is closed.", "success")
        except BusinessRuleError as e:
            db.session.rollback()
            flash(str(e), "danger")
        return redirect(url_for("plan_detail", plan_id=plan.id))

    @app.route("/plans/<int:plan_id>/cancel", methods=["POST"])
    @permission_required("plans.manage_own")
    def plan_cancel(plan_id):
        plan = _plan(plan_id, write=True)
        if not plan.is_open:
            flash("The plan is already closed.", "warning")
        else:
            plan.status = "cancelled"
            plan.closed_at = utcnow()
            db.session.commit()
            record_event("plan.cancelled", plan_id=plan.id)
            flash("Plan cancelled.", "success")
        return redirect(url_for("plan_detail", plan_id=plan.id))

    @app.route("/plans/<int:plan_id>/delete", methods=["POST"])
    @permission_required("plans.manage_own")
    def plan_delete(plan_id):
        plan = _plan(plan_id, write=True)
        if plan.status not in ("draft", "cancelled"):
            flash("Only draft or cancelled plans can be deleted; evaluated plans keep their history.", "danger")
            return redirect(url_for("plan_detail", plan_id=plan.id))
        db.session.delete(plan)
        db.session.commit()
        record_event("plan.deleted", plan_id=plan_id)
        flash("Plan deleted.", "success")
        return redirect(url_for("plans"))

    # ------------------------------------------------------------ inference history

    @app.route("/history")
    @permission_required("market.view")
    def history():
        q = Inference.query
        scope = "all" if can("inference.view_all") and request.args.get("scope") != "mine" else "mine"
        if scope == "mine":
            q = q.filter_by(user_id=g.user.id)
        rows = q.order_by(Inference.created_at.desc()).limit(300).all()
        return render_template("ops/history.html", rows=rows, scope=scope, decision_labels=DECISION_LABELS)

    @app.route("/history/<int:inference_id>")
    @permission_required("market.view")
    def inference_detail(inference_id):
        inf = db.session.get(Inference, inference_id) or abort(404)
        if inf.user_id != g.user.id and not can("inference.view_all"):
            abort(403)
        return render_template("ops/inference_detail.html", inf=inf, decision_labels=DECISION_LABELS)

    # ------------------------------------------------------------ incidents and feedback

    @app.route("/incidents", methods=["GET", "POST"])
    @permission_required("incidents.report")
    def incidents():
        if request.method == "POST":
            f = request.form
            try:
                kind = f.get("kind")
                severity = f.get("severity", "medium")
                if kind not in INCIDENT_KINDS or severity not in ("low", "medium", "high"):
                    raise BusinessRuleError("Choose a valid type and severity.")
                inc = Incident(reporter_id=g.user.id, kind=kind, severity=severity,
                               title=_text(f.get("title"), "Title", 5, 120),
                               description=_text(f.get("description"), "Description", 10, 2000))
                db.session.add(inc)
                db.session.commit()
                record_event("incident.reported", "warning" if severity == "high" else "info",
                             incident_id=inc.id, kind=kind, severity=severity)
                flash(f"Report #{inc.id} registered. You can follow its status here.", "success")
                return redirect(url_for("incidents"))
            except BusinessRuleError as e:
                flash(str(e), "danger")
        q = Incident.query
        manage = can("incidents.manage")
        if not manage:
            q = q.filter_by(reporter_id=g.user.id)
        status = request.args.get("status", "")
        if status in INCIDENT_STATUSES:
            q = q.filter_by(status=status)
        rows = q.order_by(Incident.created_at.desc()).all()
        staff = User.query.filter(User.role.in_(("admin", "analyst")), User.is_active.is_(True)).all() if manage else []
        counts = dict(db.session.query(Incident.status, func.count(Incident.id)).group_by(Incident.status).all())
        return render_template("ops/incidents.html", rows=rows, manage=manage, staff=staff, kinds=INCIDENT_KINDS,
                               statuses=INCIDENT_STATUSES, status=status, counts=counts, form=request.form)

    @app.route("/incidents/<int:incident_id>", methods=["POST"])
    @permission_required("incidents.manage")
    def incident_update(incident_id):
        inc = db.session.get(Incident, incident_id) or abort(404)
        f = request.form
        status = f.get("status", inc.status)
        if status not in INCIDENT_STATUSES:
            abort(400)
        resolution = (f.get("resolution") or "").strip()[:2000] or None
        if status in ("resolved", "closed") and not resolution:
            flash("Describe the resolution before resolving or closing the report.", "danger")
            return redirect(url_for("incidents"))
        assignee = f.get("assigned_to")
        inc.assigned_to = int(assignee) if assignee else None
        inc.status = status
        inc.resolution = resolution
        db.session.commit()
        record_event("incident.updated", incident_id=inc.id, status=status)
        flash(f"Report #{inc.id} updated.", "success")
        return redirect(url_for("incidents"))
