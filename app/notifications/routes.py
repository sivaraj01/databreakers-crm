"""In-app notifications blueprint (bell widget + full list).

Email and WhatsApp delivery are NOT implemented - only in-app notifications
are provided, and the UI says so explicitly.
"""
from __future__ import annotations

from flask import (Blueprint, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Notification
from app.utils import active_required, log_audit

bp = Blueprint("notifications", __name__, url_prefix="/notifications")


@bp.route("/")
@login_required
@active_required
def index():
    page = request.args.get("page", 1, type=int)
    only = request.args.get("filter", "all")
    query = Notification.query.filter_by(user_id=current_user.id)
    if only == "unread":
        query = query.filter_by(is_read=False)
    pagination = db.paginate(query.order_by(Notification.created_at.desc()),
                             page=page, per_page=20, error_out=False)
    unread = Notification.query.filter_by(user_id=current_user.id,
                                          is_read=False).count()
    return render_template("notifications/index.html", title="Notifications",
                           pagination=pagination, notifications=pagination.items,
                           unread=unread, filter=only)


@bp.route("/<int:notification_id>/read", methods=["POST"])
@login_required
@active_required
def mark_read(notification_id: int):
    note = Notification.query.filter_by(id=notification_id,
                                        user_id=current_user.id).first_or_404()
    note.is_read = True
    db.session.commit()
    if note.link:
        return redirect(note.link)
    return redirect(url_for("notifications.index"))


@bp.route("/read-all", methods=["POST"])
@login_required
@active_required
def mark_all_read():
    updated = (Notification.query
               .filter_by(user_id=current_user.id, is_read=False)
               .update({"is_read": True}, synchronize_session=False))
    db.session.commit()
    log_audit("notifications_read", detail=f"{updated} marked read")
    flash(f"{updated} notification(s) marked as read.", "success")
    return redirect(url_for("notifications.index"))


@bp.route("/clear", methods=["POST"])
@login_required
@active_required
def clear():
    deleted = (Notification.query.filter_by(user_id=current_user.id)
               .delete(synchronize_session=False))
    db.session.commit()
    log_audit("notifications_cleared", detail=f"{deleted} deleted")
    flash(f"{deleted} notification(s) cleared.", "info")
    return redirect(url_for("notifications.index"))
