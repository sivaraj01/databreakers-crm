"""Admin blueprint: user management, audit logs, business metrics, settings."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from wtforms import (BooleanField, PasswordField, SelectField, StringField,
                     SubmitField)
from wtforms.validators import (DataRequired, Email, EqualTo, Length,
                                Optional, ValidationError)

from app.auth.forms import _password_problems
from app.extensions import db
from app.models import (Activity, AuditLog, Customer, FollowUp, Lead,
                        Notification, Product, Sale, SaleStatus, User,
                        UserRole, utcnow)
from app.services import DashboardService
from app.utils import admin_required, log_audit, notify

bp = Blueprint("admin", __name__, url_prefix="/admin")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class UserForm(FlaskForm):
    name = StringField("Full name", validators=[DataRequired(), Length(min=2, max=120)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    role = SelectField("Role", choices=[(r.value, r.value.capitalize())
                                        for r in UserRole])
    password = PasswordField("Password",
                             validators=[Optional(), Length(min=8,
                                                            message="Use at least 8 characters.")])
    is_active_flag = BooleanField("Active", default=True)
    submit = SubmitField("Save user")

    def __init__(self, *args, **kwargs):
        self.original_email = kwargs.pop("original_email", None)
        self.require_password = kwargs.pop("require_password", True)
        super().__init__(*args, **kwargs)
        if not self.require_password:
            self.password.validators = [Optional()]

    def validate_email(self, field):  # noqa: N802
        email = (field.data or "").strip().lower()
        if self.original_email and email == self.original_email.lower():
            return
        if User.query.filter(db.func.lower(User.email) == email).first():
            raise ValidationError("That email is already registered.")

    def validate_password(self, field):  # noqa: N802
        """Hold administrator-set passwords to the same bar as self-service ones.

        Without this an admin could assign a password the owner would never
        have been allowed to pick themselves, leaving the account weaker than
        the rules the app advertises. An empty value is legitimate on the edit
        form and means "keep the current password".
        """
        password = field.data or ""
        if not password:
            return
        failed = _password_problems(password)
        if failed:
            raise ValidationError("Password needs " + ", ".join(failed) + ".")

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators=extra_validators)
        if not ok:
            return False
        if self.require_password and not (self.password.data or "").strip():
            self.password.errors = ("A password is required for a new account.",)
            return False
        return True


class SettingsForm(FlaskForm):
    site_name = StringField("Site name", default="DataBreakers CRM",
                            validators=[DataRequired(), Length(max=80)])
    items_per_page = StringField("Leads per page", default="12",
                                 validators=[DataRequired()])
    enable_zia_llm = BooleanField("Let Zia AI use the configured LLM provider")
    submit = SubmitField("Save settings")

    def validate_items_per_page(self, field):  # noqa: N802
        try:
            value = int(field.data)
        except (TypeError, ValueError):
            raise ValidationError("Must be a whole number.")
        if not 5 <= value <= 100:
            raise ValidationError("Choose a value between 5 and 100.")


# In-memory + DB backed app settings (audit-logged, per-install).
SETTING_KEYS = ("site_name", "leads_per_page", "zia_llm_enabled")


def get_settings() -> dict:
    defaults = {"site_name": current_app.config.get("APP_NAME", "DataBreakers CRM"),
                "leads_per_page": str(current_app.config.get("LEADS_PER_PAGE", 12)),
                "zia_llm_enabled": "1"}
    stored = {}
    for key in SETTING_KEYS:
        row = db.session.execute(
            db.text("SELECT value FROM app_settings WHERE key = :k"), {"k": key}
        ).first() if _settings_table_exists() else None
        stored[key] = row[0] if row else defaults.get(key, "")
    stored["zia_llm_enabled"] = stored.get("zia_llm_enabled", "1") == "1"
    return stored


def _settings_table_exists() -> bool:
    return bool(db.inspect(db.engine).has_table("app_settings"))


def save_settings(data: dict) -> None:
    if not _settings_table_exists():
        return
    for key, value in data.items():
        if key not in SETTING_KEYS:
            continue
        db.session.execute(
            db.text("INSERT INTO app_settings (key, value) VALUES (:k, :v) "
                    "ON CONFLICT(key) DO UPDATE SET value = :v"),
            {"k": key, "v": str(value)})
    db.session.commit()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@bp.route("/")
@login_required
@admin_required
def index():
    settings = get_settings()
    metrics = DashboardService.summary()
    return render_template("admin/index.html", title="Admin panel",
                           metrics=metrics, settings=settings,
                           user_count=User.query.count(),
                           active_users=User.query.filter_by(is_active_flag=True).count(),
                           pending_followups=FollowUp.query.filter_by(
                               status="Pending").count())


@bp.route("/users")
@login_required
@admin_required
def users():
    q = (request.args.get("q", "") or "").strip()
    query = User.query
    if q:
        query = query.filter(db.or_(User.name.ilike(f"%{q}%"),
                                    User.email.ilike(f"%{q}%")))
    users_list = query.order_by(User.name).all()
    return render_template("admin/users.html", title="User management",
                           users=users_list, search=q)


@bp.route("/users/new", methods=["GET", "POST"])
@login_required
@admin_required
def user_new():
    form = UserForm()
    if form.validate_on_submit():
        user = User(
            name=form.name.data.strip(),
            email=form.email.data.strip().lower(),
            role=form.role.data,
            is_active_flag=bool(form.is_active_flag.data))
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        log_audit("user_created", entity="User", entity_id=user.id,
                  detail=f"{user.email} role={user.role}")
        flash(f"User {user.name} created.", "success")
        return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", title="New user", form=form)


@bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def user_edit(user_id: int):
    user = db.session.get(User, user_id) or abort(404)
    form = UserForm(obj=user, original_email=user.email, require_password=False)
    if form.validate_on_submit():
        user.name = form.name.data.strip()
        user.email = form.email.data.strip().lower()
        if (form.role.data == UserRole.EMPLOYEE.value and user.is_admin
                and _last_admin(user)):
            flash("Cannot demote the last administrator.", "warning")
            return redirect(url_for("admin.user_edit", user_id=user.id))
        user.role = form.role.data
        user.is_active_flag = bool(form.is_active_flag.data)
        password_changed = bool(form.password.data)
        if password_changed:
            user.set_password(form.password.data)
        db.session.commit()
        log_audit("user_updated", entity="User", entity_id=user.id,
                  detail=f"role={user.role} active={user.is_active_flag}")
        if password_changed:
            # A distinct action, so "who reset this account's password" is one
            # filter away in the audit log rather than buried in name/role edits.
            log_audit("user_password_reset", entity="User", entity_id=user.id,
                      detail=user.email)
        flash("User updated.", "success")
        return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", title=f"Edit {user.name}",
                           form=form, user=user)


@bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@login_required
@admin_required
def user_toggle(user_id: int):
    user = db.session.get(User, user_id) or abort(404)
    if user.id == current_user.id:
        flash("You cannot deactivate your own account.", "warning")
        return redirect(url_for("admin.users"))
    if user.is_active_flag and _last_admin(user):
        flash("Cannot deactivate the last administrator.", "warning")
        return redirect(url_for("admin.users"))
    user.is_active_flag = not user.is_active_flag
    db.session.commit()
    action = "user_activated" if user.is_active_flag else "user_deactivated"
    log_audit(action, entity="User", entity_id=user.id, detail=user.email)
    flash(f"{user.name} {'activated' if user.is_active_flag else 'deactivated'}.",
          "success")
    return redirect(url_for("admin.users"))


@bp.route("/audit")
@login_required
@admin_required
def audit():
    page = request.args.get("page", 1, type=int)
    action = request.args.get("action", "")
    query = AuditLog.query
    if action:
        query = query.filter(AuditLog.action == action)
    pagination = db.paginate(query.order_by(AuditLog.created_at.desc()),
                             page=page, per_page=30, error_out=False)
    actions = [a for (a,) in db.session.query(AuditLog.action).distinct().all()]
    return render_template("admin/audit.html", title="Audit logs",
                           pagination=pagination, logs=pagination.items,
                           action=action, actions=actions)


@bp.route("/activity")
@login_required
@admin_required
def activity():
    page = request.args.get("page", 1, type=int)
    pagination = db.paginate(Activity.query.order_by(Activity.created_at.desc()),
                             page=page, per_page=30, error_out=False)
    return render_template("admin/activity.html", title="Activity monitor",
                           pagination=pagination, activities=pagination.items)


@bp.route("/settings", methods=["GET", "POST"])
@login_required
@admin_required
def settings():
    current = get_settings()
    form = SettingsForm(site_name=current["site_name"],
                        items_per_page=current["leads_per_page"],
                        enable_zia_llm=current["zia_llm_enabled"])
    if form.validate_on_submit():
        save_settings({
            "site_name": form.site_name.data.strip(),
            "leads_per_page": str(int(form.items_per_page.data)),
            "zia_llm_enabled": "1" if form.enable_zia_llm.data else "0",
        })
        current_app.config["APP_NAME"] = form.site_name.data.strip()
        current_app.config["LEADS_PER_PAGE"] = int(form.items_per_page.data)
        log_audit("settings_updated", entity="Settings",
                  detail=f"site={form.site_name.data.strip()}")
        flash("Settings saved.", "success")
        return redirect(url_for("admin.settings"))
    return render_template("admin/settings.html", title="Settings", form=form,
                           settings=current,
                           llm_configured=bool(current_app.config.get("ZIA_LLM_API_KEY")))


@bp.route("/notifications/broadcast", methods=["POST"])
@login_required
@admin_required
def broadcast():
    title = (request.form.get("title") or "").strip()
    body = (request.form.get("body") or "").strip()
    if not title:
        flash("A title is required.", "danger")
        return redirect(url_for("admin.index"))
    targets = User.query.filter_by(is_active_flag=True).all()
    for user in targets:
        notify(user.id, title, body, kind="info",
               link=url_for("notifications.index"))
    log_audit("notification_broadcast", entity="Notification",
              detail=f"{title[:80]} -> {len(targets)} users")
    flash(f"Notification sent to {len(targets)} user(s).", "success")
    return redirect(url_for("admin.index"))


def _last_admin(user: User) -> bool:
    return (user.is_admin and
            User.query.filter(User.role == UserRole.ADMIN.value,
                              User.is_active_flag.is_(True),
                              User.id != user.id).count() == 0)
