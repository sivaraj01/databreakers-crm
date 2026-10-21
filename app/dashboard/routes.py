"""Dashboard blueprint: live KPI cards + Chart.js series (all DB-backed)."""
from __future__ import annotations

from flask import Blueprint, render_template
from flask_login import current_user, login_required

from app.services import DashboardService
from app.utils import active_required

bp = Blueprint("dashboard", __name__)


@bp.route("/")
@login_required
@active_required
def index():
    service = DashboardService
    summary = service.summary()
    has_data = summary["total_leads"] or summary["total_customers"] or summary["total_revenue"]
    return render_template(
        "dashboard/index.html", title="Dashboard",
        summary=summary, has_data=bool(has_data),
        chart_leads=service.monthly_lead_generation(),
        chart_status=service.lead_status_distribution(),
        chart_revenue=service.monthly_revenue(),
        chart_growth=service.customer_growth(),
        chart_conversion=service.conversion_trend(),
        chart_products=service.product_performance(),
        activities=service.recent_activities(),
    )


@bp.route("/activity")
@login_required
@active_required
def activity():
    from flask import request

    from app.models import Activity
    from app.extensions import db

    page = request.args.get("page", 1, type=int)
    query = Activity.query
    if not current_user.is_admin:
        query = query.filter(db.or_(Activity.user_id == current_user.id,
                                    Activity.lead_id.is_(None)))
    pagination = db.paginate(query.order_by(Activity.created_at.desc()),
                             page=page, per_page=25, error_out=False)
    return render_template("dashboard/activity.html", title="Activity",
                           pagination=pagination, activities=pagination.items)
