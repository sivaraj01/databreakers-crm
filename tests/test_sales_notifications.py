"""Tests for sales recording, revenue rules and notifications."""
from __future__ import annotations

from app.models import Notification, Sale, utcnow


def _sale_payload(**overrides):
    data = {
        "customer_id": "0",
        "product_id": "0",
        "quantity": "1",
        "amount": "1000",
        "sold_at": "",
        "status": "Completed",
        "reference": "",
        "notes": "",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Sales CRUD
# ---------------------------------------------------------------------------
def test_sales_page_renders(admin_client):
    resp = admin_client.get("/sales/")
    assert resp.status_code == 200
    assert b"Sales" in resp.data


def test_sales_create_page_renders(admin_client):
    resp = admin_client.get("/sales/new")
    assert resp.status_code == 200


def test_sale_record_success(admin_client, customer, product, app):
    resp = admin_client.post("/sales/new", data=_sale_payload(
        customer_id=str(customer), product_id=str(product),
        reference="INV-001"), follow_redirects=True)
    assert b"Sale recorded" in resp.data
    with app.app_context():
        s = Sale.query.first()
        assert s is not None
        assert s.total == 1000.0
        assert s.revenue == 1000.0


def test_sale_requires_customer(admin_client, product):
    resp = admin_client.post("/sales/new", data=_sale_payload(
        product_id=str(product)), follow_redirects=True)
    assert b"Select a customer" in resp.data


def test_sale_requires_product(admin_client, customer):
    resp = admin_client.post("/sales/new", data=_sale_payload(
        customer_id=str(customer)), follow_redirects=True)
    assert b"Select a product" in resp.data


def test_sale_rejects_duplicate_reference(admin_client, customer, product, app):
    admin_client.post("/sales/new", data=_sale_payload(
        customer_id=str(customer), product_id=str(product),
        reference="DUP-1"), follow_redirects=True)
    resp = admin_client.post("/sales/new", data=_sale_payload(
        customer_id=str(customer), product_id=str(product),
        reference="DUP-1"), follow_redirects=True)
    assert b"already exists" in resp.data
    with app.app_context():
        assert Sale.query.filter_by(reference="DUP-1").count() == 1


def test_sale_edit(admin_client, sale, customer, product, app):
    resp = admin_client.post(f"/sales/{sale}/edit", data=_sale_payload(
        customer_id=str(customer), product_id=str(product),
        amount="2500", quantity="2", status="Completed",
        reference=""), follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        assert Sale.query.get(sale).total == 5000.0


def test_sale_delete_only_admin(admin_client, sale, app):
    resp = admin_client.post(f"/sales/{sale}/delete", follow_redirects=True)
    assert b"deleted" in resp.data
    with app.app_context():
        assert Sale.query.get(sale) is None


def test_employee_cannot_delete_sale(employee_client, sale, app):
    resp = employee_client.post(f"/sales/{sale}/delete", follow_redirects=True)
    assert b"Only administrators" in resp.data
    with app.app_context():
        assert Sale.query.get(sale) is not None


def test_refunded_sale_counts_zero_revenue(app, customer, product):
    from app.extensions import db

    with app.app_context():
        s = Sale(customer_id=customer, product_id=product, amount=5000.0,
                 quantity=1, sold_at=utcnow(), status="Refunded")
        db.session.add(s)
        db.session.commit()
        assert s.revenue == 0.0
        assert s.total == 5000.0  # total stays, revenue excludes it


def test_monthly_revenue_excludes_pending(app, customer, product):
    from app.extensions import db
    from app.services import DashboardService

    with app.app_context():
        db.session.add(Sale(customer_id=customer, product_id=product,
                            amount=700.0, quantity=1, sold_at=utcnow(),
                            status="Pending"))
        db.session.commit()
        revenue = DashboardService.monthly_revenue()
        assert sum(revenue["values"]) == 0.0


def test_sales_csv_export_endpoint(admin_client, sale):
    resp = admin_client.get("/sales/export.csv")
    assert resp.status_code == 200
    assert "Counted Revenue" in resp.data.decode("utf-8-sig").splitlines()[0]


# ---------------------------------------------------------------------------
# Notifications (in-app only)
# ---------------------------------------------------------------------------
def test_notifications_page_renders(admin_client):
    resp = admin_client.get("/notifications/")
    assert resp.status_code == 200
    assert b"Notifications" in resp.data


def test_assignment_creates_notification(app, admin, employee, admin_client):
    """Creating a lead assigned to someone else notifies them."""
    resp = admin_client.post("/leads/new", data={
        "name": "Notify Me Lead", "email": "notify@example.org",
        "source": "Website", "product_interest": "",
        "expected_value": "100", "status": "New",
        "assigned_to": str(employee), "notes": "", "next_followup_at": "",
    }, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        note = Notification.query.filter_by(user_id=employee).first()
        assert note is not None
        assert "Notify Me Lead" in note.title


def test_lead_conversion_notifies_assignee(app, employee, admin_client, lead):
    admin_client.post(f"/leads/{lead}/convert", follow_redirects=True)
    with app.app_context():
        note = Notification.query.filter_by(
            user_id=employee, kind="converted").first()
        assert note is not None


def test_mark_notification_read(employee_client, employee, app):
    from app.extensions import db

    with app.app_context():
        n = Notification(user_id=employee, title="Test alert", kind="info")
        db.session.add(n)
        db.session.commit()
        note_id = n.id

    resp = employee_client.post(f"/notifications/{note_id}/read",
                                follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        assert Notification.query.get(note_id).is_read is True


def test_mark_all_read(employee_client, employee, app):
    from app.extensions import db

    with app.app_context():
        for i in range(3):
            db.session.add(Notification(user_id=employee, title=f"N{i}"))
        db.session.commit()

    employee_client.post("/notifications/read-all", follow_redirects=True)
    with app.app_context():
        unread = Notification.query.filter_by(user_id=employee,
                                              is_read=False).count()
        assert unread == 0


def test_mark_read_is_scoped_even_for_admin(admin_client, employee, app):
    """An admin must not mark another user's notification as read."""
    from app.extensions import db

    with app.app_context():
        n = Notification(user_id=employee, title="Employee only")
        db.session.add(n)
        db.session.commit()
        note_id = n.id

    resp = admin_client.post(f"/notifications/{note_id}/read")
    assert resp.status_code == 404
    with app.app_context():
        assert Notification.query.get(note_id).is_read is False


def test_notifications_are_scoped_to_user(employee_client, employee, app):
    from app.extensions import db
    from app.models import User

    with app.app_context():
        other = User(name="N User", email="nuser@example.com",
                     role="employee", is_active_flag=True)
        other.set_password("Nuser1234")
        db.session.add(other)
        db.session.flush()
        db.session.add(Notification(user_id=other.id, title="SECRET-ALERT"))
        db.session.add(Notification(user_id=employee, title="MINE-ALERT"))
        db.session.commit()

    resp = employee_client.get("/notifications/")
    assert b"MINE-ALERT" in resp.data
    assert b"SECRET-ALERT" not in resp.data


def test_unauthorized_access_to_notification_of_other_user(employee_client, app):
    """A user cannot mark someone else's notification as read."""
    from app.extensions import db
    from app.models import User

    with app.app_context():
        stranger = User(name="Stranger", email="stranger@example.com",
                        role="employee", is_active_flag=True)
        stranger.set_password("Stranger123")
        db.session.add(stranger)
        db.session.flush()
        n = Notification(user_id=stranger.id, title="Theirs")
        db.session.add(n)
        db.session.commit()
        note_id = n.id

    resp = employee_client.post(f"/notifications/{note_id}/read")
    assert resp.status_code == 404
