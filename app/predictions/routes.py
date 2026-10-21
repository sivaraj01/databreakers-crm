"""Predictions blueprint: lead conversion probability + sales forecast."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.predictions import service
from app.utils import active_required, visible_lead_query
from app.models import Lead

bp = Blueprint("predictions", __name__, url_prefix="/predictions")


@bp.route("/")
@login_required
@active_required
def index():
    lead_status = service.lead_model_status()
    sales_status = service.sales_model_status()
    forecast = service.forecast_sales(6)
    return render_template(
        "predictions/index.html", title="Predictions",
        lead_status=lead_status, sales_status=sales_status, forecast=forecast)


@bp.route("/lead/<int:lead_id>")
@login_required
@active_required
def lead_score(lead_id: int):
    """Score a single lead (used from the lead detail page)."""
    lead = visible_lead_query().filter(Lead.id == lead_id).first_or_404()
    result = service.predict_lead_conversion(lead)
    if request.args.get("redirect"):
        if result.get("available"):
            flash(f"{lead.name}: {result['percent']}% conversion probability "
                  f"({result['band']} confidence band).", "info")
        else:
            flash(result.get("message", "Model not available."), "warning")
        return redirect(url_for("leads.detail", lead_id=lead.id))
    return result


@bp.route("/batch")
@login_required
@active_required
def batch_scores():
    """Rank the visible leads by predicted conversion probability."""
    status = service.lead_model_status()
    if not status.available:
        return {"available": False, "message": status.message, "items": []}
    leads = visible_lead_query().limit(100).all()
    scored = []
    for lead in leads:
        result = service.predict_lead_conversion(lead)
        if result.get("available"):
            scored.append({"lead_id": lead.id, "name": lead.name,
                           "status": lead.status, "percent": result["percent"],
                           "band": result["band"]})
    scored.sort(key=lambda r: -r["percent"])
    return {"available": True, "items": scored,
            "metrics": status.metrics, "limitations": status.limitations}


@bp.route("/forecast")
@login_required
@active_required
def forecast():
    months = request.args.get("months", 6, type=int)
    months = max(1, min(months, 24))
    return service.forecast_sales(months)
