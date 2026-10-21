"""Tests for dashboard metrics and analytics with real and empty data."""
from __future__ import annotations

from datetime import timedelta

from app.extensions import db
from app.models import (Customer, Lead, Product, Sale, utcnow)
from app.services import AnalyticsService, DashboardService


# ---------------------------------------------------------------------------
# Empty database
# ---------------------------------------------------------------------------
def test_dashboard_renders_on_empty_database(admin_client):
    resp = admin_client.get("/")
    assert resp.status_code == 200
    assert b"Dashboard" in resp.data


def test_dashboard_empty_state_shown(admin_client):
    resp = admin_client.get("/")
    assert b"Welcome to DataBreakers CRM" in resp.data


def test_summary_is_zero_when_empty(app):
    with app.app_context():
        s = DashboardService.summary()
        assert s["total_leads"] == 0
        assert s["total_customers"] == 0
        assert s["total_revenue"] == 0.0
        assert s["conversion_rate"] == 0.0
        assert s["followups_due_today"] == 0


def test_conversion_rate_zero_when_no_decided_leads(app):
    with app.app_context():
        assert DashboardService.summary()["conversion_rate"] == 0.0


def test_chart_series_empty_safe(app):
    with app.app_context():
        assert DashboardService.monthly_lead_generation()["values"] == [0] * 12
        assert DashboardService.monthly_revenue()["values"] == [0.0] * 12
        assert DashboardService.customer_growth()["values"] == [0] * 12
        assert DashboardService.product_performance() == {"labels": [], "values": []}


def test_status_distribution_handles_empty(app):
    with app.app_context():
        data = DashboardService.lead_status_distribution()
        assert data["labels"] == ["No leads"]
        assert data["values"] == [0]


# ---------------------------------------------------------------------------
# With data
# ---------------------------------------------------------------------------
def _seed(app, leads=4, converted=1, lost=1, customers=2, sales=2):
    with app.app_context():
        from app.models import User

        user = User(name="Seeder", email="seeder@example.com",
                    role="employee", is_active_flag=True)
        user.set_password("SeedPass123")
        db.session.add(user)
        db.session.flush()

        statuses = (["Converted"] * converted + ["Lost"] * lost +
                    ["New"] * (leads - converted - lost))
        for i, status in enumerate(statuses):
            db.session.add(Lead(name=f"L{i}", email=f"l{i}@example.com",
                                source="Website", status=status,
                                assigned_to=user.id))

        cust_ids = []
        for i in range(customers):
            c = Customer(name=f"C{i}", email=f"c{i}@example.com",
                         created_by=user.id)
            db.session.add(c)
            db.session.flush()
            cust_ids.append(c.id)

        product = Product(name="Widget", sku="W-1", category="HW", price=100.0)
        db.session.add(product)
        db.session.flush()

        for i in range(sales):
            db.session.add(Sale(customer_id=cust_ids[i % len(cust_ids)],
                                product_id=product.id, amount=100.0,
                                quantity=1, sold_at=utcnow(),
                                status="Completed", recorded_by=user.id))
        db.session.commit()


def test_summary_counts_real_data(admin_client, app):
    _seed(app, leads=4, converted=1, lost=1, customers=2, sales=3)
    with app.app_context():
        s = DashboardService.summary()
        assert s["total_leads"] == 4
        assert s["converted_leads"] == 1
        assert s["lost_leads"] == 1
        assert s["total_customers"] == 2
        assert s["total_revenue"] == 300.0
        # conversion = converted / (converted + lost) = 1/2 = 50%
        assert s["conversion_rate"] == 50.0
        assert s["avg_deal_value"] == 100.0


def test_dashboard_page_shows_computed_revenue(admin_client, app):
    _seed(app, sales=3)
    resp = admin_client.get("/")
    assert resp.status_code == 200
    assert b"300.00" in resp.data


def test_expected_value_is_not_revenue(app):
    """A big expected deal value must never count as revenue."""
    _seed(app, leads=1, converted=0, lost=0, customers=1, sales=0)
    with app.app_context():
        lead = Lead.query.first()
        lead.expected_value = 999999.0
        db.session.commit()
        s = DashboardService.summary()
        assert s["total_revenue"] == 0.0
        assert s["total_leads"] == 1


def test_pending_sales_do_not_count_as_revenue(app):
    _seed(app, customers=1, sales=0)
    with app.app_context():
        c = Customer.query.first()
        p = Product.query.first()
        db.session.add(Sale(customer_id=c.id, product_id=p.id, amount=5000.0,
                            quantity=1, sold_at=utcnow(), status="Pending"))
        db.session.commit()
        assert DashboardService.summary()["total_revenue"] == 0.0


def test_product_performance_ranks_by_revenue(admin_client, app):
    _seed(app, customers=2, sales=0)
    with app.app_context():
        c1, c2 = Customer.query.limit(2).all()
        p = Product.query.first()
        db.session.add(Sale(customer_id=c1.id, product_id=p.id, amount=100.0,
                            quantity=1, sold_at=utcnow(), status="Completed"))
        db.session.add(Sale(customer_id=c2.id, product_id=p.id, amount=100.0,
                            quantity=5, sold_at=utcnow(), status="Completed"))
        db.session.commit()

    with app.app_context():
        data = DashboardService.product_performance()
        assert data["labels"] == ["Widget"]
        assert data["values"] == [600.0]


# ---------------------------------------------------------------------------
# Analytics module
# ---------------------------------------------------------------------------
def test_analytics_page_renders_empty(admin_client):
    resp = admin_client.get("/analytics/")
    assert resp.status_code == 200
    assert b"Analytics" in resp.data


def test_analytics_empty_data_is_safe(admin_client, app):
    resp = admin_client.get("/analytics/")
    assert resp.status_code == 200
    with app.app_context():
        assert AnalyticsService.source_performance()["empty"] is True
        assert AnalyticsService.customer_segments()["empty"] is True
        assert AnalyticsService.engagement()["empty"] is True
        assert AnalyticsService.retention()["total_customers"] == 0
        assert AnalyticsService.retention()["retention_rate"] == 0.0


def test_analytics_documents_formulas(admin_client, app):
    with app.app_context():
        docs = AnalyticsService.METRIC_DOCS
        assert "conversion_rate" in docs
        assert "converted" in docs["conversion_rate"]
        assert "retention" in docs
        assert "revenue_by_product" in docs


def test_source_performance_computes_win_rate(admin_client, app):
    with app.app_context():
        from app.models import User

        u = User(name="S", email="s@example.com", role="employee",
                 is_active_flag=True)
        u.set_password("Spass1234")
        db.session.add(u)
        db.session.flush()
        for i, status in enumerate(["Converted", "Converted", "Lost", "New"]):
            db.session.add(Lead(name=f"S{i}", email=f"s{i}@example.org",
                                source="Referral", status=status,
                                assigned_to=u.id))
        db.session.commit()

    with app.app_context():
        data = AnalyticsService.source_performance()
        assert data["empty"] is False
        row = data["records"][0]
        assert row["Source"] == "Referral"
        assert row["Leads"] == 4
        assert row["Converted"] == 2
        assert row["Win rate %"] == 50.0


def test_retention_metrics(app):
    _seed(app, customers=2, sales=1)
    with app.app_context():
        r = AnalyticsService.retention()
        assert r["total_customers"] == 2
        assert r["active_customers"] == 1
        assert r["churned_customers"] == 1
        assert r["retention_rate"] == 50.0


def test_customer_segments_buckets_by_clv(app):
    with app.app_context():
        for i, (name, email) in enumerate(
                [("Rich Co", "rich@example.org"), ("Small Co", "small@example.org")]):
            c = Customer(name=name, email=email, created_by=None)
            db.session.add(c)
            db.session.flush()
            p = Product(name=f"P{i}", sku=f"SKU{i}", price=1.0)
            db.session.add(p)
            db.session.flush()
            amount = 30000.0 if i == 0 else 500.0
            db.session.add(Sale(customer_id=c.id, product_id=p.id,
                                amount=amount, quantity=1, sold_at=utcnow(),
                                status="Completed"))
        db.session.commit()

    with app.app_context():
        data = AnalyticsService.customer_segments()
        assert data["empty"] is False
        tiers = {r["Tier"]: r["Customers"] for r in data["records"]}
        assert tiers.get("Platinum") == 1
        assert tiers.get("Bronze") == 1


def test_revenue_by_employee(admin_client, app):
    _seed(app, customers=1, sales=2)
    with app.app_context():
        data = AnalyticsService.revenue_by_employee()
        assert data["labels"] == ["Seeder"]
        assert data["values"] == [200.0]
