"""Application configuration for DataBreakers CRM.

All secrets are read from environment variables (optionally loaded from a
`.env` file). No secret is ever hardcoded in this repository.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"

# Load .env from the project root if it exists (never fails if missing).
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _database_url(raw: str) -> str:
    """Normalise a ``DATABASE_URL`` into something SQLAlchemy accepts.

    Hosted Postgres providers still hand out the pre-2011 ``postgres://``
    scheme in places, which SQLAlchemy 2.x rejects outright. Rewrite it to
    ``postgresql://``. An explicit driver (``postgresql+psycopg://``) is left
    untouched so a deployment can pin the driver it actually ships.
    """
    url = (raw or "").strip()
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


class Config:
    """Base configuration shared by every environment."""

    # --- Core ---
    SECRET_KEY = os.environ.get("SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY")
    if not SECRET_KEY:
        # Development fallback. Production must set SECRET_KEY explicitly.
        SECRET_KEY = "dev-only-insecure-key-change-me"

    TESTING = False
    DEBUG = False
    ENV = os.environ.get("FLASK_ENV", "production")

    # --- Database ---
    SQLALCHEMY_DATABASE_URI = _database_url(
        os.environ.get("DATABASE_URL", f"sqlite:///{INSTANCE_DIR / 'databreakers_crm.db'}")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # --- Session / cookie security ---
    SESSION_COOKIE_HTTPONLY = _bool("SESSION_COOKIE_HTTPONLY", True)
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = _bool("REMEMBER_COOKIE_SECURE", False)
    REMEMBER_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = int(os.environ.get("SESSION_LIFETIME_HOURS", 8)) * 3600
    WTF_CSRF_TIME_LIMIT = int(os.environ.get("WTF_CSRF_TIME_LIMIT", 3600))

    # --- Misc ---
    PREFERRED_URL_SCHEME = os.environ.get("PREFERRED_URL_SCHEME", "http")
    JSON_SORT_KEYS = False
    MAX_CONTENT_LENGTH = 4 * 1024 * 1024  # 4 MB uploads
    SEND_FILE_MAX_AGE_DEFAULT = 0 if ENV == "development" else 3600

    # --- Zia AI (server side only, never sent to the browser) ---
    ZIA_LLM_API_KEY = os.environ.get("ZIA_LLM_API_KEY", "").strip()
    ZIA_LLM_BASE_URL = os.environ.get("ZIA_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    ZIA_LLM_MODEL = os.environ.get("ZIA_LLM_MODEL", "gpt-4o-mini")
    ZIA_LLM_TIMEOUT_SECONDS = int(os.environ.get("ZIA_LLM_TIMEOUT_SECONDS", 30))

    # --- Mail (SMTP delivery for the password-reset OTP) ---
    # Every value comes from the environment; nothing is hardcoded and nothing
    # here ever reaches the browser. When SMTP_HOST is unset the mailer reports
    # itself as unconfigured instead of failing the request, so an instance
    # without mail still runs.
    SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER", "").strip()
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    MAIL_FROM = os.environ.get("MAIL_FROM", "").strip() or SMTP_USER
    MAIL_TIMEOUT = int(os.environ.get("MAIL_TIMEOUT", "10"))
    MAIL_USE_SSL = _bool("MAIL_USE_SSL", SMTP_PORT == 465)

    # --- Password reset (email OTP) ---
    PASSWORD_RESET_OTP_TTL_MINUTES = int(os.environ.get("PASSWORD_RESET_OTP_TTL_MINUTES", "10"))
    PASSWORD_RESET_MAX_ATTEMPTS = int(os.environ.get("PASSWORD_RESET_MAX_ATTEMPTS", "5"))
    PASSWORD_RESET_COOLDOWN_SECONDS = int(os.environ.get("PASSWORD_RESET_COOLDOWN_SECONDS", "60"))
    PASSWORD_RESET_WINDOW_MINUTES = int(os.environ.get("PASSWORD_RESET_WINDOW_MINUTES", "15"))

    # --- Pagination defaults ---
    LEADS_PER_PAGE = 12
    CUSTOMERS_PER_PAGE = 12
    SALES_PER_PAGE = 15


class DevelopmentConfig(Config):
    DEBUG = True
    ENV = "development"


class TestingConfig(Config):
    TESTING = True
    DEBUG = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    PRESERVE_CONTEXT_ON_EXCEPTION = False


class ProductionConfig(Config):
    ENV = "production"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", True)
    REMEMBER_COOKIE_SECURE = _bool("REMEMBER_COOKIE_SECURE", True)

    def __init__(self) -> None:  # pragma: no cover - guarded startup check
        if Config.SECRET_KEY.startswith("dev-only"):
            raise RuntimeError(
                "SECRET_KEY is not configured. Set the SECRET_KEY environment "
                "variable before running in production."
            )


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}


def get_config(name: str | None = None) -> type[Config]:
    """Resolve a configuration class from an environment name."""
    key = (name or os.environ.get("FLASK_ENV") or "development").lower()
    return CONFIG_MAP.get(key, DevelopmentConfig)
