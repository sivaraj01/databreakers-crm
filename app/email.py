"""SMTP delivery for DataBreakers CRM.

Outbound mail exists for one purpose: proving ownership of an inbox during a
self-service password reset. The transport is plain SMTP from the standard
library, so there is no third-party SDK and no API key - the server, port,
user and password are read from the environment (``SMTP_*`` in ``config.py``)
and are never sent to the browser.

Everything here fails soft. A serverless request must not hang or 500 because
a mail provider is slow or unconfigured, so every send is bounded by a socket
timeout and returns ``False`` instead of raising. Callers treat ``False`` as
"mail could not be delivered" and say so honestly rather than pretending a
code was sent.
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from flask import current_app

__all__ = ["smtp_configured", "send_password_reset_otp"]


def smtp_configured() -> bool:
    """True when there is enough configuration to attempt a delivery."""
    return bool(current_app.config.get("SMTP_HOST"))


def _recipient_is_set(address: str) -> bool:
    return bool((address or "").strip())


def send_message(to_address: str, subject: str, body: str) -> bool:
    """Deliver one plain-text message. Returns ``False`` on any failure.

    Deliberately plain text: this carries a six-digit code, and a multipart
    HTML message would only add an attack surface (and a much larger body to
    render inside a request).
    """
    to_address = (to_address or "").strip()
    if not _recipient_is_set(to_address):
        current_app.logger.warning("mail skipped: no recipient address")
        return False
    if not smtp_configured():
        current_app.logger.warning(
            "mail skipped: SMTP_HOST is not set, so outbound mail is disabled"
        )
        return False

    config = current_app.config
    host = config["SMTP_HOST"]
    port = int(config.get("SMTP_PORT", 587))
    user = (config.get("SMTP_USER") or "").strip()
    password = config.get("SMTP_PASSWORD") or ""
    sender = (config.get("MAIL_FROM") or user or "").strip() or to_address
    timeout = float(config.get("MAIL_TIMEOUT", 10))
    use_ssl = bool(config.get("MAIL_USE_SSL", port == 465))

    message = EmailMessage()
    message["From"] = sender
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)

    try:
        context = ssl.create_default_context()
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) as client:
                if user:
                    client.login(user, password)
                client.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as client:
                client.ehlo()
                client.starttls(context=context)
                client.ehlo()
                if user:
                    client.login(user, password)
                client.send_message(message)
    except Exception as exc:  # noqa: BLE001 - any transport failure must stay soft
        # Log the type and the reason only: the reason can quote a server
        # banner, and logging credentials - or a full traceback - is never OK.
        current_app.logger.warning(
            "mail delivery to %s failed: %s: %s",
            _redact(to_address), type(exc).__name__, str(exc)[:200],
        )
        return False

    current_app.logger.info("mail delivered to %s (host=%s port=%s)",
                            _redact(to_address), host, port)
    return True


def _redact(address: str) -> str:
    """Partially mask an address for a log line.

    Enough to tell two recipients apart, not enough to harvest addresses out of
    a log file.
    """
    local, _, domain = address.partition("@")
    if not domain:
        return "***"
    head = local[:2] if len(local) > 2 else local[:1]
    return f"{head}***@{domain}"


def send_password_reset_otp(to_address: str, code: str, ttl_minutes: int) -> bool:
    """Email a six-digit reset code with its expiry stated in plain words."""
    body = (
        "DataBreakers CRM - password reset\n"
        "\n"
        f"Your verification code is: {code}\n"
        "\n"
        f"The code expires in {ttl_minutes} minutes and can only be used once.\n"
        "\n"
        "If you did not ask to reset your password you can ignore this email - "
        "your password has not changed and nobody can reset it without this "
        "code.\n"
        "\n"
        "Never share this code. DataBreakers staff will never ask for it.\n"
    )
    return send_message(
        to_address,
        subject=f"{code} is your DataBreakers CRM reset code",
        body=body,
    )
