"""Forms for the authentication blueprint."""
from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import (BooleanField, PasswordField, StringField, SubmitField,
                     ValidationError)
from wtforms.validators import (DataRequired, Email, EqualTo, Length, Regexp)

from app.models import User


def _password_problems(pw: str) -> list[str]:
    """Composition rules every password-changing form must agree on."""
    checks = {
        "at least 8 characters": len(pw) >= 8,
        "a lowercase letter": any(c.islower() for c in pw),
        "an uppercase letter": any(c.isupper() for c in pw),
        "a digit": any(c.isdigit() for c in pw),
    }
    return [name for name, ok in checks.items() if not ok]


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
        failed = _password_problems(field.data or "")
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


# ---------------------------------------------------------------------------
# Password reset (email OTP)
# ---------------------------------------------------------------------------
class ForgotPasswordForm(FlaskForm):
    """Step 1 - which inbox should receive the code.

    The route answers identically whether or not the address exists, so this
    form deliberately performs no lookup of its own: an unknown address must
    not be detectable from the response.
    """

    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    submit = SubmitField("Send code")


class VerifyOtpForm(FlaskForm):
    """Step 2 - prove the code reached that inbox."""

    code = StringField(
        "Verification code",
        validators=[
            DataRequired(message="Enter the 6-digit code."),
            Regexp(r"^\d{6}$", message="The code is exactly 6 digits."),
        ])
    submit = SubmitField("Verify code")


class ResetPasswordForm(FlaskForm):
    """Step 3 - the new credentials. Same composition rules as a password
    change while signed in, so a reset cannot produce a weaker password."""

    new_password = PasswordField(
        "New password",
        validators=[DataRequired(), Length(min=8, message="Use at least 8 characters.")])
    confirm_password = PasswordField(
        "Confirm new password",
        validators=[DataRequired(), EqualTo("new_password", message="Passwords do not match.")])
    submit = SubmitField("Set password")

    def validate_new_password(self, field):  # noqa: N802
        failed = _password_problems(field.data or "")
        if failed:
            raise ValidationError("New password needs " + ", ".join(failed) + ".")
