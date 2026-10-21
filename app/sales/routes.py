"""Sales management blueprint: record transactions, trends, CSV export."""
from __future__ import annotations

import io
from datetime import date, datetime, timedelta

import pandas as pd
from flask import (Blueprint, Response, current_app, flash, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from wtforms import (DateField, DecimalField, IntegerField, SelectField,
                     StringField, SubmitField, TextAreaField)
from wtforms.validators import (DataRequired, Length, NumberRange, Optional,
                                ValidationError)

from app.extensions import db
from app.models import Customer, Product, Sale, SaleStatus, utcnow
from app.services import DashboardService, ReportService
from app.utils import (active_required, log_activity, log_audit,
                       parse_date, visible_sale_query)

bp = Blueprint("sales", __name__, url_prefix="/sales")

SALE_STATUS_TONE = {"Completed": "success", "Pending": "warning", "Refunded": "danger"}


class SaleRecordForm(FlaskForm):
    customer_id = SelectField("Customer", coerce=int, validators=[DataRequired()])
    product_id = SelectField("Product", coerce=int, validators=[DataRequired()])
    quantity = IntegerField("Quantity", default=1,
                            validators=[DataRequired(), NumberRange(min=1, max=10_000)])
    amount = DecimalField("Unit amount", validators=[DataRequired(),
                                                     NumberRange(min=0, max=100_000_000)])
    sold_at = DateField("Sale date", format="%Y-%m-%d", validators=[Optional()])
    status = SelectField("Status", choices=[(s, s) for s in SaleStatus._value2member_map_])
    reference = StringField("Reference", validators=[Optional(), Length(max=60)])
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=2000)])
    submit = SubmitField("Record sale")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.customer_id.choices = [(0, "-- select customer --")] + \
            [(c.id, f"{c.name} ({c.company or 'individual'})")
             for c in Customer.query.order_by(Customer.name).limit(500)]
        self.product_id.choices = [(0, "-- select product --")] + \
            [(p.id, f"{p.name} — {p.price:,.2f}")
             for p in Product.query.filter_by(is_active=True).order_by(Product.name)]

    def validate_reference(self, field):  # noqa: N802
        ref = (field.data or "").strip()
        if ref and Sale.query.filter_by(reference=ref).first():
            raise ValidationError("That reference number already exists.")

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators=extra_validators)
        # The sentinel choice (0) is a valid choice, so the standard validators
        # cannot distinguish "nothing selected" from a real pick - check it
        # ourselves and report a friendly message instead of a generic one.
        valid = True
        if not self.customer_id.data or self.customer_id.data == 0:
            self.customer_id.errors = ("Select a customer.",)
            valid = False
        if not self.product_id.data or self.product_id.data == 0:
            self.product_id.errors = ("Select a product.",)
            valid = False
        return ok and valid


@bp.route("/")
@login_required
@active_required
def index():
    page = request.args.get("page", 1, type=int)
    status = request.args.get("status", "")
    start = parse_date(request.args.get("start"))
    end = parse_date(request.args.get("end"))

    query = visible_sale_query()
    if status in SaleStatus._value2member_map_:
        query = query.filter(Sale.status == status)
    if start:
        query = query.filter(Sale.sold_at >= datetime.combine(start, datetime.min.time()))
    if end:
        query = query.filter(Sale.sold_at < datetime.combine(end + timedelta(days=1),
                                                             datetime.min.time()))

    pagination = db.paginate(query.order_by(Sale.sold_at.desc()),
                             page=page,
                             per_page=current_app.config["SALES_PER_PAGE"],
                             error_out=False)

    completed = visible_sale_query().filter_by(status=SaleStatus.COMPLETED.value)
    total_revenue = sum(s.total for s in completed.all())
    count = completed.count()
    this_month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly = sum(s.total for s in completed.filter(Sale.sold_at >= this_month_start).all())
    trend = DashboardService.monthly_revenue(12)

    return render_template(
        "sales/list.html", title="Sales", pagination=pagination, sales=pagination.items,
        status=status, statuses=[(s, s) for s in SaleStatus._value2member_map_],
        start=start, end=end, status_tone=SALE_STATUS_TONE,
        total_revenue=round(total_revenue, 2),
        avg_deal=round(total_revenue / count, 2) if count else 0.0,
        monthly_revenue=round(monthly, 2), completed_count=count,
        trend=trend, form=SaleRecordForm())


@bp.route("/new", methods=["GET", "POST"])
@login_required
@active_required
def create():
    form = SaleRecordForm()
    if form.validate_on_submit():
        sale = Sale(
            customer_id=form.customer_id.data,
            product_id=form.product_id.data,
            quantity=form.quantity.data,
            amount=float(form.amount.data),
            sold_at=datetime.combine(form.sold_at.data, datetime.min.time())
            if form.sold_at.data else utcnow(),
            status=form.status.data,
            reference=(form.reference.data or "").strip() or None,
            notes=(form.notes.data or "").strip() or None,
            recorded_by=current_user.id,
        )
        db.session.add(sale)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("Could not record the sale (duplicate reference?).", "danger")
            return render_template("sales/form.html", title="Record sale", form=form)
        customer = sale.customer
        log_audit("sale_recorded", entity="Sale", entity_id=sale.id,
                  detail=f"{customer.name if customer else '?'} x{sale.quantity} "
                         f"= {sale.total:,.2f}")
        log_activity("sale_recorded",
                     f"Sale {sale.total:,.2f} recorded for "
                     f"{customer.name if customer else 'customer'}",
                     entity_type="Sale", entity_id=sale.id)
        flash("Sale recorded.", "success")
        return redirect(url_for("sales.index"))
    return render_template("sales/form.html", title="Record sale", form=form)


@bp.route("/<int:sale_id>/edit", methods=["GET", "POST"])
@login_required
@active_required
def edit(sale_id: int):
    sale = visible_sale_query().filter(Sale.id == sale_id).first_or_404()
    form = SaleRecordForm(obj=sale)
    if form.validate_on_submit():
        sale.customer_id = form.customer_id.data
        sale.product_id = form.product_id.data
        sale.quantity = form.quantity.data
        sale.amount = float(form.amount.data)
        sale.sold_at = (datetime.combine(form.sold_at.data, datetime.min.time())
                        if form.sold_at.data else sale.sold_at)
        sale.status = form.status.data
        sale.reference = (form.reference.data or "").strip() or None
        sale.notes = (form.notes.data or "").strip() or None
        db.session.commit()
        log_audit("sale_updated", entity="Sale", entity_id=sale.id)
        flash("Sale updated.", "success")
        return redirect(url_for("sales.index"))
    return render_template("sales/form.html", title=f"Edit sale #{sale.id}",
                           form=form, sale=sale)


@bp.route("/<int:sale_id>/delete", methods=["POST"])
@login_required
@active_required
def delete(sale_id: int):
    sale = visible_sale_query().filter(Sale.id == sale_id).first_or_404()
    if not current_user.is_admin:
        flash("Only administrators can delete sales records.", "warning")
        return redirect(url_for("sales.index"))
    total = sale.total
    db.session.delete(sale)
    db.session.commit()
    log_audit("sale_deleted", entity="Sale", entity_id=sale_id, detail=f"{total:,.2f}")
    flash(f"Sale #{sale_id} deleted.", "success")
    return redirect(url_for("sales.index"))


@bp.route("/export.csv")
@login_required
@active_required
def export_csv():
    start = parse_date(request.args.get("start"))
    end = parse_date(request.args.get("end"))
    df = ReportService.sales(start, end)
    if df.empty:
        flash("No sales in the selected period to export.", "info")
        return redirect(url_for("sales.index"))
    return _csv_response(df, f"databreakers_sales_{date.today():%Y%m%d}.csv")


def _csv_response(df: pd.DataFrame, filename: str) -> Response:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    resp = Response(buf.getvalue(), mimetype="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = f"attachment; filename={filename}"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
