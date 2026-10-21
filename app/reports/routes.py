"""Reports blueprint: filtered views + CSV export (permission-scoped)."""
from __future__ import annotations

import io
from datetime import date

import pandas as pd
from flask import (Blueprint, Response, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from app.services import ReportService
from app.utils import (active_required, log_audit, parse_date,
                       visible_customer_query, visible_followup_query,
                       visible_lead_query, visible_sale_query)

bp = Blueprint("reports", __name__, url_prefix="/reports")

REPORT_TYPES = {
    "leads": ("Leads", ReportService.leads),
    "customers": ("Customers", ReportService.customers),
    "sales": ("Sales", ReportService.sales),
    "followups": ("Follow-ups", ReportService.followups),
    "conversion": ("Conversion performance", ReportService.conversion),
}


@bp.route("/")
@login_required
@active_required
def index():
    kind = request.args.get("type", "leads")
    if kind not in REPORT_TYPES:
        kind = "leads"
    title, builder = REPORT_TYPES[kind]

    start = parse_date(request.args.get("start"))
    end = parse_date(request.args.get("end"))
    if start and end and start > end:
        flash("The start date must be before the end date.", "warning")
        start, end = None, None

    df = builder(start, end)
    records = df.to_dict(orient="records") if not df.empty else []
    columns = list(df.columns) if not df.empty else []
    has_filters = bool(start or end)

    return render_template(
        "reports/index.html", title="Reports", kind=kind, report_title=title,
        types=[(k, t[0]) for k, t in REPORT_TYPES.items()],
        columns=columns, records=records[:500], total_rows=len(records),
        truncated=len(records) > 500, start=start, end=end,
        has_filters=has_filters,
        monthly=ReportService.monthly_analytics(12))


@bp.route("/monthly")
@login_required
@active_required
def monthly():
    """Monthly business analytics (HTML table + chart)."""
    months = request.args.get("months", 12, type=int)
    months = max(1, min(months, 24))
    df = ReportService.monthly_analytics(months)
    records = df.to_dict(orient="records")
    return render_template("reports/monthly.html", title="Monthly analytics",
                           records=records, months=months,
                           chart=df.to_dict(orient="list"))


@bp.route("/export.csv")
@login_required
@active_required
def export_csv():
    kind = request.args.get("type", "leads")
    if kind not in REPORT_TYPES:
        flash("Unknown report type.", "danger")
        return redirect(url_for("reports.index"))
    title, builder = REPORT_TYPES[kind]
    start = parse_date(request.args.get("start"))
    end = parse_date(request.args.get("end"))
    if start and end and start > end:
        start, end = None, None

    df = builder(start, end)
    log_audit("report_exported", entity="Report", detail=f"{kind} csv rows={len(df)}")
    if df.empty:
        flash(f"No rows to export for the {title} report with those filters.", "info")
        return redirect(url_for("reports.index", type=kind,
                                start=request.args.get("start", ""),
                                end=request.args.get("end", "")))
    filename = f"databreakers_{kind}_{date.today():%Y%m%d}.csv"
    return _csv_response(df, filename)


@bp.route("/export.xlsx")
@login_required
@active_required
def export_excel():
    """Excel export - only available when openpyxl is installed."""
    kind = request.args.get("type", "leads")
    if kind not in REPORT_TYPES:
        flash("Unknown report type.", "danger")
        return redirect(url_for("reports.index"))
    title, builder = REPORT_TYPES[kind]
    start = parse_date(request.args.get("start"))
    end = parse_date(request.args.get("end"))
    df = builder(start, end)
    if df.empty:
        flash("No rows to export.", "info")
        return redirect(url_for("reports.index", type=kind))

    try:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name=title[:31])
        data = buf.getvalue()
    except ImportError:
        flash("Excel export requires the 'openpyxl' package. Install it with "
              "<code>pip install openpyxl</code>, or use the CSV export.", "warning")
        return redirect(url_for("reports.index", type=kind,
                                start=request.args.get("start", ""),
                                end=request.args.get("end", "")))
    log_audit("report_exported", entity="Report", detail=f"{kind} xlsx rows={len(df)}")
    resp = Response(data, mimetype="application/vnd.openxmlformats-officedocument"
                                  ".spreadsheetml.sheet")
    resp.headers["Content-Disposition"] = \
        f"attachment; filename=databreakers_{kind}_{date.today():%Y%m%d}.xlsx"
    return resp


def _csv_response(df: pd.DataFrame, filename: str) -> Response:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    resp = Response("\ufeff" + buf.getvalue(), mimetype="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = f"attachment; filename={filename}"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
