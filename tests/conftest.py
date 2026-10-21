"""Shared pytest fixtures for the DataBreakers CRM test suite.

Each test gets a fresh in-memory database, so tests are isolated and
can run in any order.
"""
from __future__ import annotations

import pytest

from app import create_app
from app.extensions import db as _db
from app.models import (Customer, FollowUp, Lead, Product, Sale, User,
                        UserRole, utcnow)


@pytest.fixture(scope="session")
def app():
    """Application instance configured for testing (in-memory SQLite)."""
    application = create_app("testing")
    yield application


@pytest.fixture(autouse=True)
def _fresh_db(app):
    """Create schema before each test and drop everything afterwards."""
    with app.app_context():
        _db.drop_all()
        _db.create_all()
        # ``app_settings`` has no ORM model, so ``drop_all()`` leaves it
        # (and its rows) behind. Clear it so one test's saved settings
        # cannot leak into the next test.
        if _db.inspect(_db.engine).has_table("app_settings"):
            _db.session.execute(_db.text("DELETE FROM app_settings"))
            _db.session.commit()
    yield
    with app.app_context():
        _db.session.remove()
        _db.drop_all()


@pytest.fixture(autouse=True)
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _restore_app_config(app):
    """Snapshot mutable app config and restore it after every test.

    Some routes legitimately mutate config at runtime (e.g. the admin
    settings page changes APP_NAME / LEADS_PER_PAGE). Because the ``app``
    fixture is session-scoped, that mutation would otherwise leak into
    later tests and make them order-dependent.
    """
    watched = ("APP_NAME", "LEADS_PER_PAGE", "CUSTOMERS_PER_PAGE",
               "SALES_PER_PAGE", "WTF_CSRF_ENABLED", "ZIA_LLM_API_KEY")
    before = {k: app.config.get(k) for k in watched}
    yield
    app.config.update(before)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
@pytest.fixture
def admin(app):
    with app.app_context():
        user = User(name="Ada Admin", email="admin@example.com",
                    role=UserRole.ADMIN.value, is_active_flag=True)
        user.set_password("AdminPass123")
        _db.session.add(user)
        _db.session.commit()
        return user.id


@pytest.fixture
def employee(app):
    with app.app_context():
        user = User(name="Eve Employee", email="eve@example.com",
                    role=UserRole.EMPLOYEE.value, is_active_flag=True)
        user.set_password("EmployeePass123")
        _db.session.add(user)
        _db.session.commit()
        return user.id


@pytest.fixture
def inactive_user(app):
    with app.app_context():
        user = User(name="Dan Deactivated", email="dan@example.com",
                    role=UserRole.EMPLOYEE.value, is_active_flag=False)
        user.set_password("Whatever123")
        _db.session.add(user)
        _db.session.commit()
        return user.id


def login(client, email: str, password: str):
    """POST to the login endpoint and return the response."""
    return client.post("/auth/login", data={"email": email, "password": password},
                       follow_redirects=True)


@pytest.fixture
def admin_client(client, admin):
    login(client, "admin@example.com", "AdminPass123")
    return client


@pytest.fixture
def employee_client(client, employee):
    login(client, "eve@example.com", "EmployeePass123")
    return client


# ---------------------------------------------------------------------------
# Domain objects
# ---------------------------------------------------------------------------
@pytest.fixture
def lead(app, employee):
    with app.app_context():
        lead = Lead(name="Test Prospect", email="prospect@example.com",
                    phone="+91 900000001", company="Acme",
                    source="Website", expected_value=5000.0,
                    status="New", assigned_to=employee)
        _db.session.add(lead)
        _db.session.commit()
        return lead.id


@pytest.fixture
def other_employee_lead(app, admin):
    """A lead owned by someone else, used for permission tests."""
    with app.app_context():
        other = User(name="Sam Someone", email="sam@example.com",
                     role=UserRole.EMPLOYEE.value, is_active_flag=True)
        other.set_password("SamPass12345")
        _db.session.add(other)
        _db.session.flush()
        lead = Lead(name="Foreign Lead", email="foreign@example.com",
                    source="Referral", status="Qualified",
                    assigned_to=other.id)
        _db.session.add(lead)
        _db.session.commit()
        return lead.id


@pytest.fixture
def customer(app, employee):
    with app.app_context():
        customer = Customer(name="Acme Corp", email="hello@acme.example.com",
                            company="Acme", segment="Premium",
                            created_by=employee)
        _db.session.add(customer)
        _db.session.commit()
        return customer.id


@pytest.fixture
def product(app):
    with app.app_context():
        p = Product(name="Analytics Suite", sku="DB-ANL",
                    category="Software", price=4500.0, is_active=True)
        _db.session.add(p)
        _db.session.commit()
        return p.id


@pytest.fixture
def sale(app, customer, product, employee):
    with app.app_context():
        s = Sale(customer_id=customer, product_id=product, amount=4500.0,
                 quantity=2, sold_at=utcnow(), status="Completed",
                 recorded_by=employee)
        _db.session.add(s)
        _db.session.commit()
        return s.id


@pytest.fixture
def followup(app, lead, employee):
    with app.app_context():
        f = FollowUp(lead_id=lead, assigned_to=employee, created_by=employee,
                     due_at=utcnow(), kind="Call", status="Pending")
        _db.session.add(f)
        _db.session.commit()
        return f.id
