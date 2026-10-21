"""Tests for customer CRUD, duplicate prevention and interactions."""
from __future__ import annotations

from app.extensions import db
from app.models import Customer, Sale, utcnow


def _payload(**overrides):
    data = {
        "name": "Globex Industries",
        "email": "contact@globex.example.com",
        "phone": "+91 9222222222",
        "company": "Globex",
        "city": "Chennai",
        "country": "India",
        "industry": "Manufacturing",
        "segment": "Standard",
        "notes": "",
    }
    data.update(overrides)
    return data


def test_customer_list_renders(admin_client, customer):
    resp = admin_client.get("/customers/")
    assert resp.status_code == 200
    assert b"Acme Corp" in resp.data


def test_customer_detail_renders(admin_client, customer):
    resp = admin_client.get(f"/customers/{customer}")
    assert resp.status_code == 200
    assert b"Acme Corp" in resp.data


def test_customer_create_success(admin_client, app):
    resp = admin_client.post("/customers/new", data=_payload(),
                             follow_redirects=True)
    assert b"Customer created" in resp.data
    with app.app_context():
        c = Customer.query.filter_by(email="contact@globex.example.com").first()
        assert c is not None
        assert c.segment == "Standard"


def test_customer_create_requires_name(admin_client, app):
    resp = admin_client.post("/customers/new", data=_payload(name=""),
                             follow_redirects=True)
    assert b"This field is required" in resp.data
    with app.app_context():
        assert Customer.query.count() == 0


def test_customer_rejects_invalid_email(admin_client):
    resp = admin_client.post("/customers/new", data=_payload(email="nope"),
                             follow_redirects=True)
    assert b"Invalid email" in resp.data


def test_customer_rejects_duplicate_email(admin_client, customer):
    resp = admin_client.post("/customers/new",
                             data=_payload(email="hello@acme.example.com",
                                           name="Acme Duplicate"),
                             follow_redirects=True)
    assert b"already exists" in resp.data


def test_customer_edit_allows_own_email(admin_client, customer):
    """Editing without changing the email must NOT trigger the duplicate check."""
    resp = admin_client.post(f"/customers/{customer}/edit",
                             data=_payload(name="Acme Corp Renamed"),
                             follow_redirects=True)
    assert b"Customer updated" in resp.data


def test_customer_edit_conflict_blocked(admin_client, customer, app):
    """Editing a customer onto another customer's email must be blocked."""
    with app.app_context():
        other = Customer(name="Other Buyer", email="contact@globex.example.com",
                         company="Other", created_by=1)
        db.session.add(other)
        db.session.commit()

    resp = admin_client.post(f"/customers/{customer}/edit",
                             data=_payload(email="contact@globex.example.com"),
                             follow_redirects=True)
    assert b"already exists" in resp.data
    with app.app_context():
        c = Customer.query.get(customer)
        assert c.email == "hello@acme.example.com"  # unchanged


def test_customer_delete_success(admin_client, customer, app):
    resp = admin_client.post(f"/customers/{customer}/delete",
                             follow_redirects=True)
    assert b"deleted" in resp.data
    with app.app_context():
        assert Customer.query.get(customer) is None


def test_customer_with_sales_cannot_be_deleted(admin_client, customer, sale, app):
    resp = admin_client.post(f"/customers/{customer}/delete",
                             follow_redirects=True)
    assert b"cannot be deleted" in resp.data
    with app.app_context():
        assert Customer.query.get(customer) is not None


def test_customer_interaction_logged(admin_client, customer, app):
    resp = admin_client.post(f"/customers/{customer}/interactions",
                             data={"kind": "Call", "summary": "Discussed renewal."},
                             follow_redirects=True)
    assert b"Interaction logged" in resp.data
    with app.app_context():
        c = Customer.query.get(customer)
        assert c.interactions.count() == 1


def test_customer_interaction_rejects_empty(admin_client, customer, app):
    resp = admin_client.post(f"/customers/{customer}/interactions",
                             data={"kind": "Call", "summary": ""},
                             follow_redirects=True)
    assert b"required" in resp.data.lower()
    with app.app_context():
        assert Customer.query.get(customer).interactions.count() == 0


def test_customer_quick_sale_records_revenue(admin_client, customer, product, app):
    resp = admin_client.post(f"/customers/{customer}/sales", data={
        "product_id": str(product), "quantity": "3", "amount": "1000",
        "status": "Completed", "sold_at": "",
    }, follow_redirects=True)
    assert b"Sale recorded" in resp.data
    with app.app_context():
        sale = Sale.query.first()
        assert sale is not None
        assert sale.total == 3000.0


def test_customer_quick_sale_rejects_bad_quantity(admin_client, customer, product, app):
    resp = admin_client.post(f"/customers/{customer}/sales", data={
        "product_id": str(product), "quantity": "abc", "amount": "1000",
        "status": "Completed", "sold_at": "",
    }, follow_redirects=True)
    assert b"must be numbers" in resp.data
    with app.app_context():
        assert Sale.query.count() == 0


def test_customer_quick_sale_rejects_negative_amount(admin_client, customer, product, app):
    resp = admin_client.post(f"/customers/{customer}/sales", data={
        "product_id": str(product), "quantity": "1", "amount": "-50",
        "status": "Completed", "sold_at": "",
    }, follow_redirects=True)
    assert b"valid product" in resp.data
    with app.app_context():
        assert Sale.query.count() == 0


def test_customer_search(admin_client, customer):
    resp = admin_client.get("/customers/?q=Acme")
    assert b"Acme Corp" in resp.data
    resp = admin_client.get("/customers/?q=zzzz-nothing")
    assert b"No customers yet" in resp.data
