"""Forms for the authentication blueprint."""
from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import (DataRequired, Email, EqualTo, Length,
                                ValidationError)

from app.models import User


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=1)])
    remember = BooleanField("Remember me")
    submit = SubmitField("Sign in")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Current password",
                                     validators=[DataRequired(), Length(min=1)])
    new_password = PasswordField(
        "New password",
        validators=[DataRequired(), Length(min=8, message="Use at least 8 characters.")])
    confirm_password = PasswordField(
        "Confirm new password",
        validators=[DataRequired(), EqualTo("new_password", message="Passwords do not match.")])
    submit = SubmitField("Update password")

    def validate_new_password(self, field):  # noqa: N802
        pw = field.data or ""
        checks = {
            "at least 8 characters": len(pw) >= 8,
            "a lowercase letter": any(c.islower() for c in pw),
            "an uppercase letter": any(c.isupper() for c in pw),
            "a digit": any(c.isdigit() for c in pw),
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            raise ValidationError("New password needs " + ", ".join(failed) + ".")


class ProfileForm(FlaskForm):
    name = StringField("Full name", validators=[DataRequired(), Length(min=2, max=120)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    submit = SubmitField("Save profile")

    def __init__(self, original_email: str | None = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_email = original_email

    def validate_email(self, field):  # noqa: N802
        if self.original_email and field.data.lower() == self.original_email.lower():
            return
        if User.query.filter(db_func_lower(User.email) == field.data.lower()).first():
            raise ValidationError("That email is already registered.")


def db_func_lower(column):
    from sqlalchemy import func

    return func.lower(column)


class RegistrationForm(FlaskForm):
    """Used only by administrators to create employee accounts."""

    name = StringField("Full name", validators=[DataRequired(), Length(min=2, max=120)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8)])
    submit = SubmitField("Create account")

    def validate_email(self, field):  # noqa: N802
        if User.query.filter(db_func_lower(User.email) == field.data.lower()).first():
            raise ValidationError("That email is already registered.")
