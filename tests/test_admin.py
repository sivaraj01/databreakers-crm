"""Admin panel: user management, deactivation, audit logs, settings."""
from __future__ import annotations

from app.extensions import db
from app.models import AuditLog, User, utcnow


def test_admin_home_renders(admin_client):
    resp = admin_client.get("/admin/")
    assert resp.status_code == 200
    assert b"Admin panel" in resp.data


def test_user_list_renders(admin_client, admin, employee):
    resp = admin_client.get("/admin/users")
    assert resp.status_code == 200
    assert b"admin@example.com" in resp.data
    assert b"eve@example.com" in resp.data


def test_create_employee(admin_client, app):
    resp = admin_client.post("/admin/users/new", data={
        "name": "New Hire", "email": "hire@example.com",
        "role": "employee", "password": "HirePass1234",
        "is_active_flag": "y",
    }, follow_redirects=True)
    assert b"created" in resp.data
    with app.app_context():
        u = User.query.filter_by(email="hire@example.com").first()
        assert u is not None
        assert u.role == "employee"
        assert u.check_password("HirePass1234")
        assert not u.password_hash.startswith("HirePass")


def test_create_employee_requires_password(admin_client, app):
    resp = admin_client.post("/admin/users/new", data={
        "name": "No Password", "email": "nopw@example.com",
        "role": "employee", "is_active_flag": "y",
    }, follow_redirects=True)
    assert b"required" in resp.data.lower()
    with app.app_context():
        assert User.query.filter_by(email="nopw@example.com").first() is None


def test_create_employee_rejects_duplicate_email(admin_client, employee):
    resp = admin_client.post("/admin/users/new", data={
        "name": "Dup", "email": "eve@example.com", "role": "employee",
        "password": "Whatever123", "is_active_flag": "y",
    }, follow_redirects=True)
    assert b"already registered" in resp.data


def test_edit_user_without_password_keeps_old_one(admin_client, employee, app):
    resp = admin_client.post(f"/admin/users/{employee}/edit", data={
        "name": "Eve Updated", "email": "eve@example.com",
        "role": "employee", "is_active_flag": "y", "password": "",
    }, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        u = User.query.get(employee)
        assert u.name == "Eve Updated"
        assert u.check_password("EmployeePass123")  # unchanged


def test_deactivate_employee(admin_client, employee, app):
    resp = admin_client.post(f"/admin/users/{employee}/toggle",
                             follow_redirects=True)
    assert b"deactivated" in resp.data
    with app.app_context():
        assert User.query.get(employee).is_active_flag is False


def test_deactivated_employee_cannot_login(client, app, employee):
    with app.app_context():
        u = User.query.get(employee)
        u.is_active_flag = False
        db.session.commit()
    resp = client.post("/auth/login", data={
        "email": "eve@example.com", "password": "EmployeePass123"},
        follow_redirects=True)
    assert b"deactivated" in resp.data.lower()


def test_cannot_deactivate_self(admin_client, app, admin):
    resp = admin_client.post(f"/admin/users/{admin}/toggle",
                             follow_redirects=True)
    assert b"cannot deactivate your own" in resp.data
    with app.app_context():
        assert User.query.get(admin).is_active_flag is True


def test_cannot_demote_last_admin(admin_client, app, admin):
    resp = admin_client.post(f"/admin/users/{admin}/edit", data={
        "name": "Ada Admin", "email": "admin@example.com",
        "role": "employee", "is_active_flag": "y", "password": "",
    }, follow_redirects=True)
    assert b"last administrator" in resp.data
    with app.app_context():
        assert User.query.get(admin).role == "admin"


def test_audit_log_page_renders(admin_client):
    resp = admin_client.get("/admin/audit")
    assert resp.status_code == 200
    assert b"Audit logs" in resp.data


def test_login_is_audited(admin_client, app):
    with app.app_context():
        entry = AuditLog.query.filter_by(action="login").first()
        assert entry is not None
        assert entry.actor == "admin@example.com"


def test_activity_monitor_renders(admin_client):
    resp = admin_client.get("/admin/activity")
    assert resp.status_code == 200


def test_settings_page_renders(admin_client):
    resp = admin_client.get("/admin/settings")
    assert resp.status_code == 200
    assert b"Settings" in resp.data


def test_settings_update(admin_client, app):
    resp = admin_client.post("/admin/settings", data={
        "site_name": "My CRM", "items_per_page": "25",
        "enable_zia_llm": "y",
    }, follow_redirects=True)
    assert b"Settings saved" in resp.data


def test_settings_rejects_bad_page_size(admin_client, app):
    resp = admin_client.post("/admin/settings", data={
        "site_name": "My CRM", "items_per_page": "9999",
    }, follow_redirects=True)
    assert b"between 5 and 100" in resp.data


def test_settings_are_persisted_to_database(admin_client, app):
    """Saved settings must land in ``app_settings`` (survives restarts).

    Regression test: the table has no ORM model, so ``db.create_all()`'
    skipped it and ``save_settings()`` silently did nothing.
    """
    resp = admin_client.post("/admin/settings", data={
        "site_name": "Persisted CRM", "items_per_page": "25",
    }, follow_redirects=True)
    assert resp.status_code == 200

    from app.admin.routes import get_settings, _settings_table_exists

    with app.app_context():
        assert _settings_table_exists() is True
        saved = get_settings()
    assert saved["site_name"] == "Persisted CRM"
    assert saved["leads_per_page"] == "25"


def test_persisted_settings_are_reloaded_at_startup(admin_client, app):
    """A new app instance must pick up values stored in the database."""
    from app import _load_persisted_settings
    from app.admin.routes import save_settings

    with app.app_context():
        save_settings({"site_name": "Restarted CRM", "leads_per_page": "20"})

    app.config["APP_NAME"] = "DataBreakers CRM"
    app.config["LEADS_PER_PAGE"] = 12
    with app.app_context():
        _load_persisted_settings(app)
    assert app.config["APP_NAME"] == "Restarted CRM"
    assert app.config["LEADS_PER_PAGE"] == 20


def test_broadcast_notification(admin_client, employee, app):
    resp = admin_client.post("/admin/notifications/broadcast", data={
        "title": "Quarterly targets", "body": "Review the new targets.",
    }, follow_redirects=True)
    assert b"sent to" in resp.data
    with app.app_context():
        from app.models import Notification

        assert Notification.query.filter_by(
            title="Quarterly targets").count() >= 1


def test_broadcast_requires_title(admin_client):
    resp = admin_client.post("/admin/notifications/broadcast",
                             data={"title": "", "body": "x"},
                             follow_redirects=True)
    assert b"title is required" in resp.data


def test_employee_cannot_access_admin_json_endpoints(employee_client):
    resp = employee_client.get("/admin/audit")
    assert resp.status_code == 403
