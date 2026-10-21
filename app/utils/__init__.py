"""Reusable helpers: authorization decorators, audit, notifications, utilities."""
from __future__ import annotations

import functools
from datetime import date, datetime, timedelta

from flask import abort, flash, request
from flask_login import current_user

from app.extensions import db
from app.models import Activity, AuditLog, Notification

# ---------------------------------------------------------------------------
# Authorization decorators
# ---------------------------------------------------------------------------
def admin_required(view):
    """Deny access to every user that is not an administrator (server side)."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return app_login_required()
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def app_login_required():
    from flask_login import login_required

    return login_required(lambda: abort(403))


def active_required(view):
    """Block deactivated users even if their session is still alive."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if current_user.is_authenticated and not current_user.is_active:
            from flask_login import logout_user

            logout_user()
            flash("Your account has been deactivated. Contact an administrator.", "danger")
            from flask import redirect, url_for

            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Data scoping
# ---------------------------------------------------------------------------
def _scope_user():
    """Return the user whose data should be scoped, or None.

    * Inside a request context: the logged-in user (AnonymousUser if not
      logged in). Callers must still enforce ``login_required``.
    * Outside a request context (CLI scripts, seeding, ML training, tests):
      returns ``None``, meaning "system context, no scoping applied".

    We never infer elevated privileges from an anonymous web session.
    """
    from flask import has_request_context

    if not has_request_context():
        return None
    return current_user


def _is_admin_context() -> bool:
    """True when the caller may see every record."""
    user = _scope_user()
    if user is None:
        return True  # trusted system context (no HTTP request involved)
    return bool(getattr(user, "is_admin", False))


def visible_lead_query():
    """Leads visible to the current user (admins see everything)."""
    from app.models import Lead

    query = Lead.query
    user = _scope_user()
    if user is not None and not getattr(user, "is_admin", False):
        query = query.filter(Lead.assigned_to == user.id)
    return query


def visible_sale_query():
    from app.models import Sale

    query = Sale.query
    user = _scope_user()
    if user is not None and not getattr(user, "is_admin", False):
        # An employee sees sales they recorded OR sales for customers they own.
        from app.models import Customer

        owned = db.session.query(Customer.id).filter(
            Customer.created_by == user.id
        ).subquery()
        query = query.filter(
            db.or_(Sale.recorded_by == user.id,
                   Sale.customer_id.in_(db.select(owned.c.id)))
        )
    return query


def visible_followup_query():
    from app.models import FollowUp

    query = FollowUp.query
    user = _scope_user()
    if user is not None and not getattr(user, "is_admin", False):
        query = query.filter(FollowUp.assigned_to == user.id)
    return query


def visible_customer_query():
    from app.models import Customer

    query = Customer.query
    user = _scope_user()
    if user is not None and not getattr(user, "is_admin", False):
        # An employee sees customers they created or leads assigned to them.
        from app.models import Lead

        assigned_lead_customers = db.select(Lead.customer_id).where(
            Lead.assigned_to == user.id)
        query = query.filter(db.or_(
            Customer.created_by == user.id,
            Customer.id.in_(assigned_lead_customers),
        ))
    return query


# ---------------------------------------------------------------------------
# Audit + activity + notifications
# ---------------------------------------------------------------------------
def log_audit(action: str, entity: str | None = None, entity_id: int | None = None,
              detail: str | None = None, user_id: int | None = None) -> None:
    """Record a security-relevant event. Never raises into the request."""
    from flask import has_request_context

    try:
        user = _scope_user()
        actor = "system"
        ip_address = None
        if has_request_context():
            actor = user.email if getattr(user, "is_authenticated", False) else "anonymous"
            ip_address = (request.remote_addr or "")[:60]
        elif user is not None:
            actor = getattr(user, "email", "system")
        entry = AuditLog(
            user_id=user_id or getattr(user, "id", None),
            actor=actor,
            action=action,
            entity=entity,
            entity_id=entity_id,
            detail=(detail or "")[:500],
            ip_address=ip_address,
        )
        db.session.add(entry)
        db.session.commit()
    except Exception:  # pragma: no cover - logging must never break a request
        db.session.rollback()


def log_activity(action: str, summary: str, entity_type: str = "system",
                 entity_id: int | None = None, lead_id: int | None = None,
                 user_id: int | None = None) -> None:
    try:
        user = _scope_user()
        db.session.add(Activity(
            user_id=user_id or getattr(user, "id", None),
            lead_id=lead_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            summary=summary[:400],
        ))
        db.session.commit()
    except Exception:  # pragma: no cover
        db.session.rollback()


def notify(user_id: int, title: str, body: str = "", kind: str = "info",
           link: str | None = None) -> None:
    """Create an in-app notification. Email/WhatsApp are NOT implemented."""
    if not user_id:
        return
    try:
        db.session.add(Notification(
            user_id=user_id, title=title[:160], body=(body or "")[:500],
            kind=kind, link=link[:255] if link else None,
        ))
        db.session.commit()
    except Exception:  # pragma: no cover
        db.session.rollback()


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------
def month_key(dt: datetime | date) -> str:
    return dt.strftime("%Y-%m")


def last_n_months(n: int, anchor: date | None = None) -> list[str]:
    """Return ISO month keys for the last ``n`` months, oldest first."""
    anchor = anchor or date.today()
    out, year, month = [], anchor.year, anchor.month
    for _ in range(n):
        out.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return list(reversed(out))


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def start_of_month(d: date) -> date:
    return d.replace(day=1)


def month_range(start: date, end: date) -> list[str]:
    out, cur = [], start
    while cur <= end:
        out.append(cur.strftime("%Y-%m"))
        cur = (cur + timedelta(days=32)).replace(day=1)
    return out
