"""Product catalogue blueprint: CRUD + performance summary."""
from __future__ import annotations

from flask import (Blueprint, flash, redirect, render_template, request,
                   url_for)
from flask_login import login_required
from flask_wtf import FlaskForm
from wtforms import (BooleanField, DecimalField, StringField, SubmitField,
                     TextAreaField)
from wtforms.validators import (DataRequired, Length, NumberRange, Optional,
                                ValidationError)

from app.extensions import db
from app.models import Product, Sale, SaleStatus
from app.utils import active_required, admin_required, log_audit

bp = Blueprint("products", __name__, url_prefix="/products")


class ProductForm(FlaskForm):
    name = StringField("Product name", validators=[DataRequired(), Length(min=2, max=160)])
    sku = StringField("SKU", validators=[Optional(), Length(max=60)])
    category = StringField("Category", default="General",
                           validators=[DataRequired(), Length(max=80)])
    price = DecimalField("Price", default=0.0,
                         validators=[DataRequired(),
                                     NumberRange(min=0, max=100_000_000)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=2000)])
    is_active = BooleanField("Active", default=True)
    submit = SubmitField("Save product")

    def __init__(self, *args, **kwargs):
        self.original_name = kwargs.pop("original_name", None)
        super().__init__(*args, **kwargs)

    def validate_name(self, field):  # noqa: N802
        if self.original_name and field.data.strip().lower() == self.original_name.lower():
            return
        if Product.query.filter(db.func.lower(Product.name) == field.data.strip().lower()).first():
            raise ValidationError("A product with this name already exists.")

    def validate_sku(self, field):  # noqa: N802
        sku = (field.data or "").strip()
        if not sku:
            return
        if Product.query.filter(db.func.lower(Product.sku) == sku.lower()).first():
            raise ValidationError("A product with this SKU already exists.")


@bp.route("/")
@login_required
@active_required
def index():
    products = Product.query.order_by(Product.name).all()
    rows = []
    for p in products:
        completed = p.sales.filter_by(status=SaleStatus.COMPLETED.value)
        rows.append({
            "product": p,
            "units": completed.count(),
            "revenue": round(sum(s.total for s in completed.all()), 2),
        })
    total_revenue = round(sum(r["revenue"] for r in rows), 2)
    return render_template("products/list.html", title="Products",
                           rows=rows, total_revenue=total_revenue)


@bp.route("/new", methods=["GET", "POST"])
@login_required
@admin_required
def create():
    form = ProductForm()
    if form.validate_on_submit():
        product = _from_form(Product(), form)
        db.session.add(product)
        db.session.commit()
        log_audit("product_created", entity="Product", entity_id=product.id,
                  detail=product.name)
        flash("Product added.", "success")
        return redirect(url_for("products.index"))
    return render_template("products/form.html", title="New product",
                           form=form, product=None)


@bp.route("/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit(product_id: int):
    product = Product.query.get_or_404(product_id)
    form = ProductForm(obj=product, original_name=product.name)
    if form.validate_on_submit():
        _from_form(product, form)
        db.session.commit()
        log_audit("product_updated", entity="Product", entity_id=product.id)
        flash("Product updated.", "success")
        return redirect(url_for("products.index"))
    return render_template("products/form.html", title=f"Edit {product.name}",
                           form=form, product=product)


@bp.route("/<int:product_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete(product_id: int):
    product = Product.query.get_or_404(product_id)
    if product.sales.count():
        product.is_active = False
        db.session.commit()
        log_audit("product_archived", entity="Product", entity_id=product.id)
        flash("This product has recorded sales, so it was archived (deactivated) "
              "instead of deleted.", "info")
    else:
        name = product.name
        db.session.delete(product)
        db.session.commit()
        log_audit("product_deleted", entity="Product", entity_id=product_id, detail=name)
        flash(f"Product “{name}” deleted.", "success")
    return redirect(url_for("products.index"))


def _from_form(product: Product, form: ProductForm) -> Product:
    product.name = form.name.data.strip()
    product.sku = (form.sku.data or "").strip() or None
    product.category = form.category.data.strip() or "General"
    product.price = float(form.price.data or 0)
    product.description = (form.description.data or "").strip() or None
    product.is_active = bool(form.is_active.data)
    return product
