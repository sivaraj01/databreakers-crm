"""Follow-up management blueprint: schedule, assign, complete, reminders."""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import (Blueprint, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from wtforms import (DateTimeField, SelectField, StringField, SubmitField,
                     TextAreaField)
from wtforms.validators import (DataRequired, Length, Optional, ValidationError)

from app.extensions import db
from app.models import (Activity, Customer, FollowUp, FollowUpStatus, Lead,
                        Notification, User, utcnow)
from app.services import FollowUpService
from app.utils import (active_required, log_activity, log_audit, notify,
                       visible_followup_query)

bp = Blueprint("followups", __name__, url_prefix="/followups")


class FollowUpForm(FlaskForm):
    subject_type = SelectField("Related to",
                               choices=[("lead", "Lead"), ("customer", "Customer"),
                                        ("none", "General")])
    lead_id = SelectField("Lead", coerce=int, validators=[Optional()])
    customer_id = SelectField("Customer", coerce=int, validators=[Optional()])
    assigned_to = SelectField("Assign to", coerce=int, validators=[DataRequired()])
    due_at = DateTimeField("Due date & time (YYYY-MM-DD HH:MM)",
                           format="%Y-%m-%d %H:%M", validators=[DataRequired()])
    kind = SelectField("Type",
                       choices=[(k, k) for k in
                                ("Call", "Meeting", "Email", "Demo", "Site Visit")])
    notes = TextAreaField("Call / meeting notes", validators=[Optional(), Length(max=5000)])
    submit = SubmitField("Schedule follow-up")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        users = User.query.filter_by(is_active_flag=True).order_by(User.name).all()
        if current_user.is_admin:
            self.assigned_to.choices = [(u.id, f"{u.name} ({u.role_label})") for u in users]
        else:
            self.assigned_to.choices = [(current_user.id, current_user.name)]
        self.lead_id.choices = [(0, "-- select lead --")] + [
            (l.id, f"{l.name} ({l.status})") for l in
            Lead.query.filter(Lead.status.in_(Lead.OPEN_STATUSES))
            .order_by(Lead.name).limit(200)]
        self.customer_id.choices = [(0, "-- select customer --")] + [
            (c.id, f"{c.name}") for c in
            Customer.query.order_by(Customer.name).limit(200)]

    def validate_due_at(self, field):  # noqa: N802
        if field.data and field.data < datetime.now() - timedelta(minutes=5):
            raise ValidationError("Due time cannot be in the past.")

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators=extra_validators)
        if not ok:
            return False
        if self.subject_type.data == "lead" and not self.lead_id.data:
            self.lead_id.errors = ("Select the lead this follow-up belongs to.",)
            return False
        if self.subject_type.data == "customer" and not self.customer_id.data:
            self.customer_id.errors = ("Select the customer this follow-up belongs to.",)
            return False
        return True


class OutcomeForm(FlaskForm):
    outcome = TextAreaField("Outcome", validators=[DataRequired(), Length(min=3, max=5000)])
    submit = SubmitField("Mark completed")


@bp.route("/")
@login_required
@active_required
def index():
    scope = request.args.get("scope", "today")
    buckets = FollowUpService.buckets()
    return render_template("followups/list.html", title="Follow-ups",
                           buckets=buckets, scope=scope,
                           form=FollowUpForm(), outcome_form=OutcomeForm())


@bp.route("/new", methods=["GET", "POST"])
@login_required
@active_required
def create():
    form = FollowUpForm()
    if form.validate_on_submit():
        followup = _build_followup(form)
        db.session.add(followup)
        db.session.commit()
        log_audit("followup_created", entity="FollowUp", entity_id=followup.id,
                  detail=f"{followup.kind} @ {followup.due_at}")
        log_activity("followup_created",
                     f"Follow-up scheduled for {followup.subject}",
                     entity_type="FollowUp", entity_id=followup.id,
                     lead_id=followup.lead_id)
        if followup.assigned_to != current_user.id:
            notify(followup.assigned_to, "Follow-up assigned",
                   f"{followup.kind} with {followup.subject} due "
                   f"{followup.due_at:%Y-%m-%d %H:%M}", kind="followup",
                   link=url_for("followups.index"))
        flash("Follow-up scheduled.", "success")
        return redirect(url_for("followups.index"))
    return render_template("followups/form.html", title="Schedule follow-up",
                           form=form)


@bp.route("/<int:followup_id>/complete", methods=["POST"])
@login_required
@active_required
def complete(followup_id: int):
    followup = visible_followup_query().filter(
        FollowUp.id == followup_id).first_or_404()
    if followup.status != FollowUpStatus.PENDING.value:
        flash("This follow-up is already closed.", "info")
        return redirect(url_for("followups.index"))
    form = OutcomeForm()
    if form.validate_on_submit():
        followup.outcome = form.outcome.data.strip()
        followup.status = FollowUpStatus.COMPLETED.value
        followup.completed_at = utcnow()
        db.session.commit()
        log_audit("followup_completed", entity="FollowUp", entity_id=followup.id)
        log_activity("followup_completed",
                     f"Follow-up completed: {followup.subject}",
                     entity_type="FollowUp", entity_id=followup.id,
                     lead_id=followup.lead_id)
        if followup.lead:
            followup.lead.last_contact_at = utcnow()
            db.session.commit()
        flash("Follow-up marked as completed.", "success")
    else:
        flash("Describe the outcome before completing the follow-up.", "danger")
    return redirect(url_for("followups.index"))


@bp.route("/<int:followup_id>/cancel", methods=["POST"])
@login_required
@active_required
def cancel(followup_id: int):
    followup = visible_followup_query().filter(
        FollowUp.id == followup_id).first_or_404()
    followup.status = FollowUpStatus.CANCELLED.value
    followup.completed_at = utcnow()
    db.session.commit()
    log_audit("followup_cancelled", entity="FollowUp", entity_id=followup.id)
    flash("Follow-up cancelled.", "info")
    return redirect(url_for("followups.index"))


@bp.route("/<int:followup_id>/reschedule", methods=["POST"])
@login_required
@active_required
def reschedule(followup_id: int):
    followup = visible_followup_query().filter(
        FollowUp.id == followup_id).first_or_404()
    raw = request.form.get("due_at", "")
    try:
        new_due = datetime.strptime(raw, "%Y-%m-%dT%H:%M")
    except (TypeError, ValueError):
        try:
            new_due = datetime.strptime(raw, "%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            flash("Invalid date format.", "danger")
            return redirect(url_for("followups.index"))
    followup.due_at = new_due
    db.session.commit()
    log_audit("followup_rescheduled", entity="FollowUp", entity_id=followup.id,
              detail=f"new due {new_due}")
    flash("Follow-up rescheduled.", "success")
    return redirect(url_for("followups.index"))


def _build_followup(form: FollowUpForm) -> FollowUp:
    """Build a FollowUp from validated form data (validation already ran)."""
    subject_type = form.subject_type.data
    lead_id = form.lead_id.data if subject_type == "lead" else None
    customer_id = form.customer_id.data if subject_type == "customer" else None
    return FollowUp(
        lead_id=lead_id or None,
        customer_id=customer_id or None,
        assigned_to=form.assigned_to.data,
        created_by=current_user.id,
        due_at=form.due_at.data,
        kind=form.kind.data,
        notes=(form.notes.data or "").strip() or None,
    )


@bp.route("/reminders")
@login_required
@active_required
def reminders():
    """JSON endpoint consumed by the header bell widget."""
    buckets = FollowUpService.buckets()
    items = []
    for label, rows in buckets.items():
        for f in rows[:5]:
            items.append({
                "id": f.id, "title": f"{f.kind} · {f.subject}",
                "due_at": f.due_at.strftime("%Y-%m-%d %H:%M"),
                "state": f.due_state,
                "link": url_for("followups.index"),
            })
    return {"items": items, "total": sum(len(v) for k, v in buckets.items()
                                         if k in ("overdue", "today"))}
