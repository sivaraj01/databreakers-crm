"""Tests for authentication, session security and role-based authorization."""
from __future__ import annotations

from tests.conftest import login


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------
def test_login_page_renders(client):
    resp = client.get("/auth/login")
    assert resp.status_code == 200
    assert b"DataBreakers" in resp.data


def test_login_with_valid_credentials(client, admin):
    resp = login(client, "admin@example.com", "AdminPass123")
    assert resp.status_code == 200
    assert b"Dashboard" in resp.data


def test_login_with_wrong_password_is_rejected(client, admin):
    resp = login(client, "admin@example.com", "wrong-password")
    assert b"Invalid email or password" in resp.data


def test_login_with_unknown_email_is_rejected(client):
    resp = login(client, "nobody@example.com", "Whatever123")
    assert b"Invalid email or password" in resp.data


def test_deactivated_user_cannot_log_in(client, inactive_user):
    resp = login(client, "dan@example.com", "Whatever123")
    assert b"deactivated" in resp.data.lower()


def test_logout(client, admin):
    login(client, "admin@example.com", "AdminPass123")
    resp = client.get("/auth/logout", follow_redirects=True)
    assert resp.status_code == 200
    # After logout, protected pages redirect to the login screen.
    resp = client.get("/", follow_redirects=True)
    assert b"Sign in" in resp.data


def test_session_cookie_is_httponly(client, admin):
    """Session cookies must carry HttpOnly so JS cannot read them."""
    login(client, "admin@example.com", "AdminPass123")
    resp = client.get("/auth/login")
    set_cookie = resp.headers.get("Set-Cookie", "")
    if "session=" in set_cookie:
        assert "HttpOnly" in set_cookie


# ---------------------------------------------------------------------------
# Unauthorized access
# ---------------------------------------------------------------------------
def test_dashboard_requires_login(client):
    resp = client.get("/", follow_redirects=True)
    assert b"Sign in" in resp.data


def test_leads_require_login(client):
    assert client.get("/leads/").status_code == 302


def test_customers_require_login(client):
    assert client.get("/customers/").status_code == 302


def test_sales_require_login(client):
    assert client.get("/sales/").status_code == 302


def test_admin_routes_require_login(client):
    assert client.get("/admin/users").status_code == 302


def test_protected_route_redirects_to_login(client):
    resp = client.get("/analytics/")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


# ---------------------------------------------------------------------------
# Role-based authorization
# ---------------------------------------------------------------------------
def test_employee_cannot_open_admin_pages(employee_client):
    for url in ("/admin/", "/admin/users", "/admin/audit",
                "/admin/settings", "/admin/users/new"):
        resp = employee_client.get(url)
        assert resp.status_code == 403, f"{url} should be forbidden for employees"


def test_employee_cannot_create_products(employee_client):
    resp = employee_client.get("/products/new")
    assert resp.status_code == 403


def test_admin_can_open_admin_pages(admin_client):
    for url in ("/admin/", "/admin/users", "/admin/audit",
                "/admin/activity", "/admin/settings"):
        resp = admin_client.get(url)
        assert resp.status_code == 200, f"{url} should render for admins"


def test_employee_sees_only_their_own_leads(employee_client, lead, other_employee_lead):
    resp = employee_client.get("/leads/")
    assert resp.status_code == 200
    assert b"Test Prospect" in resp.data
    assert b"Foreign Lead" not in resp.data


def test_admin_sees_all_leads(admin_client, lead, other_employee_lead):
    resp = admin_client.get("/leads/")
    assert b"Test Prospect" in resp.data
    assert b"Foreign Lead" in resp.data


def test_employee_cannot_open_a_foreign_lead(employee_client, other_employee_lead):
    resp = employee_client.get(f"/leads/{other_employee_lead}")
    assert resp.status_code == 404


def test_employee_cannot_delete_a_foreign_lead(employee_client, other_employee_lead):
    resp = employee_client.post(f"/leads/{other_employee_lead}/delete")
    assert resp.status_code == 404


def test_admin_can_open_any_lead(admin_client, other_employee_lead):
    resp = admin_client.get(f"/leads/{other_employee_lead}")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Password hashing & profile
# ---------------------------------------------------------------------------
def test_password_is_never_stored_in_plaintext(app, admin):
    with app.app_context():
        from app.models import User

        user = User.query.filter_by(email="admin@example.com").first()
        assert user.password_hash != "AdminPass123"
        assert "AdminPass123" not in user.password_hash
        # pbkdf2 format: method$salt$hash
        assert user.password_hash.startswith("pbkdf2:")
        assert user.check_password("AdminPass123")
        assert not user.check_password("not-the-password")


def test_profile_page_renders(employee_client):
    resp = employee_client.get("/auth/profile")
    assert resp.status_code == 200
    assert b"My profile" in resp.data


def test_profile_update_changes_name(employee_client, employee, app):
    resp = employee_client.post("/auth/profile",
                                data={"name": "Eve Renamed",
                                      "email": "eve@example.com"},
                                follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        from app.models import User

        assert User.query.get(employee).name == "Eve Renamed"


def test_profile_email_conflict_is_rejected(employee_client, admin, app):
    resp = employee_client.post("/auth/profile",
                                data={"name": "Eve Employee",
                                      "email": "admin@example.com"},
                                follow_redirects=True)
    assert b"already registered" in resp.data


def test_change_password_rejects_wrong_current(employee_client, app):
    resp = employee_client.post("/auth/change-password", data={
        "current_password": "TotallyWrong1",
        "new_password": "NewPass1234",
        "confirm_password": "NewPass1234",
    }, follow_redirects=True)
    assert b"incorrect" in resp.data.lower()


def test_change_password_rejects_weak_password(employee_client):
    resp = employee_client.post("/auth/change-password", data={
        "current_password": "EmployeePass123",
        "new_password": "short",
        "confirm_password": "short",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"at least 8 characters" in resp.data


def test_change_password_success(employee_client, employee, app):
    resp = employee_client.post("/auth/change-password", data={
        "current_password": "EmployeePass123",
        "new_password": "BrandNew1234",
        "confirm_password": "BrandNew1234",
    }, follow_redirects=True)
    assert b"Password updated" in resp.data
    with app.app_context():
        from app.models import User

        user = User.query.get(employee)
        assert user.check_password("BrandNew1234")


def test_password_mismatch_is_rejected(employee_client):
    resp = employee_client.post("/auth/change-password", data={
        "current_password": "EmployeePass123",
        "new_password": "BrandNew1234",
        "confirm_password": "Different999",
    }, follow_redirects=True)
    assert b"do not match" in resp.data


# ---------------------------------------------------------------------------
# CSRF protection
# ---------------------------------------------------------------------------
def test_csrf_is_enabled_outside_testing():
    """CSRF must be active in the real (non-testing) app."""
    from app import create_app

    application = create_app("development")
    assert application.config.get("WTF_CSRF_ENABLED") is not False
    assert application.config["SECRET_KEY"]


def test_csrf_token_present_on_login_page(app, client):
    """With CSRF protection enabled, the login form must render its token."""
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        resp = client.get("/auth/login")
        assert b"csrf_token" in resp.data
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
