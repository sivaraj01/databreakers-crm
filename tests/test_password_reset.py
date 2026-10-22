"""Self-service password reset: request a code, verify it, set a password.

Mail is captured by a fake rather than sent, so these tests exercise the whole
flow without an SMTP server - and can assert on the plaintext code, which is
the only way to check that what reaches the inbox is what the database
verifies against.
"""
from __future__ import annotations

import re

import pytest

from app import email as email_module
from app.auth import reset as reset_service
from app.extensions import db as _db
from app.models import AuditLog, PasswordResetOtp, User, utcnow

STRONG = "Sunrise42Pass"
FLASH_RE = re.compile(r'data-flash="([^"]+)"')


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def outbox(monkeypatch):
    """Capture outbound mail in place of the real SMTP transport."""
    sent: list[dict] = []

    def fake_send(to_address, code, ttl_minutes):
        sent.append({"to": to_address, "code": code, "ttl": ttl_minutes})
        return True

    monkeypatch.setattr(email_module, "send_password_reset_otp", fake_send)
    return sent


@pytest.fixture
def smtp(app, outbox):
    """Configure mail and hand back the capturing outbox."""
    watched = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM")
    before = {key: app.config.get(key) for key in watched}
    app.config.update({
        "SMTP_HOST": "smtp.example.test",
        "SMTP_PORT": 587,
        "SMTP_USER": "noreply@example.test",
        "SMTP_PASSWORD": "placeholder-not-a-real-secret",
        "MAIL_FROM": "noreply@example.test",
    })
    yield outbox
    app.config.update(before)


def flashes(response) -> list[str]:
    return FLASH_RE.findall(response.get_data(as_text=True))


def request_code(client, email: str):
    return client.post("/auth/forgot-password", data={"email": email},
                       follow_redirects=True)


def verify(client, code: str):
    return client.post("/auth/verify-otp", data={"code": code},
                       follow_redirects=True)


def set_password(client, new: str, confirm: str | None = None):
    return client.post("/auth/reset-password",
                       data={"new_password": new, "confirm_password": confirm or new},
                       follow_redirects=True)


def wrong_code_of(code: str) -> str:
    """A 6-digit string that is guaranteed to differ from ``code``."""
    return str((int(code) + 1) % 1_000_000).zfill(6)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
def test_forgot_password_page_renders(client):
    resp = client.get("/auth/forgot-password")
    assert resp.status_code == 200
    assert "Reset your password" in resp.get_data(as_text=True)


def test_login_page_offers_the_reset_link(client):
    resp = client.get("/auth/login")
    assert "/auth/forgot-password" in resp.get_data(as_text=True)


def test_smtp_missing_is_reported_honestly(client, app, outbox):
    """No SMTP configured is a deployment fact, so it may be stated plainly."""
    app.config["SMTP_HOST"] = ""
    resp = request_code(client, "admin@example.com")
    body = resp.get_data(as_text=True)
    assert "not configured" in body
    assert outbox == []
    # It must not walk the caller into a code screen that can never succeed.
    assert "/auth/verify-otp" not in body


# ---------------------------------------------------------------------------
# Issuing a code
# ---------------------------------------------------------------------------
def test_existing_account_gets_a_six_digit_code(client, admin, smtp):
    resp = request_code(client, "Admin@Example.com")   # case-insensitive lookup
    assert len(smtp) == 1
    assert smtp[0]["to"] == "admin@example.com"
    assert re.fullmatch(r"\d{6}", smtp[0]["code"])
    assert "code is on its way" in resp.get_data(as_text=True)


def test_unknown_address_is_indistinguishable_from_a_real_one(client, app, admin, smtp):
    # Separate clients on purpose: the cooldown lives in the session, so the
    # comparison only means anything from a browser that has not asked before.
    known = app.test_client().post("/auth/forgot-password",
                                   data={"email": "admin@example.com"},
                                   follow_redirects=True)
    unknown = app.test_client().post("/auth/forgot-password",
                                     data={"email": "nobody@example.com"},
                                     follow_redirects=True)

    # Identical wording, and no sentence that would betray which one exists.
    assert flashes(known) == flashes(unknown)
    assert not any("no account" in m or "not found" in m.lower()
                   for m in flashes(unknown))
    assert len(smtp) == 1     # only the real address produced a code


def test_code_is_stored_hashed_never_in_plaintext(client, admin, app, smtp):
    request_code(client, "admin@example.com")
    with app.app_context():
        otp = PasswordResetOtp.query.order_by(PasswordResetOtp.id.desc()).first()
        assert otp is not None
        assert otp.code_hash != smtp[0]["code"]
        assert smtp[0]["code"] not in otp.code_hash


def test_deactivated_account_is_not_emailed(client, inactive_user, smtp):
    resp = request_code(client, "dan@example.com")
    assert smtp == []
    assert "code is on its way" in resp.get_data(as_text=True)


def test_resend_is_throttled_by_the_session(client, admin, smtp, app):
    request_code(client, "admin@example.com")
    resp = request_code(client, "admin@example.com")
    assert "seconds" in resp.get_data(as_text=True)
    assert len(smtp) == 1     # nothing went out a second time


def test_a_new_code_invalidates_the_previous_one(client, admin, smtp, app):
    """Only one code may ever be live, or an old email stays a valid key."""
    app.config["PASSWORD_RESET_COOLDOWN_SECONDS"] = 0

    request_code(client, "admin@example.com")
    first = smtp[0]["code"]
    request_code(client, "admin@example.com")
    second = smtp[1]["code"]
    assert first != second

    stale = verify(client, first)
    assert "invalid or has expired" in stale.get_data(as_text=True)

    current = verify(client, second)
    assert "Choose a new password" in current.get_data(as_text=True)

    with app.app_context():
        otp_rows = PasswordResetOtp.query.order_by(PasswordResetOtp.id.asc()).all()
        assert otp_rows[0].is_consumed     # superseded when the new one was issued


# ---------------------------------------------------------------------------
# Verifying a code
# ---------------------------------------------------------------------------
def test_direct_visit_to_the_code_screen_redirects(client):
    resp = client.get("/auth/verify-otp", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/forgot-password" in resp.headers["Location"]


def test_correct_code_advances_to_the_password_step(client, admin, smtp):
    request_code(client, "admin@example.com")
    resp = verify(client, smtp[0]["code"])
    assert resp.status_code == 200
    assert "Choose a new password" in resp.get_data(as_text=True)


def test_wrong_code_is_rejected_with_generic_wording(client, admin, smtp):
    request_code(client, "admin@example.com")
    resp = verify(client, wrong_code_of(smtp[0]["code"]))
    assert "invalid or has expired" in resp.get_data(as_text=True)
    assert "admin@example.com" not in " ".join(flashes(resp))


def test_expired_code_is_rejected(client, admin, app, smtp):
    request_code(client, "admin@example.com")
    with app.app_context():
        otp = PasswordResetOtp.query.order_by(PasswordResetOtp.id.desc()).first()
        otp.expires_at = utcnow().replace(year=utcnow().year - 1)
        _db.session.commit()

    resp = verify(client, smtp[0]["code"])
    assert "invalid or has expired" in resp.get_data(as_text=True)


def test_attempt_budget_stops_guessing(client, admin, smtp, app):
    app.config["PASSWORD_RESET_MAX_ATTEMPTS"] = 2
    request_code(client, "admin@example.com")
    code = smtp[0]["code"]

    verify(client, wrong_code_of(code))
    verify(client, wrong_code_of(code))

    # Budget is spent: even the right code is refused now.
    resp = verify(client, code)
    assert "invalid or has expired" in resp.get_data(as_text=True)


def test_the_final_allowed_attempt_may_still_be_correct(client, admin, smtp, app):
    """The limit counts attempts, it must not silently eat the last one."""
    app.config["PASSWORD_RESET_MAX_ATTEMPTS"] = 3
    request_code(client, "admin@example.com")
    code = smtp[0]["code"]

    verify(client, wrong_code_of(code))
    verify(client, wrong_code_of(code))
    resp = verify(client, code)
    assert "Choose a new password" in resp.get_data(as_text=True)


def test_a_code_can_only_be_used_once(app, admin, smtp):
    with app.app_context():
        user = User.query.filter_by(email="admin@example.com").one()
        code, _ = reset_service.issue_otp(user)
        assert reset_service.verify_otp(user, code) == reset_service.OK
        assert reset_service.verify_otp(user, code) != reset_service.OK


# ---------------------------------------------------------------------------
# Completing the reset
# ---------------------------------------------------------------------------
def test_password_step_requires_a_verified_code(client, admin):
    resp = client.get("/auth/reset-password", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/forgot-password" in resp.headers["Location"]


def test_full_reset_changes_the_password(client, admin, smtp, app):
    request_code(client, "admin@example.com")
    verify(client, smtp[0]["code"])
    resp = set_password(client, STRONG)
    assert "Sign in with your new password" in resp.get_data(as_text=True)

    from tests.conftest import login
    old = login(client, "admin@example.com", "AdminPass123")
    assert "Invalid email or password" in old.get_data(as_text=True)

    new = login(client, "admin@example.com", STRONG)
    assert "Invalid email or password" not in new.get_data(as_text=True)


def test_completed_reset_cannot_be_replayed(client, admin, smtp):
    request_code(client, "admin@example.com")
    verify(client, smtp[0]["code"])
    set_password(client, STRONG)

    resp = client.get("/auth/reset-password", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/forgot-password" in resp.headers["Location"]


def test_weak_password_is_refused_and_password_unchanged(client, admin, smtp):
    request_code(client, "admin@example.com")
    verify(client, smtp[0]["code"])
    resp = set_password(client, "alllowercase")

    assert "needs" in resp.get_data(as_text=True)
    with_client_check = client.post("/auth/login",
                                    data={"email": "admin@example.com",
                                          "password": "alllowercase"},
                                    follow_redirects=True)
    assert "Invalid email or password" in with_client_check.get_data(as_text=True)


def test_success_is_recorded_in_the_audit_log(client, admin, app, smtp):
    request_code(client, "admin@example.com")
    verify(client, smtp[0]["code"])
    set_password(client, STRONG)

    with app.app_context():
        actions = [row.action for row in AuditLog.query.all()]
    assert "password_reset_code_sent" in actions
    assert "password_reset_completed" in actions


def test_signed_in_users_are_pointed_at_the_change_password_page(admin_client):
    resp = admin_client.get("/auth/forgot-password", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/change-password" in resp.headers["Location"]
