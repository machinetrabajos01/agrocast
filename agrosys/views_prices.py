"""Food price catalog, product pages, shopping lists with price calculation and the PDF of the cheapest purchase."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Response, abort, flash, g, redirect, render_template, request, url_for

from . import prices
from .db import ShoppingItem, ShoppingList, FoodProduct, db
from .pdf import shopping_list_pdf
from .security import permission_required
from .services import record_event

MAX_LISTS = 20
MAX_ITEMS = 60


def _own_list(list_id) -> ShoppingList:
    sl = db.session.get(ShoppingList, list_id) or abort(404)
    if sl.owner_id != g.user.id:
        abort(403)
    return sl


def _text(name, maximum, required=False):
    value = " ".join((request.form.get(name) or "").split())[:maximum]
    if required and len(value) < 2:
        raise ValueError(f"The {name.replace('_', ' ')} is required.")
    return value


def register_routes(app):
    @app.route("/prices")
    @permission_required("market.view")
    def prices_home():
        lists = ShoppingList.query.filter_by(owner_id=g.user.id).order_by(ShoppingList.updated_at.desc()).all()
        selected = None
        if request.args.get("list"):
            selected = _own_list(int(request.args["list"]) if request.args["list"].isdigit() else abort(404))
        elif lists:
            selected = lists[0]
        lines, totals = prices.calculate(selected) if selected else ([], None)
        products = prices.catalog()
        return render_template("prices/calculator.html", lists=lists, selected=selected, lines=lines, totals=totals,
                               products=products, groups=prices.groups(), cities=prices.cities(),
                               refresh=prices.last_refresh(), latest_daily=prices.latest_daily_date(),
                               title="Price calculator")

    @app.route("/prices/lists", methods=["POST"])
    @permission_required("market.view")
    def prices_list_create():
        try:
            name = _text("name", 80, required=True)
        except ValueError as e:
            flash(str(e), "danger")
            return redirect(url_for("prices_home"))
        if ShoppingList.query.filter_by(owner_id=g.user.id).count() >= MAX_LISTS:
            flash(f"You can keep up to {MAX_LISTS} lists. Delete one first.", "warning")
            return redirect(url_for("prices_home"))
        city = _text("city", 80)
        sl = ShoppingList(owner_id=g.user.id, name=name, city=city if city in prices.cities() else None)
        db.session.add(sl)
        db.session.commit()
        record_event("shopping_list.created", list_id=sl.id)
        return redirect(url_for("prices_home", list=sl.id))

    @app.route("/prices/lists/<int:list_id>/settings", methods=["POST"])
    @permission_required("market.view")
    def prices_list_settings(list_id):
        sl = _own_list(list_id)
        try:
            sl.name = _text("name", 80, required=True)
        except ValueError as e:
            flash(str(e), "danger")
            return redirect(url_for("prices_home", list=sl.id))
        city = _text("city", 80)
        sl.city = city if city in prices.cities() else None
        db.session.commit()
        flash("List updated.", "success")
        return redirect(url_for("prices_home", list=sl.id))

    @app.route("/prices/lists/<int:list_id>/delete", methods=["POST"])
    @permission_required("market.view")
    def prices_list_delete(list_id):
        sl = _own_list(list_id)
        db.session.delete(sl)
        db.session.commit()
        record_event("shopping_list.deleted", list_id=list_id)
        flash("List deleted.", "info")
        return redirect(url_for("prices_home"))

    @app.route("/prices/lists/<int:list_id>/items", methods=["POST"])
    @permission_required("market.view")
    def prices_item_add(list_id):
        sl = _own_list(list_id)
        raw_id = request.form.get("product_id", "")
        product = db.session.get(FoodProduct, int(raw_id)) if raw_id.isdigit() else None
        try:
            qty = Decimal((request.form.get("quantity") or "").replace(",", "."))
        except InvalidOperation:
            qty = Decimal(0)
        if product is None:
            flash("Choose a product from the list.", "danger")
        elif not (Decimal("0.01") <= qty <= Decimal("100000")):
            flash("The quantity must be a number between 0.01 and 100,000.", "danger")
        else:
            item = ShoppingItem.query.filter_by(list_id=sl.id, product_id=product.id).first()
            if item:
                item.quantity = qty
                flash(f"{product.name}: quantity updated.", "success")
            elif len(sl.items) >= MAX_ITEMS:
                flash(f"A list can have up to {MAX_ITEMS} products.", "warning")
                return redirect(url_for("prices_home", list=sl.id))
            else:
                db.session.add(ShoppingItem(list_id=sl.id, product_id=product.id, quantity=qty))
                flash(f"{product.name} added.", "success")
            sl.updated_at = db.func.now()
            db.session.commit()
        return redirect(url_for("prices_home", list=sl.id) + "#items")

    @app.route("/prices/lists/<int:list_id>/items/<int:item_id>/delete", methods=["POST"])
    @permission_required("market.view")
    def prices_item_delete(list_id, item_id):
        sl = _own_list(list_id)
        item = ShoppingItem.query.filter_by(id=item_id, list_id=sl.id).first() or abort(404)
        db.session.delete(item)
        db.session.commit()
        return redirect(url_for("prices_home", list=sl.id) + "#items")

    @app.route("/prices/lists/<int:list_id>/pdf")
    @permission_required("market.view")
    def prices_list_pdf(list_id):
        sl = _own_list(list_id)
        lines, totals = prices.calculate(sl)
        if not lines:
            flash("Add at least one product before downloading the PDF.", "warning")
            return redirect(url_for("prices_home", list=sl.id))
        data = shopping_list_pdf(sl, lines, totals, g.user.full_name)
        record_event("shopping_list.pdf", list_id=sl.id, items=len(lines))
        safe = "".join(ch for ch in sl.name if ch.isalnum() or ch in " -_").strip().replace(" ", "_") or "list"
        return Response(data, mimetype="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="agrocast_{safe[:40]}.pdf"',
                                 "Cache-Control": "no-store"})

    @app.route("/prices/catalog")
    @permission_required("market.view")
    def prices_catalog():
        q = (request.args.get("q") or "")[:60]
        group = (request.args.get("group") or "")[:60]
        products = prices.catalog(q, group)
        current = prices.bulk_current(products)
        rows = [{"product": p, **current.get(p.id, {"best": None, "source": None, "date": None, "markets": 0})}
                for p in products]
        return render_template("prices/catalog.html", rows=rows, q=q, group=group, groups=prices.groups(),
                               latest_daily=prices.latest_daily_date(), refresh=prices.last_refresh(),
                               title="Food prices today")

    @app.route("/prices/product/<int:product_id>")
    @permission_required("market.view")
    def prices_product(product_id):
        product = db.session.get(FoodProduct, product_id) or abort(404)
        detail = prices.product_detail(product)
        lists = ShoppingList.query.filter_by(owner_id=g.user.id).order_by(ShoppingList.updated_at.desc()).all()
        return render_template("prices/product.html", p=product, d=detail, lists=lists, title=product.name)
