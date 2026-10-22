"""Authentication blueprint: login, logout, profile, change password,
and the self-service password reset (email one-time code)."""
from __future__ import annotations

import time
from datetime import datetime

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, session, url_for)
from flask_login import (current_user, login_required, login_user,
                         logout_user)

import app.email as mailer
from app.auth import reset as reset_service
from app.auth.forms import (ChangePasswordForm, ForgotPasswordForm, LoginForm,
                            ProfileForm, ResetPasswordForm, VerifyOtpForm)
from app.extensions import db
from app.models import User, utcnow
from app.utils import log_audit

bp = Blueprint("auth", __name__, url_prefix="/auth")

# Session key holding the in-progress reset. It carries the address the user
# typed (never a password or a code) and a timestamp proving they passed the
# code step recently enough to be trusted with a password change.
RESET_SESSION_KEY = "password_reset"

# One wording per outcome, deliberately. Anything more specific would tell a
# caller whether an address has an account attached to it.
GENERIC_CODE_SENT = ("If that address has an account, a 6-digit code is on its "
                     "way. Check your inbox.")
GENERIC_CODE_INVALID = ("That code is invalid or has expired. You can request "
                        "a new one.")


def _reset_state() -> dict:
    state = session.get(RESET_SESSION_KEY)
    return state if isinstance(state, dict) else {}


def _save_reset_state(state: dict) -> None:
    session[RESET_SESSION_KEY] = state


def _clear_reset_state() -> None:
    session.pop(RESET_SESSION_KEY, None)


def _session_wait(state: dict) -> int:
    """Seconds this browser must wait before asking for another code.

    Derived only from this session's own last request, so the message is
    identical whether or not the address has an account behind it.
    """
    cooldown = int(current_app.config.get("PASSWORD_RESET_COOLDOWN_SECONDS", 60))
    requested = state.get("requested_at")
    if not isinstance(requested, (int, float)):
        return 0
    return max(0, int(cooldown - (time.time() - requested)))


def _lookup_user(email: str) -> User | None:
    return User.query.filter(db.func.lower(User.email) == email).first()


def _dispatch_code(email: str, state: dict) -> dict:
    """Issue and email a code for ``email``, without revealing whether it exists.

    Delivery problems are recorded in the audit log rather than shown to the
    caller: a message that appears only for addresses with an account would
    turn this form into an account-enumeration oracle. The one exception is
    SMTP being unconfigured, which is a property of the deployment and not of
    the address, so the caller reports that one honestly.
    """
    state = dict(state)
    state["email"] = email
    state["requested_at"] = time.time()
    state.pop("verified_at", None)

    user = _lookup_user(email)
    if user is None or not user.is_active:
        return state

    code, _wait = reset_service.issue_otp(user)
    if code is None:
        # The database cooldown suppressed a send. It exists to stop an inbox
        # being flooded from a fresh session; the visible message comes from
        # this browser's own wait, which already expired a moment ago.
        log_audit("password_reset_throttled", user_id=user.id,
                  detail=reset_service.mask_email(user.email))
        return state

    ttl = int(current_app.config.get("PASSWORD_RESET_OTP_TTL_MINUTES", 10))
    if mailer.send_password_reset_otp(user.email, code, ttl):
        log_audit("password_reset_code_sent", user_id=user.id,
                  detail=reset_service.mask_email(user.email))
    else:
        current_app.logger.warning(
            "password reset code could not be delivered to %s",
            reset_service.mask_email(user.email))
        log_audit("password_reset_mail_failed", user_id=user.id,
                  detail=reset_service.mask_email(user.email))
    return state


def _verified_reset_user() -> User | None:
    """The account whose code was verified, while that proof is still fresh."""
    state = _reset_state()
    verified = state.get("verified_at")
    if not isinstance(verified, (int, float)):
        return None
    window = int(current_app.config.get("PASSWORD_RESET_WINDOW_MINUTES", 15)) * 60
    if time.time() - verified > window:
        _clear_reset_state()
        return None
    user_id = state.get("user_id")
    user = db.session.get(User, user_id) if isinstance(user_id, int) else None
    if user is None or not user.is_active:
        _clear_reset_state()
        return None
    return user


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = LoginForm()
    if form.validate_on_submit():
        email = (form.email.data or "").strip().lower()
        user = User.query.filter(db.func.lower(User.email) == email).first()

        if user is None or not user.check_password(form.password.data):
            log_audit("login_failed", detail=f"email={email}")
            flash("Invalid email or password.", "danger")
        elif not user.is_active:
            log_audit("login_blocked_inactive", user_id=user.id, detail=email)
            flash("This account has been deactivated. Contact an administrator.", "warning")
        else:
            user.last_login_at = utcnow()
            db.session.commit()
            login_user(user, remember=form.remember.data,
                       duration=None)
            log_audit("login", user_id=user.id, detail=email)
            next_url = request.args.get("next")
            # Only allow same-origin relative redirects (open-redirect guard).
            if next_url and next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(url_for("dashboard.index"))

    return render_template("auth/login.html", form=form, title="Sign in")


@bp.route("/logout", methods=["POST", "GET"])
def logout():
    if current_user.is_authenticated:
        log_audit("logout")
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    form = ProfileForm(original_email=current_user.email,
                       obj=current_user)
    if form.validate_on_submit():
        current_user.name = form.name.data.strip()
        current_user.email = form.email.data.strip().lower()
        db.session.commit()
        log_audit("profile_updated", entity="User", entity_id=current_user.id)
        flash("Profile updated.", "success")
        return redirect(url_for("auth.profile"))

    return render_template("auth/profile.html", form=form, title="My profile")


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            flash("Your current password is incorrect.", "danger")
        else:
            current_user.set_password(form.new_password.data)
            db.session.commit()
            log_audit("password_changed", entity="User", entity_id=current_user.id)
            flash("Password updated. Use it the next time you sign in.", "success")
            return redirect(url_for("auth.profile"))

    return render_template("auth/change_password.html", form=form, title="Change password")


# ---------------------------------------------------------------------------
# Password reset - step 1: ask for a code
# ---------------------------------------------------------------------------
@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        flash("You are already signed in. Use Change password instead.", "info")
        return redirect(url_for("auth.change_password"))

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        # A property of the deployment, not of the address, so reporting it
        # reveals nothing about who has an account.
        if not mailer.smtp_configured():
            flash("Password reset by email is not configured on this deployment. "
                  "Ask an administrator to set it up.", "warning")
            return render_template("auth/forgot_password.html", form=form,
                                   title="Reset password")

        state = _reset_state()
        wait = _session_wait(state)
        if wait:
            flash(f"Please wait about {wait} seconds before asking for another code.",
                  "warning")
            return render_template("auth/forgot_password.html", form=form,
                                   title="Reset password")

        email = (form.email.data or "").strip().lower()
        _save_reset_state(_dispatch_code(email, state))
        flash(GENERIC_CODE_SENT, "info")
        return redirect(url_for("auth.verify_otp"))

    return render_template("auth/forgot_password.html", form=form,
                           title="Reset password")


# ---------------------------------------------------------------------------
# Password reset - step 2: prove control of the inbox
# ---------------------------------------------------------------------------
@bp.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    state = _reset_state()
    email = state.get("email")
    if not email:
        flash("Request a verification code first.", "warning")
        return redirect(url_for("auth.forgot_password"))

    form = VerifyOtpForm()
    if form.validate_on_submit():
        user = _lookup_user(email)
        if user is None or not user.is_active:
            result = reset_service.NO_CODE
        else:
            result = reset_service.verify_otp(user, (form.code.data or "").strip())

        if result == reset_service.OK:
            state = dict(state)
            state["user_id"] = user.id
            state["verified_at"] = time.time()
            _save_reset_state(state)
            flash("Code verified. Choose a new password.", "success")
            return redirect(url_for("auth.reset_password"))

        # Wrong, expired and exhausted all answer the same way on purpose.
        log_audit("password_reset_code_failed", user_id=user.id if user else None,
                  detail=reset_service.mask_email(email))
        flash(GENERIC_CODE_INVALID, "danger")

    return render_template("auth/verify_otp.html", form=form, title="Enter code",
                           masked_email=reset_service.mask_email(email))


@bp.route("/reset-code", methods=["POST"])
def resend_reset_code():
    """Issue another code from the verify screen (throttled like step 1)."""
    state = _reset_state()
    email = state.get("email")
    if not email:
        flash("Request a verification code first.", "warning")
        return redirect(url_for("auth.forgot_password"))

    wait = _session_wait(state)
    if wait:
        flash(f"Please wait about {wait} seconds before asking for another code.",
              "warning")
        return redirect(url_for("auth.verify_otp"))

    _save_reset_state(_dispatch_code(email, state))
    flash(GENERIC_CODE_SENT, "info")
    return redirect(url_for("auth.verify_otp"))


# ---------------------------------------------------------------------------
# Password reset - step 3: set the new password
# ---------------------------------------------------------------------------
@bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    user = _verified_reset_user()
    if user is None:
        flash("That reset session has expired. Request a new code.", "warning")
        return redirect(url_for("auth.forgot_password"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.new_password.data)
        db.session.commit()
        # Drop the session proof immediately: one successful password change
        # ends the flow, so the same verification cannot be replayed for a
        # second change while its window is open.
        _clear_reset_state()
        log_audit("password_reset_completed", entity="User", entity_id=user.id,
                  user_id=user.id, detail=reset_service.mask_email(user.email))
        flash("Password updated. Sign in with your new password.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", form=form,
                           title="Set new password")
