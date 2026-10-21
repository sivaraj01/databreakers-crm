"""Zia AI assistant: local deterministic engine, permissions, honesty rules."""
from __future__ import annotations

import json

from app.assistant import zia


# ---------------------------------------------------------------------------
# Capability & routing
# ---------------------------------------------------------------------------
def test_capabilities_list_is_populated():
    assert len(zia.CAPABILITIES) >= 5
    assert any("follow-up today" in c.lower() for c in zia.CAPABILITIES)


def test_intent_routing_detects_known_questions():
    assert zia._detect_intent("Which leads need follow-up today?") == "followups_today"
    assert zia._detect_intent("Summarize this month's sales") == "sales_month"
    assert zia._detect_intent("Which lead sources perform best?") == "lead_sources"
    assert zia._detect_intent("Explain the lead conversion metrics.") == "conversion"
    assert zia._detect_intent("Summarize customer activity.") == "customers"
    assert zia._detect_intent("Explain prediction results.") == "predictions"
    assert zia._detect_intent("something odd entirely") == "help"


# ---------------------------------------------------------------------------
# Local engine on an EMPTY database
# ---------------------------------------------------------------------------
def test_followups_today_empty_is_safe(app):
    with app.app_context():
        answer = zia._answer_followups_today()
    assert answer.source == "local"
    assert "no follow-ups" in answer.answer.lower()
    assert answer.numbers["due_today"] == 0


def test_sales_month_empty_is_safe(app):
    with app.app_context():
        answer = zia._answer_sales_month()
    assert answer.source == "local"
    assert "0" in answer.answer
    assert answer.numbers["revenue"] == 0.0


def test_lead_sources_empty_is_safe(app):
    with app.app_context():
        answer = zia._answer_lead_sources()
    assert "no leads" in answer.answer.lower()


def test_conversion_explains_formula(app):
    with app.app_context():
        answer = zia._answer_conversion()
    assert "converted / (converted + lost)" in answer.answer
    assert "0.0%" in answer.answer or "all zero" in answer.answer


def test_revenue_answer_empty(app):
    with app.app_context():
        answer = zia._answer_revenue()
    assert answer.numbers["revenue"] == 0.0


def test_top_customers_empty(app):
    with app.app_context():
        answer = zia._answer_top_customers()
    assert "no customers" in answer.answer.lower()


def test_help_lists_capabilities(app):
    with app.app_context():
        answer = zia._answer_help()
    for cap in zia.CAPABILITIES:
        assert cap in answer.answer


# ---------------------------------------------------------------------------
# Local engine WITH data
# ---------------------------------------------------------------------------
def _seed(app):
    from app.extensions import db
    from app.models import Customer, Lead, Product, Sale, User, utcnow

    with app.app_context():
        u = User(name="Zoe Worker", email="zoe@example.com",
                 role="employee", is_active_flag=True)
        u.set_password("ZoePass123")
        db.session.add(u)
        db.session.flush()
        for i, status in enumerate(["Converted", "Lost", "New"]):
            db.session.add(Lead(name=f"ZL{i}", email=f"zl{i}@example.org",
                                source="Referral", status=status,
                                assigned_to=u.id))
        c = Customer(name="Zed Co", email="zed@example.org", created_by=u.id)
        db.session.add(c)
        db.session.flush()
        p = Product(name="ZedKit", sku="ZK-1", price=1000.0)
        db.session.add(p)
        db.session.flush()
        db.session.add(Sale(customer_id=c.id, product_id=p.id, amount=1000.0,
                            quantity=2, sold_at=utcnow(), status="Completed",
                            recorded_by=u.id))
        db.session.commit()


def test_conversion_answer_with_data(app):
    _seed(app)
    with app.app_context():
        answer = zia._answer_conversion()
    # converted=1, lost=1 -> 1/(1+1)*100 = 50%
    assert answer.numbers["conversion_rate"] == 50.0
    assert "50.0%" in answer.answer


def test_revenue_answer_with_data(app):
    _seed(app)
    with app.app_context():
        answer = zia._answer_revenue()
    assert answer.numbers["revenue"] == 2000.0


def test_lead_count_answer_with_data(app):
    _seed(app)
    with app.app_context():
        answer = zia._answer_lead_count()
    assert answer.numbers["total"] == 3


def test_source_performance_answer_with_data(app):
    _seed(app)
    with app.app_context():
        answer = zia._answer_lead_sources()
    assert "Referral" in answer.answer
    assert answer.numbers["best"] == "Referral"


# ---------------------------------------------------------------------------
# Honesty about the LLM
# ---------------------------------------------------------------------------
def test_no_llm_configured_reports_local_source(app, client, admin_client):
    """Without a key, the answer must say it came from the built-in engine."""
    resp = admin_client.post("/zia/ask", json={"question": "What is my total revenue?"})
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["source"] == "local"
    assert "built-in analytics engine" in payload["answer"]


def test_zia_status_explains_how_to_configure(admin_client, client, app):
    resp = admin_client.get("/zia/status")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["llm_configured"] is False
    assert "ZIA_LLM_API_KEY" in payload["note"]


def test_zia_page_renders(admin_client):
    resp = admin_client.get("/zia/")
    assert resp.status_code == 200
    assert b"Zia AI" in resp.data


def test_zia_requires_short_question(admin_client):
    resp = admin_client.post("/zia/ask", json={"question": "a"})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_zia_requires_login(client):
    resp = client.post("/zia/ask", json={"question": "How many leads do I have?"})
    assert resp.status_code in (302, 401)


def test_llm_failure_falls_back_honestly(app, monkeypatch, admin_client):
    """If the provider call fails, say so and still give the local answer."""
    monkeypatch.setattr(zia, "llm_configured", lambda: True)

    def broken_call(question, facts):
        return "", "Connection refused"

    monkeypatch.setattr(zia, "_call_llm", broken_call)

    resp = admin_client.post("/zia/ask", json={"question": "What is my total revenue?"})
    payload = resp.get_json()
    assert payload["source"] == "llm-fallback"
    assert "Connection refused" in payload["answer"]
    # The real number must still be present.
    assert "0.00" in payload["answer"]


def test_llm_success_is_used_when_available(app, monkeypatch, admin_client):
    monkeypatch.setattr(zia, "llm_configured", lambda: True)
    monkeypatch.setattr(zia, "_call_llm",
                        lambda q, f: ("AI-phrased answer with 0.00 revenue.", ""))

    resp = admin_client.post("/zia/ask", json={"question": "Summarize this month's sales"})
    payload = resp.get_json()
    assert payload["source"] == "llm"
    assert payload["answer"] == "AI-phrased answer with 0.00 revenue."


def test_employee_zia_scoped_to_own_data(app, employee_client, lead,
                                         other_employee_lead):
    """Zia must not reveal another employee's lead in counts."""
    resp = employee_client.post("/zia/ask",
                                json={"question": "How many leads do I have?"})
    payload = resp.get_json()
    assert payload["numbers"]["total"] == 1
    assert "Foreign Lead" not in payload["answer"]
