"""Business & customer analytics blueprint (Pandas + SQL aggregations)."""
from __future__ import annotations

from flask import Blueprint, render_template
from flask_login import login_required

from app.services import AnalyticsService, DashboardService
from app.utils import active_required

bp = Blueprint("analytics", __name__, url_prefix="/analytics")


@bp.route("/")
@login_required
@active_required
def index():
    return render_template(
        "analytics/index.html", title="Analytics",
        overview=AnalyticsService.overview(),
        source=AnalyticsService.source_performance(),
        segments=AnalyticsService.customer_segments(),
        retention=AnalyticsService.retention(),
        trend=AnalyticsService.monthly_sales_trend(),
        by_product=AnalyticsService.revenue_by_product(),
        by_employee=AnalyticsService.revenue_by_employee(),
        engagement=AnalyticsService.engagement(),
        growth=DashboardService.customer_growth(12),
    )
