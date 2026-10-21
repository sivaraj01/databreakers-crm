"""Business logic for DataBreakers CRM.

Route handlers stay thin; every calculation lives here so it can be unit
tested and reused by the dashboard, analytics and Zia AI assistant.
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta

import pandas as pd
from flask_login import current_user

from app.extensions import db
from app.models import (
    Customer, CustomerInteraction, FollowUp, FollowUpStatus, Lead, LeadStatus,
    Product, Sale, SaleStatus, User,
)
from app.utils import (last_n_months, visible_customer_query,
                       visible_followup_query, visible_lead_query,
                       visible_sale_query)

TODAY = date.today()


# ===========================================================================
# Dashboard service
# ===========================================================================
class DashboardService:
    """Live metrics computed from the database (nothing is hard-coded)."""

    @staticmethod
    def summary() -> dict:
        leads = Lead.query
        today = datetime.combine(TODAY, datetime.min.time())
        tomorrow = today + timedelta(days=1)

        total_leads = leads.count()
        total_customers = Customer.query.count()
        new_leads = leads.filter(Lead.status == LeadStatus.NEW.value).count()
        converted = leads.filter(Lead.status == LeadStatus.CONVERTED.value).count()
        lost = leads.filter(Lead.status == LeadStatus.LOST.value).count()
        followups_due_today = (FollowUp.query
                               .filter(FollowUp.status == FollowUpStatus.PENDING.value,
                                       FollowUp.due_at >= today,
                                       FollowUp.due_at < tomorrow)
                               .count())
        overdue_followups = (FollowUp.query
                             .filter(FollowUp.status == FollowUpStatus.PENDING.value,
                                     FollowUp.due_at < today)
                             .count())
        closed = converted + lost
        conversion_rate = round((converted / closed) * 100, 1) if closed else 0.0
        acquisition_rate = round((converted / total_leads) * 100, 1) if total_leads else 0.0

        revenue = db.session.query(db.func.coalesce(db.func.sum(Sale.amount * Sale.quantity), 0.0)) \
            .filter(Sale.status == SaleStatus.COMPLETED.value).scalar() or 0.0
        completed_sales = Sale.query.filter_by(status=SaleStatus.COMPLETED.value).count()

        return {
            "total_leads": total_leads,
            "total_customers": total_customers,
            "new_leads": new_leads,
            "converted_leads": converted,
            "lost_leads": lost,
            "followups_due_today": followups_due_today,
            "overdue_followups": overdue_followups,
            "conversion_rate": conversion_rate,
            "acquisition_rate": acquisition_rate,
            "total_revenue": round(float(revenue), 2),
            "avg_deal_value": round(float(revenue) / completed_sales, 2) if completed_sales else 0.0,
            "open_leads": leads.filter(Lead.status.in_(Lead.OPEN_STATUSES)).count(),
        }

    # -- chart series -------------------------------------------------------
    @staticmethod
    def monthly_lead_generation(months: int = 12) -> dict:
        keys = last_n_months(months)
        counts = defaultdict(int)
        for ym, n in (db.session.query(
                db.func.strftime("%Y-%m", Lead.created_at), db.func.count(Lead.id))
                .group_by(db.func.strftime("%Y-%m", Lead.created_at)).all()):
            if ym:
                counts[ym] = n
        return {"labels": [_human_month(k) for k in keys],
                "values": [counts[k] for k in keys]}

    @staticmethod
    def lead_status_distribution() -> dict:
        rows = (db.session.query(Lead.status, db.func.count(Lead.id))
                .group_by(Lead.status).all())
        data = dict(rows)
        labels = [s for s in LeadStatus._value2member_map_ if data.get(s)]
        # keep canonical order, append any unexpected status at the end
        labels += [s for s in data if s not in LeadStatus._value2member_map_]
        return {"labels": labels or ["No leads"],
                "values": [data.get(s, 0) for s in labels] or [0]}

    @staticmethod
    def monthly_revenue(months: int = 12) -> dict:
        keys = last_n_months(months)
        totals = defaultdict(float)
        rows = (db.session.query(db.func.strftime("%Y-%m", Sale.sold_at),
                                 db.func.sum(Sale.amount * Sale.quantity))
                .filter(Sale.status == SaleStatus.COMPLETED.value)
                .group_by(db.func.strftime("%Y-%m", Sale.sold_at)).all())
        for ym, total in rows:
            if ym:
                totals[ym] = float(total or 0.0)
        return {"labels": [_human_month(k) for k in keys],
                "values": [round(totals[k], 2) for k in keys]}

    @staticmethod
    def customer_growth(months: int = 12) -> dict:
        keys = last_n_months(months)
        counts = defaultdict(int)
        rows = (db.session.query(db.func.strftime("%Y-%m", Customer.created_at),
                                 db.func.count(Customer.id))
                .group_by(db.func.strftime("%Y-%m", Customer.created_at)).all())
        for ym, n in rows:
            if ym:
                counts[ym] = n
        running, series = 0, []
        for k in keys:
            running += counts[k]
            series.append(running)
        return {"labels": [_human_month(k) for k in keys], "values": series}

    @staticmethod
    def conversion_trend(months: int = 6) -> dict:
        """% of leads created each month that were eventually converted."""
        keys = last_n_months(months)
        created, converted = defaultdict(int), defaultdict(int)
        for ym, n in (db.session.query(db.func.strftime("%Y-%m", Lead.created_at),
                                       db.func.count(Lead.id))
                      .group_by(db.func.strftime("%Y-%m", Lead.created_at)).all()):
            if ym:
                created[ym] = n
        for ym, n in (db.session.query(db.func.strftime("%Y-%m", Lead.created_at),
                                       db.func.count(Lead.id))
                      .filter(Lead.status == LeadStatus.CONVERTED.value)
                      .group_by(db.func.strftime("%Y-%m", Lead.created_at)).all()):
            if ym:
                converted[ym] = n
        values = [round(converted[k] / created[k] * 100, 1) if created[k] else 0.0
                  for k in keys]
        return {"labels": [_human_month(k) for k in keys], "values": values}

    @staticmethod
    def product_performance(limit: int = 8) -> dict:
        rows = (db.session.query(Product.name,
                                 db.func.sum(Sale.amount * Sale.quantity))
                .join(Sale, Sale.product_id == Product.id)
                .filter(Sale.status == SaleStatus.COMPLETED.value)
                .group_by(Product.name)
                .order_by(db.func.sum(Sale.amount * Sale.quantity).desc())
                .limit(limit).all())
        if not rows:
            return {"labels": [], "values": []}
        return {"labels": [r[0] for r in rows],
                "values": [round(float(r[1] or 0.0), 2) for r in rows]}

    @staticmethod
    def recent_activities(limit: int = 8) -> list:
        from app.models import Activity
        from app.utils import _scope_user

        query = Activity.query
        user = _scope_user()
        if user is not None and not getattr(user, "is_admin", False):
            query = query.filter(db.or_(Activity.user_id == user.id,
                                        Activity.lead_id.is_(None)))
        return query.order_by(Activity.created_at.desc()).limit(limit).all()


def _human_month(ym: str) -> str:
    try:
        year, month = int(ym[:4]), int(ym[5:7])
        return calendar.month_abbr[month] + " " + str(year % 100).zfill(2)
    except Exception:
        return ym


# ===========================================================================
# Follow-up service
# ===========================================================================
class FollowUpService:
    @staticmethod
    def buckets() -> dict:
        today_start = datetime.combine(TODAY, datetime.min.time())
        tomorrow_start = today_start + timedelta(days=1)
        q = FollowUp.query.filter_by(status=FollowUpStatus.PENDING.value)
        return {
            "overdue": q.filter(FollowUp.due_at < today_start)
                        .order_by(FollowUp.due_at).all(),
            "today": q.filter(FollowUp.due_at >= today_start,
                              FollowUp.due_at < tomorrow_start)
                      .order_by(FollowUp.due_at).all(),
            "upcoming": q.filter(FollowUp.due_at >= tomorrow_start,
                                 FollowUp.due_at < tomorrow_start + timedelta(days=14))
                         .order_by(FollowUp.due_at).all(),
            "completed": (FollowUp.query
                          .filter_by(status=FollowUpStatus.COMPLETED.value)
                          .order_by(FollowUp.completed_at.desc()).limit(10).all()),
        }


# ===========================================================================
# Analytics service (Pandas + SQL aggregations)
# ===========================================================================
class AnalyticsService:
    """Every metric documents the formula it uses."""

    METRIC_DOCS = {
        "conversion_rate": "converted leads / (converted + lost) * 100",
        "lead_source_performance": "converted leads from a source / leads from that source * 100",
        "acquisition_trend": "count of customers created per month (cumulative)",
        "retention": "customers with a completed purchase in the last 365 days / total customers * 100",
        "engagement": "interactions + follow-ups per customer in the period",
        "revenue_by_product": "SUM(amount * quantity) of completed sales grouped by product",
        "revenue_by_employee": "SUM(amount * quantity) of completed sales grouped by recorder",
        "avg_deal_value": "SUM(revenue) / COUNT(completed sales)",
        "clv_segment": "segment customers by lifetime value: Platinum >= 25,000, Gold >= 10,000, Silver >= 2,000, Bronze otherwise",
    }

    @classmethod
    def overview(cls) -> dict:
        total_leads = Lead.query.count()
        converted = Lead.query.filter_by(status=LeadStatus.CONVERTED.value).count()
        lost = Lead.query.filter_by(status=LeadStatus.LOST.value).count()
        closed = converted + lost
        return {
            "total_leads": total_leads,
            "converted": converted,
            "lost": lost,
            "open": total_leads - closed,
            "conversion_rate": round(converted / closed * 100, 1) if closed else 0.0,
            "acquisition_rate": round(converted / total_leads * 100, 1) if total_leads else 0.0,
            "formulas": cls.METRIC_DOCS,
        }

    @classmethod
    def source_performance(cls) -> dict:
        """SQL aggregation + safe pandas framing (empty-safe)."""
        rows = (db.session.query(Lead.source, Lead.status, db.func.count(Lead.id))
                .group_by(Lead.source, Lead.status).all())
        if not rows:
            return {"headers": ["Source", "Leads", "Converted", "Win rate %"],
                    "records": [], "empty": True}
        pivot = defaultdict(lambda: defaultdict(int))
        for source, status, n in rows:
            pivot[source][status] += n
        records = []
        for source, statuses in sorted(pivot.items()):
            total = sum(statuses.values())
            conv = statuses.get(LeadStatus.CONVERTED.value, 0)
            records.append({
                "Source": source,
                "Leads": total,
                "Converted": conv,
                "Win rate %": round(conv / total * 100, 1) if total else 0.0,
            })
        records.sort(key=lambda r: (-r["Win rate %"], -r["Leads"]))
        return {"headers": list(records[0].keys()), "records": records, "empty": False}

    @classmethod
    def customer_segments(cls) -> dict:
        customers = Customer.query.all()
        if not customers:
            return {"headers": ["Segment", "Customers", "Total CLV", "Avg CLV"],
                    "records": [], "empty": True}
        rows = [{"Customer": c.name, "CLV": c.lifetime_value,
                 "Purchases": c.total_purchases, "Segment": c.segment}
                for c in customers]
        df = pd.DataFrame(rows)
        df["Tier"] = pd.cut(
            df["CLV"], bins=[-1, 2000, 10000, 25000, float("inf")],
            labels=["Bronze", "Silver", "Gold", "Platinum"])
        grouped = (df.groupby("Tier", observed=False)
                   .agg(Customers=("Customer", "count"),
                        CLV=("CLV", "sum"))
                   .reset_index())
        records = [{
            "Tier": str(r["Tier"]), "Customers": int(r["Customers"]),
            "Total CLV": round(float(r["CLV"]), 2),
            "Avg CLV": round(float(r["CLV"]) / int(r["Customers"]), 2) if r["Customers"] else 0.0,
        } for _, r in grouped.iterrows() if int(r["Customers"]) > 0]
        return {"headers": list(records[0].keys()) if records else
                ["Tier", "Customers", "Total CLV", "Avg CLV"],
                "records": records, "empty": not records}

    @classmethod
    def retention(cls) -> dict:
        from datetime import timedelta as td

        total = Customer.query.count()
        cut = datetime.now() - td(days=365)
        active = (Sale.query
                  .filter(Sale.status == SaleStatus.COMPLETED.value,
                          Sale.sold_at >= cut)
                  .distinct(Sale.customer_id).count() if total else 0)
        churned = total - active
        return {
            "total_customers": total,
            "active_customers": active,
            "churned_customers": churned,
            "retention_rate": round(active / total * 100, 1) if total else 0.0,
            "formula": "active customers = customers with >=1 completed sale in last 365 days; retention = active / total * 100",
        }

    @classmethod
    def monthly_sales_trend(cls, months: int = 12) -> dict:
        return DashboardService.monthly_revenue(months)

    @classmethod
    def revenue_by_product(cls) -> dict:
        return DashboardService.product_performance(limit=20)

    @classmethod
    def revenue_by_employee(cls) -> dict:
        rows = (db.session.query(User.name,
                                 db.func.sum(Sale.amount * Sale.quantity))
                .join(Sale, Sale.recorded_by == User.id)
                .filter(Sale.status == SaleStatus.COMPLETED.value)
                .group_by(User.name)
                .order_by(db.func.sum(Sale.amount * Sale.quantity).desc()).all())
        return {"labels": [r[0] for r in rows],
                "values": [round(float(r[1] or 0.0), 2) for r in rows]}

    @classmethod
    def engagement(cls, months: int = 6) -> dict:
        cut = datetime.now() - timedelta(days=30 * months)
        customers = Customer.query.all()
        if not customers:
            return {"headers": ["Customer", "Interactions", "Follow-ups", "Purchases", "Engagement"],
                    "records": [], "empty": True}
        records = []
        for c in customers:
            inter = (CustomerInteraction.query
                     .filter(CustomerInteraction.customer_id == c.id,
                             CustomerInteraction.created_at >= cut).count())
            fups = (FollowUp.query
                    .filter(FollowUp.customer_id == c.id,
                            FollowUp.created_at >= cut).count())
            sales = (Sale.query
                     .filter(Sale.customer_id == c.id,
                             Sale.status == SaleStatus.COMPLETED.value,
                             Sale.sold_at >= cut).count())
            score = inter + fups + sales * 2
            level = "High" if score >= 6 else "Medium" if score >= 3 else "Low"
            records.append({"Customer": c.name, "Interactions": inter,
                            "Follow-ups": fups, "Purchases": sales,
                            "Engagement": level})
        records.sort(key=lambda r: -(r["Interactions"] + r["Follow-ups"] + r["Purchases"]))
        return {"headers": list(records[0].keys()), "records": records, "empty": False}


# ===========================================================================
# Reporting service
# ===========================================================================
class ReportService:
    """Builds tabular datasets (pandas) used by the reports module.

    All builders go through the ``visible_*_query()`` helpers so an export can
    never contain another user's records (admins and system/CLI contexts see
    everything).
    """

    @classmethod
    def leads(cls, start: date | None, end: date | None) -> pd.DataFrame:
        q = visible_lead_query()
        if start:
            q = q.filter(Lead.created_at >= datetime.combine(start, datetime.min.time()))
        if end:
            q = q.filter(Lead.created_at < datetime.combine(end + timedelta(days=1),
                                                            datetime.min.time()))
        rows = [{
            "ID": l.id, "Name": l.name, "Email": l.email, "Phone": l.phone or "",
            "Company": l.company or "", "Source": l.source,
            "Status": l.status, "Expected Value": l.expected_value or 0.0,
            "Assigned To": l.assignee.name if l.assignee else "Unassigned",
            "Created At": l.created_at.strftime("%Y-%m-%d %H:%M") if l.created_at else "",
            "Next Follow-up": l.next_followup_at.strftime("%Y-%m-%d %H:%M") if l.next_followup_at else "",
        } for l in q.order_by(Lead.created_at.desc()).all()]
        return pd.DataFrame(rows)

    @classmethod
    def customers(cls, start: date | None, end: date | None) -> pd.DataFrame:
        q = visible_customer_query()
        if start:
            q = q.filter(Customer.created_at >= datetime.combine(start, datetime.min.time()))
        if end:
            q = q.filter(Customer.created_at < datetime.combine(end + timedelta(days=1),
                                                                datetime.min.time()))
        rows = [{
            "ID": c.id, "Name": c.name, "Email": c.email, "Phone": c.phone or "",
            "Company": c.company or "", "Segment": c.segment,
            "City": c.city or "", "Country": c.country or "",
            "Purchases": c.total_purchases, "Lifetime Value": round(c.lifetime_value, 2),
            "Created At": c.created_at.strftime("%Y-%m-%d %H:%M") if c.created_at else "",
        } for c in q.order_by(Customer.created_at.desc()).all()]
        return pd.DataFrame(rows)

    @classmethod
    def sales(cls, start: date | None, end: date | None) -> pd.DataFrame:
        q = visible_sale_query()
        if start:
            q = q.filter(Sale.sold_at >= datetime.combine(start, datetime.min.time()))
        if end:
            q = q.filter(Sale.sold_at < datetime.combine(end + timedelta(days=1),
                                                         datetime.min.time()))
        rows = [{
            "ID": s.id, "Reference": s.reference or "",
            "Customer": s.customer.name if s.customer else "",
            "Product": s.product.name if s.product else "",
            "Quantity": s.quantity, "Unit Amount": s.amount, "Total": s.total,
            "Counted Revenue": s.revenue,
            "Status": s.status,
            "Sold At": s.sold_at.strftime("%Y-%m-%d %H:%M") if s.sold_at else "",
            "Recorded By": s.recorder.name if s.recorder else "",
        } for s in q.order_by(Sale.sold_at.desc()).all()]
        return pd.DataFrame(rows)

    @classmethod
    def followups(cls, start: date | None, end: date | None) -> pd.DataFrame:
        q = visible_followup_query()
        if start:
            q = q.filter(FollowUp.due_at >= datetime.combine(start, datetime.min.time()))
        if end:
            q = q.filter(FollowUp.due_at < datetime.combine(end + timedelta(days=1),
                                                            datetime.min.time()))
        rows = [{
            "ID": f.id, "Subject": f.subject, "Type": f.kind, "Status": f.status,
            "Due At": f.due_at.strftime("%Y-%m-%d %H:%M") if f.due_at else "",
            "Assigned To": f.assignee.name if f.assignee else "",
            "Outcome": f.outcome or "",
        } for f in q.order_by(FollowUp.due_at.desc()).all()]
        return pd.DataFrame(rows)

    @classmethod
    def conversion(cls, start: date | None, end: date | None) -> pd.DataFrame:
        df = cls.leads(start, end)
        if df.empty:
            return pd.DataFrame(columns=["Status", "Leads", "Share %"])
        grouped = (df.groupby("Status").size().reset_index(name="Leads"))
        total = int(grouped["Leads"].sum())
        grouped["Share %"] = (grouped["Leads"] / total * 100).round(1) if total else 0.0
        return grouped.sort_values("Leads", ascending=False)

    @classmethod
    def monthly_analytics(cls, months: int = 12) -> pd.DataFrame:
        revenue = DashboardService.monthly_revenue(months)
        leadgen = DashboardService.monthly_lead_generation(months)
        conv = DashboardService.conversion_trend(months)
        return pd.DataFrame({
            "Month": revenue["labels"],
            "Revenue": revenue["values"],
            "New Leads": leadgen["values"],
            "Conversion %": conv["values"],
        })
