"""Customer management blueprint: CRUD, profile, interactions, purchases."""
from __future__ import annotations

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy import or_
from wtforms import (SelectField, StringField, SubmitField, TextAreaField)
from wtforms.validators import (DataRequired, Email, Length, Optional,
                                ValidationError)

from app.extensions import db
from app.models import (Customer, CustomerInteraction, Lead, Product, Sale,
                        SaleStatus, utcnow)
from app.utils import (active_required, log_activity, log_audit,
                       visible_customer_query)

bp = Blueprint("customers", __name__, url_prefix="/customers")

SEGMENTS = ["Standard", "Premium", "Enterprise", "SMB", "Trial"]
INTERACTION_KINDS = ["Call", "Meeting", "Email", "Demo", "Note"]


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------
class CustomerForm(FlaskForm):
    name = StringField("Customer name", validators=[DataRequired(), Length(min=2, max=160)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    phone = StringField("Phone", validators=[Optional(), Length(max=40)])
    company = StringField("Company", validators=[Optional(), Length(max=160)])
    city = StringField("City", validators=[Optional(), Length(max=120)])
    country = StringField("Country", validators=[Optional(), Length(max=120)])
    industry = StringField("Industry", validators=[Optional(), Length(max=120)])
    segment = SelectField("Segment", choices=[(s, s) for s in SEGMENTS])
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=5000)])
    submit = SubmitField("Save customer")

    def __init__(self, *args, **kwargs):
        self.original_email: str | None = kwargs.pop("original_email", None)
        super().__init__(*args, **kwargs)

    def validate_email(self, field):  # noqa: N802
        email = (field.data or "").strip().lower()
        if self.original_email and email == self.original_email.lower():
            return
        clash = Customer.query.filter(
            db.func.lower(Customer.email) == email).first()
        if clash:
            raise ValidationError(
                f"A customer with this email already exists (#{clash.id} - {clash.name}).")


class InteractionForm(FlaskForm):
    kind = SelectField("Type", choices=[(k, k) for k in INTERACTION_KINDS])
    summary = TextAreaField("Summary", validators=[DataRequired(), Length(min=3, max=2000)])
    submit = SubmitField("Log interaction")


class SaleForm(FlaskForm):
    """Quick sale capture from the customer profile page."""

    product_id = SelectField("Product", coerce=int, validators=[DataRequired()])
    quantity = StringField("Quantity", default="1", validators=[DataRequired()])
    amount = StringField("Unit amount", validators=[DataRequired()])
    status = SelectField("Status", choices=[(s, s) for s in
                                            ("Completed", "Pending", "Refunded")])
    sold_at = StringField("Date (YYYY-MM-DD)", validators=[Optional()])
    submit = SubmitField("Record sale")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.product_id.choices = [(0, "-- select product --")] + \
            [(p.id, f"{p.name} ({p.price:,.2f})")
             for p in Product.query.filter_by(is_active=True).order_by(Product.name)]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@bp.route("/")
@login_required
@active_required
def index():
    page = request.args.get("page", 1, type=int)
    search = (request.args.get("q", "") or "").strip()
    segment = request.args.get("segment", "")

    query = visible_customer_query()
    if search:
        like = f"%{search}%"
        query = query.filter(or_(
            Customer.name.ilike(like), Customer.email.ilike(like),
            Customer.company.ilike(like), Customer.city.ilike(like)))
    if segment:
        query = query.filter(Customer.segment == segment)

    pagination = db.paginate(query.order_by(Customer.created_at.desc()),
                             page=page,
                             per_page=current_app.config["CUSTOMERS_PER_PAGE"],
                             error_out=False)
    return render_template("customers/list.html", title="Customers",
                           pagination=pagination, customers=pagination.items,
                           search=search, segment=segment, segments=SEGMENTS)


@bp.route("/new", methods=["GET", "POST"])
@login_required
@active_required
def create():
    form = CustomerForm()
    if form.validate_on_submit():
        customer = _customer_from_form(Customer(), form)
        customer.created_by = current_user.id
        db.session.add(customer)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("Could not save the customer (possible duplicate).", "danger")
            return render_template("customers/form.html", title="New customer",
                                   form=form, customer=None)
        log_audit("customer_created", entity="Customer", entity_id=customer.id,
                  detail=customer.email)
        log_activity("customer_created", f"New customer: {customer.name}",
                     entity_type="Customer", entity_id=customer.id)
        flash("Customer created.", "success")
        return redirect(url_for("customers.detail", customer_id=customer.id))
    return render_template("customers/form.html", title="New customer",
                           form=form, customer=None)


@bp.route("/<int:customer_id>")
@login_required
@active_required
def detail(customer_id: int):
    customer = _get_customer(customer_id)
    interactions = (customer.interactions
                    .order_by(CustomerInteraction.created_at.desc()).limit(20).all())
    sales = customer.sales.order_by(Sale.sold_at.desc()).all()
    completed = [s for s in sales if s.status == SaleStatus.COMPLETED.value]
    revenue = sum(s.total for s in completed)
    return render_template(
        "customers/detail.html", title=customer.name, customer=customer,
        interactions=interactions, sales=sales, revenue=round(revenue, 2),
        interaction_form=InteractionForm(), sale_form=SaleForm(),
        activity=_activity_timeline(customer))


@bp.route("/<int:customer_id>/edit", methods=["GET", "POST"])
@login_required
@active_required
def edit(customer_id: int):
    customer = _get_customer(customer_id)
    form = CustomerForm(obj=customer, original_email=customer.email)
    if form.validate_on_submit():
        _customer_from_form(customer, form)
        db.session.commit()
        log_audit("customer_updated", entity="Customer", entity_id=customer.id)
        flash("Customer updated.", "success")
        return redirect(url_for("customers.detail", customer_id=customer.id))
    return render_template("customers/form.html", title=f"Edit {customer.name}",
                           form=form, customer=customer)


@bp.route("/<int:customer_id>/delete", methods=["POST"])
@login_required
@active_required
def delete(customer_id: int):
    customer = _get_customer(customer_id)
    if customer.sales.count():
        flash("This customer has recorded sales and cannot be deleted. "
              "Archive it instead.", "warning")
        return redirect(url_for("customers.detail", customer_id=customer.id))
    # Unlink any leads that point at this customer before deleting.
    Lead.query.filter_by(customer_id=customer.id).update(
        {"customer_id": None, "status": "Lost"}, synchronize_session=False)
    name = customer.name
    db.session.delete(customer)
    db.session.commit()
    log_audit("customer_deleted", entity="Customer", entity_id=customer_id, detail=name)
    flash(f"Customer “{name}” deleted.", "success")
    return redirect(url_for("customers.index"))


@bp.route("/<int:customer_id>/interactions", methods=["POST"])
@login_required
@active_required
def add_interaction(customer_id: int):
    customer = _get_customer(customer_id)
    form = InteractionForm()
    if form.validate_on_submit():
        db.session.add(CustomerInteraction(
            customer_id=customer.id, user_id=current_user.id,
            kind=form.kind.data, summary=form.summary.data.strip()))
        db.session.commit()
        log_activity("customer_interaction",
                     f"{form.kind.data} logged for {customer.name}",
                     entity_type="Customer", entity_id=customer.id)
        flash("Interaction logged.", "success")
    else:
        flash("Interaction summary is required.", "danger")
    return redirect(url_for("customers.detail", customer_id=customer.id))


@bp.route("/<int:customer_id>/sales", methods=["POST"])
@login_required
@active_required
def add_sale(customer_id: int):
    customer = _get_customer(customer_id)
    form = SaleForm()
    if form.validate_on_submit():
        product = db.session.get(Product, form.product_id.data)
        try:
            quantity = int(form.quantity.data)
            amount = float(str(form.amount.data).replace(",", ""))
        except (TypeError, ValueError):
            flash("Quantity and amount must be numbers.", "danger")
            return redirect(url_for("customers.detail", customer_id=customer.id))
        if product is None or quantity < 1 or amount < 0:
            flash("Choose a valid product, quantity and amount.", "danger")
            return redirect(url_for("customers.detail", customer_id=customer.id))
        from app.utils import parse_date

        sold_at = parse_date(form.sold_at.data) or utcnow()
        sale = Sale(customer_id=customer.id, product_id=product.id,
                    quantity=quantity, amount=amount, status=form.status.data,
                    sold_at=sold_at, recorded_by=current_user.id)
        db.session.add(sale)
        db.session.commit()
        log_audit("sale_recorded", entity="Sale", entity_id=sale.id,
                  detail=f"{customer.name} / {product.name} x{quantity}")
        log_activity("sale_recorded",
                     f"Sale of {product.name} x{quantity} to {customer.name}",
                     entity_type="Customer", entity_id=customer.id)
        flash("Sale recorded.", "success")
    else:
        flash("Select a product and enter a valid amount.", "danger")
    return redirect(url_for("customers.detail", customer_id=customer.id))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_customer(customer_id: int) -> Customer:
    return visible_customer_query().filter(Customer.id == customer_id).first_or_404()


def _customer_from_form(customer: Customer, form: CustomerForm) -> Customer:
    customer.name = form.name.data.strip()
    customer.email = form.email.data.strip().lower()
    customer.phone = (form.phone.data or "").strip() or None
    customer.company = (form.company.data or "").strip() or None
    customer.city = (form.city.data or "").strip() or None
    customer.country = (form.country.data or "").strip() or None
    customer.industry = (form.industry.data or "").strip() or None
    customer.segment = form.segment.data
    customer.notes = (form.notes.data or "").strip() or customer.notes
    return customer


def _activity_timeline(customer: Customer) -> list[dict]:
    events = []
    for i in customer.interactions.order_by(
            CustomerInteraction.created_at.desc()).limit(6):
        events.append({"when": i.created_at, "what": f"{i.kind}: {i.summary}",
                       "who": i.user.name if i.user else "System"})
    for s in customer.sales.order_by(Sale.sold_at.desc()).limit(6):
        events.append({"when": s.sold_at,
                       "what": f"Sale: {s.product.name if s.product else '—'} "
                               f"x{s.quantity} = {s.total:,.2f} ({s.status})",
                       "who": s.recorder.name if s.recorder else "System"})
    events.sort(key=lambda e: e["when"] or utcnow(), reverse=True)
    return events[:10]
