"""Lead management blueprint: full CRUD, search, filter, sort, pagination."""
from __future__ import annotations

from datetime import datetime

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import desc, func, or_

from app.extensions import db
from app.leads.forms import LeadForm, NoteForm
from app.models import Activity, Customer, Lead, LeadStatus, Notification, utcnow
from app.utils import (active_required, log_activity, log_audit, notify,
                       visible_lead_query)

bp = Blueprint("leads", __name__, url_prefix="/leads")

SORT_MAP = {
    "newest": ("created_at", desc),
    "oldest": ("created_at", lambda c: c.asc()),
    "name": ("name", lambda c: c.asc()),
    "status": ("status", lambda c: c.asc()),
    "value": ("expected_value", desc),
    "followup": ("next_followup_at", lambda c: c.asc()),
}


@bp.route("/")
@login_required
@active_required
def index():
    page = request.args.get("page", 1, type=int)
    search = (request.args.get("q", "") or "").strip()
    status = request.args.get("status", "")
    source = request.args.get("source", "")
    sort = request.args.get("sort", "newest")

    query = visible_lead_query()

    if search:
        like = f"%{search}%"
        query = query.filter(or_(
            Lead.name.ilike(like), Lead.email.ilike(like),
            Lead.company.ilike(like), Lead.phone.ilike(like)))
    if status in LeadStatus._value2member_map_:
        query = query.filter(Lead.status == status)
    if source:
        query = query.filter(Lead.source == source)

    column, direction = SORT_MAP.get(sort, SORT_MAP["newest"])
    query = query.order_by(direction(getattr(Lead, column)), Lead.id.desc())

    per_page = current_app.config["LEADS_PER_PAGE"]
    pagination = db.paginate(query, page=page, per_page=per_page, error_out=False)

    from app.models import lead_sources

    return render_template(
        "leads/list.html", title="Leads", pagination=pagination, leads=pagination.items,
        search=search, status=status, source=source, sort=sort,
        statuses=[(s.value, s.value) for s in LeadStatus],
        sources=[(s, s) for s in lead_sources],
        status_counts=_status_counts())


def _status_counts() -> dict:
    rows = (visible_lead_query().with_entities(Lead.status, func.count(Lead.id))
            .group_by(Lead.status).all())
    return {s: n for s, n in rows}


@bp.route("/new", methods=["GET", "POST"])
@login_required
@active_required
def create():
    form = LeadForm()
    # Employees always own the leads they create.
    if not current_user.is_admin:
        form.assigned_to.data = current_user.id
    if form.validate_on_submit():
        lead = _lead_from_form(Lead(), form)
        db.session.add(lead)
        if not _commit_or_fail():
            return render_template("leads/form.html", title="New lead",
                                   form=form, lead=None)
        log_audit("lead_created", entity="Lead", entity_id=lead.id, detail=lead.email)
        log_activity("lead_created", f"New lead created: {lead.name}",
                     entity_type="Lead", entity_id=lead.id, lead_id=lead.id)
        _notify_assignment(lead)
        flash("Lead created successfully.", "success")
        return redirect(url_for("leads.detail", lead_id=lead.id))
    return render_template("leads/form.html", title="New lead", form=form, lead=None)


@bp.route("/<int:lead_id>")
@login_required
@active_required
def detail(lead_id: int):
    lead = _get_visible_lead(lead_id)
    note_form = NoteForm()
    # ML scoring is optional - never let a missing model break this page.
    try:
        from app.predictions.service import predict_lead_conversion

        lead_prediction = predict_lead_conversion(lead)
    except Exception:  # pragma: no cover - defensive
        current_app.logger.exception("Lead scoring failed")
        lead_prediction = {"available": False,
                           "message": "The prediction model could not be loaded."}
    return render_template("leads/detail.html", title=lead.name, lead=lead,
                           note_form=note_form, lead_prediction=lead_prediction)


@bp.route("/<int:lead_id>/edit", methods=["GET", "POST"])
@login_required
@active_required
def edit(lead_id: int):
    lead = _get_visible_lead(lead_id)
    form = LeadForm(obj=lead, original_email=lead.email, original_id=lead.id)
    if form.validate_on_submit():
        previous_status = lead.status
        previous_assignee = lead.assigned_to
        _lead_from_form(lead, form)
        if lead.status == LeadStatus.CONVERTED.value and not lead.converted_at:
            lead.converted_at = utcnow()
        if lead.status in (LeadStatus.NEW.value, LeadStatus.CONTACTED.value,
                           LeadStatus.INTERESTED.value, LeadStatus.QUALIFIED.value):
            lead.converted_at = None
            lead.lost_reason = None
        if lead.status == LeadStatus.LOST.value and not lead.notes:
            pass  # lost_reason captured via notes field when provided
        if not _commit_or_fail():
            return render_template("leads/form.html", title=f"Edit {lead.name}",
                                   form=form, lead=lead)
        log_audit("lead_updated", entity="Lead", entity_id=lead.id,
                  detail=f"{previous_status} -> {lead.status}")
        log_activity("lead_updated", f"Lead updated: {lead.name}",
                     entity_type="Lead", entity_id=lead.id, lead_id=lead.id)
        if lead.status != previous_status:
            log_activity("lead_status", f"{lead.name}: {previous_status} -> {lead.status}",
                         entity_type="Lead", entity_id=lead.id, lead_id=lead.id)
            _notify_status_change(lead, previous_status)
        if lead.assigned_to != previous_assignee:
            _notify_assignment(lead)
        flash("Lead updated.", "success")
        return redirect(url_for("leads.detail", lead_id=lead.id))
    elif request.method == "POST":
        flash("Please correct the errors below.", "danger")
    return render_template("leads/form.html", title=f"Edit {lead.name}",
                           form=form, lead=lead)


@bp.route("/<int:lead_id>/delete", methods=["POST"])
@login_required
@active_required
def delete(lead_id: int):
    lead = _get_visible_lead(lead_id)
    if lead.customer_id:
        flash("This lead already has a linked customer record. "
              "Delete or unlink the customer first.", "warning")
        return redirect(url_for("leads.detail", lead_id=lead.id))
    name = lead.name
    db.session.delete(lead)
    if not _commit_or_fail():
        return redirect(url_for("leads.detail", lead_id=lead.id))
    log_audit("lead_deleted", entity="Lead", entity_id=lead_id, detail=name)
    flash(f"Lead “{name}” deleted.", "success")
    return redirect(url_for("leads.index"))


@bp.route("/<int:lead_id>/convert", methods=["POST"])
@login_required
@active_required
def convert(lead_id: int):
    """Convert a lead into a customer inside one transaction."""
    lead = _get_visible_lead(lead_id)
    if lead.status == LeadStatus.CONVERTED.value:
        flash("This lead is already converted.", "info")
        return redirect(url_for("leads.detail", lead_id=lead.id))
    if lead.status == LeadStatus.LOST.value:
        flash("A lost lead cannot be converted. Reopen it first.", "warning")
        return redirect(url_for("leads.detail", lead_id=lead.id))

    try:
        customer = Customer.query.filter(
            func.lower(Customer.email) == lead.email.lower()).first()
        if customer is None:
            customer = Customer(
                name=lead.name, email=lead.email, phone=lead.phone,
                company=lead.company, created_by=current_user.id)
            db.session.add(customer)
            db.session.flush()
        lead.customer_id = customer.id
        lead.status = LeadStatus.CONVERTED.value
        lead.converted_at = utcnow()
        lead.next_followup_at = None
        db.session.commit()
    except Exception as exc:  # pragma: no cover - defensive
        db.session.rollback()
        current_app.logger.exception("Lead conversion failed")
        flash(f"Conversion failed: {exc}", "danger")
        return redirect(url_for("leads.detail", lead_id=lead.id))

    log_audit("lead_converted", entity="Lead", entity_id=lead.id, detail=lead.email)
    log_activity("lead_converted", f"Lead converted to customer: {lead.name}",
                 entity_type="Lead", entity_id=lead.id, lead_id=lead.id)
    if lead.assigned_to:
        notify(lead.assigned_to, "Lead converted",
               f"{lead.name} is now a customer.", kind="converted",
               link=url_for("customers.detail", customer_id=lead.customer_id))
    flash(f"{lead.name} converted to a customer.", "success")
    return redirect(url_for("customers.detail", customer_id=lead.customer_id))


@bp.route("/<int:lead_id>/notes", methods=["POST"])
@login_required
@active_required
def add_note(lead_id: int):
    lead = _get_visible_lead(lead_id)
    form = NoteForm()
    if form.validate_on_submit():
        note = f"[{utcnow():%Y-%m-%d %H:%M}] {current_user.name}: {form.body.data.strip()}"
        lead.notes = f"{lead.notes}\n\n{note}".strip() if lead.notes else note
        lead.last_contact_at = utcnow()
        if _commit_or_fail():
            log_activity("lead_note", f"Note added to {lead.name}",
                         entity_type="Lead", entity_id=lead.id, lead_id=lead.id)
            flash("Note added.", "success")
    else:
        flash("Note cannot be empty.", "danger")
    return redirect(url_for("leads.detail", lead_id=lead.id))


@bp.route("/<int:lead_id>/status", methods=["POST"])
@login_required
@active_required
def quick_status(lead_id: int):
    lead = _get_visible_lead(lead_id)
    new_status = request.form.get("status", "")
    if new_status not in LeadStatus._value2member_map_:
        flash("Invalid status value.", "danger")
        return redirect(url_for("leads.detail", lead_id=lead.id))
    previous = lead.status
    lead.status = new_status
    if new_status == LeadStatus.CONVERTED.value and not lead.converted_at:
        lead.converted_at = utcnow()
    if new_status in (LeadStatus.CONTACTED.value, LeadStatus.INTERESTED.value,
                      LeadStatus.QUALIFIED.value):
        lead.last_contact_at = utcnow()
    if not _commit_or_fail():
        return redirect(url_for("leads.detail", lead_id=lead.id))
    log_audit("lead_status_changed", entity="Lead", entity_id=lead.id,
              detail=f"{previous} -> {new_status}")
    log_activity("lead_status", f"{lead.name}: {previous} -> {new_status}",
                 entity_type="Lead", entity_id=lead.id, lead_id=lead.id)
    _notify_status_change(lead, previous)
    flash(f"Status changed to {new_status}.", "success")
    return redirect(url_for("leads.detail", lead_id=lead.id))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_visible_lead(lead_id: int) -> Lead:
    lead = visible_lead_query().filter(Lead.id == lead_id).first_or_404()
    return lead


def _lead_from_form(lead: Lead, form: LeadForm) -> Lead:
    lead.name = form.name.data.strip()
    lead.email = form.email.data.strip().lower()
    lead.phone = (form.phone.data or "").strip() or None
    lead.company = (form.company.data or "").strip() or None
    lead.source = form.source.data
    lead.product_interest = (form.product_interest.data or "").strip() or None
    lead.expected_value = float(form.expected_value.data or 0)
    lead.status = form.status.data
    lead.notes = (form.notes.data or "").strip() or lead.notes
    lead.next_followup_at = form.next_followup_at.data
    lead.assigned_to = form.assigned_to.data or None
    return lead


def _commit_or_fail() -> bool:
    """Commit the current transaction; roll back and flash on failure."""
    try:
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Database commit failed")
        flash("A database error occurred. Nothing was saved.", "danger")
        return False


def _notify_assignment(lead: Lead) -> None:
    if not lead.assigned_to or lead.assigned_to == current_user.id:
        return
    db.session.add(Notification(
        user_id=lead.assigned_to,
        title=f"New lead assigned: {lead.name}",
        body=f"{lead.source} • expected {lead.expected_value or 0:,.2f}",
        kind="lead", link=url_for("leads.detail", lead_id=lead.id)))
    db.session.commit()


def _notify_status_change(lead: Lead, previous: str) -> None:
    if not lead.assigned_to or lead.assigned_to == current_user.id:
        return
    kind = "converted" if lead.status == LeadStatus.CONVERTED.value else "lead"
    db.session.add(Notification(
        user_id=lead.assigned_to,
        title=f"{lead.name}: {previous} → {lead.status}",
        body="Lead status was updated.", kind=kind,
        link=url_for("leads.detail", lead_id=lead.id)))
    db.session.commit()
