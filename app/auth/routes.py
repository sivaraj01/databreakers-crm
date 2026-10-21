"""Authentication blueprint: login, logout, profile, change password."""
from __future__ import annotations

from datetime import datetime

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, url_for)
from flask_login import (current_user, login_required, login_user,
                         logout_user)

from app.auth.forms import ChangePasswordForm, LoginForm, ProfileForm
from app.extensions import db
from app.models import User, utcnow
from app.utils import log_audit

bp = Blueprint("auth", __name__, url_prefix="/auth")


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
