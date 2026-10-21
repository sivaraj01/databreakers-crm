"""Tests for follow-up scheduling, completion and bucketing."""
from __future__ import annotations

from datetime import datetime, timedelta

from app.extensions import db
from app.models import FollowUp, User, utcnow
from app.services import FollowUpService


def _payload(**overrides):
    future = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
    data = {
        "subject_type": "lead",
        "lead_id": "0",
        "customer_id": "0",
        "assigned_to": "0",
        "due_at": future,
        "kind": "Call",
        "notes": "Ask about budget",
    }
    data.update(overrides)
    return data


def test_followups_page_renders(employee_client, followup):
    resp = employee_client.get("/followups/")
    assert resp.status_code == 200
    assert b"Follow-ups" in resp.data


def test_followup_create_success(employee_client, lead, employee, app):
    future = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
    resp = employee_client.post("/followups/new", data=_payload(
        lead_id=str(lead), assigned_to=str(employee), due_at=future),
        follow_redirects=True)
    assert b"Follow-up scheduled" in resp.data
    with app.app_context():
        assert FollowUp.query.count() == 1


def test_followup_create_requires_due_time(employee_client, lead, employee):
    resp = employee_client.post("/followups/new", data=_payload(
        lead_id=str(lead), assigned_to=str(employee), due_at=""),
        follow_redirects=True)
    assert b"This field is required" in resp.data


def test_followup_create_rejects_past_due(employee_client, lead, employee):
    past = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
    resp = employee_client.post("/followups/new", data=_payload(
        lead_id=str(lead), assigned_to=str(employee), due_at=past),
        follow_redirects=True)
    assert b"cannot be in the past" in resp.data


def test_followup_lead_subject_requires_lead(employee_client, employee):
    resp = employee_client.post("/followups/new", data=_payload(
        subject_type="lead", lead_id="0", assigned_to=str(employee)),
        follow_redirects=True)
    assert b"Select the lead" in resp.data


def test_followup_customer_subject_requires_customer(employee_client, employee):
    resp = employee_client.post("/followups/new", data=_payload(
        subject_type="customer", lead_id="0", customer_id="0",
        assigned_to=str(employee)), follow_redirects=True)
    assert b"Select the customer" in resp.data


def test_followup_complete_requires_outcome(employee_client, followup, app):
    resp = employee_client.post(f"/followups/{followup}/complete",
                                data={"outcome": ""}, follow_redirects=True)
    assert b"Describe the outcome" in resp.data
    with app.app_context():
        assert FollowUp.query.get(followup).status == "Pending"


def test_followup_complete_success(employee_client, followup, app):
    resp = employee_client.post(f"/followups/{followup}/complete",
                                data={"outcome": "Spoke, sending quote."},
                                follow_redirects=True)
    assert b"completed" in resp.data
    with app.app_context():
        f = FollowUp.query.get(followup)
        assert f.status == "Completed"
        assert f.outcome == "Spoke, sending quote."
        assert f.completed_at is not None


def test_followup_cancel(employee_client, followup, app):
    resp = employee_client.post(f"/followups/{followup}/cancel",
                                follow_redirects=True)
    assert b"cancelled" in resp.data
    with app.app_context():
        assert FollowUp.query.get(followup).status == "Cancelled"


def test_followup_reschedule(employee_client, followup, app):
    new_due = datetime.now() + timedelta(days=9)
    resp = employee_client.post(
        f"/followups/{followup}/reschedule",
        data={"due_at": new_due.strftime("%Y-%m-%dT%H:%M")},
        follow_redirects=True)
    assert b"rescheduled" in resp.data
    with app.app_context():
        stored = FollowUp.query.get(followup)
        assert stored.due_at.date() == new_due.date()


def test_followup_reschedule_rejects_garbage(employee_client, followup, app):
    with app.app_context():
        original = FollowUp.query.get(followup).due_at
    resp = employee_client.post(f"/followups/{followup}/reschedule",
                                data={"due_at": "not-a-date"},
                                follow_redirects=True)
    assert b"Invalid date" in resp.data
    with app.app_context():
        assert FollowUp.query.get(followup).due_at == original


def _other_followup(app, lead_id: int) -> int:
    """Create a follow-up assigned to a different employee."""
    with app.app_context():
        other = User(name="Other Person", email="other@example.com",
                     role="employee", is_active_flag=True)
        other.set_password("OtherPass123")
        db.session.add(other)
        db.session.flush()
        f = FollowUp(lead_id=lead_id, assigned_to=other.id, created_by=other.id,
                     due_at=utcnow(), kind="Call", status="Pending")
        db.session.add(f)
        db.session.commit()
        return f.id


def test_employee_cannot_see_others_followups(employee_client, app, lead):
    hidden_id = _other_followup(app, lead)
    resp = employee_client.post(f"/followups/{hidden_id}/cancel")
    assert resp.status_code == 404


def test_admin_can_see_all_followups(admin_client, app, lead):
    hidden_id = _other_followup(app, lead)
    resp = admin_client.post(f"/followups/{hidden_id}/cancel")
    assert resp.status_code == 302  # allowed -> redirects back


def test_overdue_bucket_is_reported(app, lead, employee):
    with app.app_context():
        f = FollowUp(lead_id=lead, assigned_to=employee, created_by=employee,
                     due_at=utcnow() - timedelta(days=3),
                     kind="Call", status="Pending")
        db.session.add(f)
        db.session.commit()

    with app.app_context():
        buckets = FollowUpService.buckets()
        assert len(buckets["overdue"]) == 1
        assert buckets["overdue"][0].due_state == "overdue"
        assert buckets["today"] == []


def test_today_bucket_is_reported(app, lead, employee):
    with app.app_context():
        now = utcnow()
        f = FollowUp(lead_id=lead, assigned_to=employee, created_by=employee,
                     due_at=now.replace(hour=min(now.hour + 1, 23), minute=30),
                     kind="Meeting", status="Pending")
        db.session.add(f)
        db.session.commit()

    with app.app_context():
        buckets = FollowUpService.buckets()
        assert len(buckets["today"]) == 1
        assert buckets["today"][0].due_state in ("today", "upcoming")


def test_reminders_endpoint_returns_json(employee_client, followup):
    resp = employee_client.get("/followups/reminders")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert isinstance(payload, dict)
    assert "items" in payload and "total" in payload
