"""Self-service password reset: issuing and checking one-time email codes.

The rules this module exists to enforce:

* the code is 6 digits from :mod:`secrets`, not ``random``;
* only its hash is persisted, so the database never holds a usable code;
* one outstanding code per account - requesting again invalidates the old one;
* a cooldown stops a forgotten address being used to flood an inbox;
* an attempt budget stops the code being brute-forced (1e6 possibilities is
  small enough that unlimited guesses would be a real risk);
* one code, one use: it is consumed the moment it verifies, and the *session*
  carries the proof to the password-change step.
"""
from __future__ import annotations

import secrets
from datetime import timedelta

from flask import current_app

from app.extensions import db
from app.models import PasswordResetOtp, User, utcnow

# Result codes shared by the routes so every user-facing message is derived
# from one place and none of them can accidentally leak whether an account
# exists.
OK = "ok"
NO_CODE = "no_code"
WRONG_CODE = "wrong_code"
EXPIRED = "expired"
EXHAUSTED = "exhausted"
COOLDOWN = "cooldown"

CODE_LENGTH = 6
CODE_SPACE = 10 ** CODE_LENGTH


def generate_code() -> str:
    """A 6-digit code with no leading-zero ambiguity (100000-999999).

    :func:`secrets.randbelow` is the CSPRNG-backed primitive; ``random`` would
    be predictable from its seed, and a predictable reset code is account
    takeover.
    """
    return str(secrets.randbelow(CODE_SPACE - 100_000) + 100_000)


def mask_email(address: str) -> str:
    """Show just enough of an address to recognise it, not enough to harvest."""
    local, _, domain = (address or "").partition("@")
    if not domain:
        return "***"
    head = local[:2] if len(local) > 2 else local[:1]
    return f"{head}***@{domain}"


def latest_code(user_id: int) -> PasswordResetOtp | None:
    """The most recent still-live code for an account, if any."""
    return (PasswordResetOtp.query
            .filter(PasswordResetOtp.user_id == user_id,
                    PasswordResetOtp.consumed_at.is_(None))
            .order_by(PasswordResetOtp.created_at.desc())
            .first())


def cooldown_remaining(otp: PasswordResetOtp | None) -> int:
    """Seconds until another code may be issued, or 0 when there is no wait."""
    if otp is None or otp.is_expired:
        return 0
    window = int(current_app.config.get("PASSWORD_RESET_COOLDOWN_SECONDS", 60))
    elapsed = (utcnow() - otp.created_at).total_seconds()
    return max(0, int(window - elapsed))


def issue_otp(user: User) -> tuple[str | None, int]:
    """Replace any outstanding code with a fresh one.

    Returns ``(code, 0)`` when a new code was created - the caller emails the
    plaintext immediately - or ``(None, seconds)`` when a code is still inside
    its cooldown window. Every other outstanding code is invalidated first, so
    an old email can never be replayed against a newer request.
    """
    previous = latest_code(user.id)
    wait = cooldown_remaining(previous)
    if wait:
        return None, wait

    if previous is not None:
        # Inside its cooldown-free window it is still live: burn it so exactly
        # one code can be valid at a time.
        previous.consumed_at = utcnow()
        db.session.flush()

    ttl = int(current_app.config.get("PASSWORD_RESET_OTP_TTL_MINUTES", 10))
    code = generate_code()
    otp = PasswordResetOtp(
        user_id=user.id,
        code_hash=_hash(code),
        expires_at=utcnow() + timedelta(minutes=ttl),
        attempts=0,
    )
    db.session.add(otp)
    db.session.commit()
    return code, 0


def verify_otp(user: User, code: str) -> str:
    """Check ``code`` and consume it on success.

    Budget is checked *before* incrementing so the configured limit is exactly
    the number of guesses allowed - the final one may still be correct - and
    reported as exhausted only when that last guess is wrong. Every attempt
    counts, right or wrong, so the window for guessing is bounded rather than
    merely slow.
    """
    otp = latest_code(user.id)
    if otp is None:
        return NO_CODE
    if otp.is_consumed:
        return EXPIRED
    if otp.is_exhausted:
        return EXHAUSTED

    otp.attempts += 1
    db.session.flush()

    if otp.is_expired:
        db.session.commit()
        return EXPIRED
    if otp.matches(code or ""):
        # Consumed here, not at the password-change step: a code that has been
        # read out once can never be replayed, and the session carries the
        # proof forward instead.
        otp.consumed_at = utcnow()
        db.session.commit()
        return OK

    db.session.commit()
    return EXHAUSTED if otp.is_exhausted else WRONG_CODE


def _hash(code: str) -> str:
    from werkzeug.security import generate_password_hash

    return generate_password_hash(code, method="pbkdf2:sha256")
