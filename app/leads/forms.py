"""Lead forms with server-side validation."""
from __future__ import annotations

from datetime import datetime

from flask_login import current_user
from flask_wtf import FlaskForm
from sqlalchemy import func
from wtforms import (DateTimeField, DecimalField, SelectField, StringField,
                     SubmitField, TextAreaField)
from wtforms.validators import (DataRequired, Email, Length, NumberRange,
                                Optional, ValidationError)

from app.extensions import db
from app.models import Lead, LeadStatus, Product, User, lead_sources


class LeadForm(FlaskForm):
    name = StringField("Lead name", validators=[DataRequired(), Length(min=2, max=160)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    phone = StringField("Phone", validators=[Optional(), Length(max=40)])
    company = StringField("Company", validators=[Optional(), Length(max=160)])
    source = SelectField("Lead source", choices=[(s, s) for s in lead_sources],
                         validators=[DataRequired()])
    product_interest = SelectField("Product interest", choices=[], validators=[Optional()])
    expected_value = DecimalField(
        "Expected deal value", default=0.0,
        validators=[Optional(),
                    NumberRange(min=0, max=10_000_000,
                                message="Value must be between 0 and 10,000,000.")])
    status = SelectField("Status",
                         choices=[(s, s) for s in LeadStatus._value2member_map_],
                         validators=[DataRequired()])
    assigned_to = SelectField("Assign to", coerce=int, validators=[Optional()])
    notes = TextAreaField("Lead notes", validators=[Optional(), Length(max=5000)])
    next_followup_at = DateTimeField("Next follow-up (YYYY-MM-DD HH:MM)",
                                     format="%Y-%m-%d %H:%M", validators=[Optional()])
    submit = SubmitField("Save lead")

    def __init__(self, *args, **kwargs):
        self.original_email: str | None = kwargs.pop("original_email", None)
        self.original_id: int | None = kwargs.pop("original_id", None)
        super().__init__(*args, **kwargs)

        products = Product.query.filter_by(is_active=True).order_by(Product.name).all()
        self.product_interest.choices = [("", "-- none --")] + \
            [(p.name, p.name) for p in products]

        if current_user.is_admin:
            users = User.query.filter_by(is_active_flag=True).order_by(User.name).all()
            # 0 (not "") is the "Unassigned" sentinel: the field coerces to int,
            # and WTForms cannot coerce an empty string.
            self.assigned_to.choices = [(0, "Unassigned")] + \
                [(u.id, f"{u.name} ({u.role_label})") for u in users]
        else:
            self.assigned_to.choices = [(current_user.id, current_user.name)]

    def validate_email(self, field):  # noqa: N802
        """Prevent duplicate active leads with the same email."""
        email = (field.data or "").strip().lower()
        if self.original_email and email == self.original_email.lower():
            return
        clash = Lead.query.filter(func.lower(Lead.email) == email).first()
        if clash:
            raise ValidationError(
                f"A lead with this email already exists (#{clash.id} - {clash.name}). "
                "Edit that record instead of creating a duplicate.")

    def validate_next_followup_at(self, field):  # noqa: N802
        if field.data and field.data < datetime.now():
            raise ValidationError("Next follow-up date cannot be in the past.")

    def validate_assigned_to(self, field):  # noqa: N802
        if field.data:
            user = db.session.get(User, field.data)
            if user is None or not user.is_active:
                raise ValidationError("Selected user is not available.")
            if not current_user.is_admin and user.id != current_user.id:
                raise ValidationError("You can only assign leads to yourself.")


class NoteForm(FlaskForm):
    body = TextAreaField("Note", validators=[DataRequired(), Length(min=2, max=5000)])
    submit = SubmitField("Add note")
