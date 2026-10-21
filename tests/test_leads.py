"""Tests for lead CRUD, validation and duplicate prevention."""
from __future__ import annotations

from datetime import datetime, timedelta

from app.models import Lead, utcnow
from tests.conftest import login


def _lead_payload(**overrides):
    data = {
        "name": "New Prospect",
        "email": "new.prospect@example.com",
        "phone": "+91 9111111111",
        "company": "Globex",
        "source": "Referral",
        "product_interest": "",
        "expected_value": "7500",
        "status": "New",
        "assigned_to": "0",
        "notes": "Created by test",
        "next_followup_at": "",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
def test_lead_create_page_renders(admin_client):
    resp = admin_client.get("/leads/new")
    assert resp.status_code == 200
    assert b"New lead" in resp.data


def test_lead_create_success(admin_client, app):
    resp = admin_client.post("/leads/new", data=_lead_payload(),
                             follow_redirects=True)
    assert resp.status_code == 200
    assert b"Lead created successfully" in resp.data
    with app.app_context():
        lead = Lead.query.filter_by(email="new.prospect@example.com").first()
        assert lead is not None
        assert lead.status == "New"
        assert lead.expected_value == 7500.0


def test_lead_create_requires_name(admin_client, app):
    payload = _lead_payload(name="")
    resp = admin_client.post("/leads/new", data=payload, follow_redirects=True)
    assert resp.status_code == 200
    assert b"This field is required" in resp.data
    with app.app_context():
        assert Lead.query.filter_by(email="new.prospect@example.com").count() == 0


def test_lead_create_rejects_invalid_email(admin_client):
    payload = _lead_payload(email="not-an-email")
    resp = admin_client.post("/leads/new", data=payload, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Invalid email" in resp.data


def test_lead_create_rejects_duplicate_email(admin_client, lead, app):
    payload = _lead_payload(email="prospect@example.com", name="Duplicate Lead")
    resp = admin_client.post("/leads/new", data=payload, follow_redirects=True)
    assert b"already exists" in resp.data
    with app.app_context():
        assert Lead.query.filter_by(email="prospect@example.com").count() == 1


def test_lead_create_rejects_negative_value(admin_client):
    payload = _lead_payload(expected_value="-100")
    resp = admin_client.post("/leads/new", data=payload, follow_redirects=True)
    assert b"between 0" in resp.data


def test_lead_create_rejects_invalid_status(admin_client):
    payload = _lead_payload(status="NotAStatus")
    resp = admin_client.post("/leads/new", data=payload, follow_redirects=True)
    assert b"Not a valid choice" in resp.data


def test_lead_create_rejects_past_followup(admin_client):
    past = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
    payload = _lead_payload(next_followup_at=past)
    resp = admin_client.post("/leads/new", data=payload, follow_redirects=True)
    assert b"cannot be in the past" in resp.data


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
def test_lead_list_renders(admin_client, lead):
    resp = admin_client.get("/leads/")
    assert resp.status_code == 200
    assert b"Test Prospect" in resp.data


def test_lead_detail_renders(admin_client, lead):
    resp = admin_client.get(f"/leads/{lead}")
    assert resp.status_code == 200
    assert b"Test Prospect" in resp.data


def test_lead_detail_404(admin_client):
    assert admin_client.get("/leads/999999").status_code == 404


def test_lead_search_filters_by_name(admin_client, lead, other_employee_lead):
    resp = admin_client.get("/leads/?q=Foreign")
    assert b"Foreign Lead" in resp.data
    assert b"Test Prospect" not in resp.data


def test_lead_status_filter(admin_client, lead, other_employee_lead):
    resp = admin_client.get("/leads/?status=Qualified")
    assert b"Foreign Lead" in resp.data
    assert b"Test Prospect" not in resp.data


def test_lead_pagination(admin_client, employee, app):
    from app.extensions import db

    with app.app_context():
        from app.models import User

        emp = User.query.filter_by(email="eve@example.com").first()
        for i in range(30):
            db.session.add(Lead(name=f"Bulk Lead {i}", email=f"bulk{i}@example.com",
                                source="Website", status="New",
                                assigned_to=emp.id))
        db.session.commit()

    first = admin_client.get("/leads/?page=1")
    second = admin_client.get("/leads/?page=2")
    last = admin_client.get("/leads/?page=3")
    assert first.status_code == 200 and second.status_code == 200

    # Default sort is newest-first, so the highest-numbered lead leads page 1
    # and the oldest (Bulk Lead 0) sits on the final page - proof that rows
    # actually paginate instead of repeating.
    assert b"Bulk Lead 29" in first.data
    assert b"Bulk Lead 0" not in first.data
    assert b"Bulk Lead 29" not in second.data
    assert b"Bulk Lead 0" in last.data
    # Pages must not show identical content.
    assert first.data != second.data


def test_lead_sort_by_value(admin_client, lead, other_employee_lead):
    resp = admin_client.get("/leads/?sort=value")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------
def test_lead_edit_page_renders(admin_client, lead):
    resp = admin_client.get(f"/leads/{lead}/edit")
    assert resp.status_code == 200
    assert b"Edit" in resp.data


def test_lead_update_success(admin_client, lead, app):
    resp = admin_client.post(f"/leads/{lead}/edit", data=_lead_payload(
        name="Renamed Prospect", email="prospect@example.com",
        status="Contacted"), follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        updated = Lead.query.get(lead)
        assert updated.name == "Renamed Prospect"
        assert updated.status == "Contacted"


def test_lead_update_duplicate_email_blocked(admin_client, lead, other_employee_lead, app):
    foreign = None
    with app.app_context():
        foreign = Lead.query.get(other_employee_lead).email
    resp = admin_client.post(f"/leads/{lead}/edit",
                             data=_lead_payload(email=foreign), follow_redirects=True)
    assert b"already exists" in resp.data
    with app.app_context():
        assert Lead.query.get(lead).email == "prospect@example.com"


def test_quick_status_change(admin_client, lead, app):
    resp = admin_client.post(f"/leads/{lead}/status", data={"status": "Interested"},
                             follow_redirects=True)
    assert b"Status changed to Interested" in resp.data
    with app.app_context():
        assert Lead.query.get(lead).status == "Interested"


def test_quick_status_rejects_invalid_value(admin_client, lead, app):
    resp = admin_client.post(f"/leads/{lead}/status", data={"status": "Bogus"},
                             follow_redirects=True)
    assert b"Invalid status" in resp.data
    with app.app_context():
        assert Lead.query.get(lead).status == "New"


def test_lead_notes_append(admin_client, lead, app):
    resp = admin_client.post(f"/leads/{lead}/notes",
                             data={"body": "Spoke to the prospect."},
                             follow_redirects=True)
    assert b"Note added" in resp.data
    with app.app_context():
        note = Lead.query.get(lead).notes
        assert "Spoke to the prospect." in note


def test_lead_note_rejects_empty(admin_client, lead):
    resp = admin_client.post(f"/leads/{lead}/notes", data={"body": ""},
                             follow_redirects=True)
    assert b"Note cannot be empty" in resp.data


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------
def test_lead_delete_success(admin_client, lead, app):
    resp = admin_client.post(f"/leads/{lead}/delete", follow_redirects=True)
    assert b"deleted" in resp.data
    with app.app_context():
        assert Lead.query.get(lead) is None


def test_lead_delete_missing_404(admin_client):
    assert admin_client.post("/leads/999999/delete").status_code == 404


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------
def test_lead_convert_creates_customer(admin_client, lead, app):
    resp = admin_client.post(f"/leads/{lead}/convert", follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        converted = Lead.query.get(lead)
        assert converted.status == "Converted"
        assert converted.customer_id is not None
        from app.models import Customer

        assert Customer.query.filter_by(email="prospect@example.com").count() == 1


def test_lead_convert_twice_does_not_duplicate_customer(admin_client, lead, app):
    admin_client.post(f"/leads/{lead}/convert", follow_redirects=True)
    resp = admin_client.post(f"/leads/{lead}/convert", follow_redirects=True)
    assert b"already converted" in resp.data
    with app.app_context():
        from app.models import Customer

        assert Customer.query.filter_by(email="prospect@example.com").count() == 1
