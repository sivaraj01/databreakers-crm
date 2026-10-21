"""DataBreakers CRM - application factory.

Registering order matters: extensions first, then blueprints, then
context processors and error handlers.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import current_user

from config import get_config
from app.extensions import csrf, db, login_manager

APP_NAME = "DataBreakers CRM"


def create_app(config_name: str | None = None) -> Flask:
    """Build and return a configured Flask application."""
    config_class = get_config(config_name)
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)
    app.config.setdefault("APP_NAME", APP_NAME)

    # The instance folder is only needed for the default SQLite database.
    # Serverless platforms (Vercel, AWS Lambda) mount the project read-only,
    # so creating the directory there would raise PermissionError. Failures
    # are non-fatal: nothing except the SQLite fallback needs this path.
    try:
        os.makedirs(app.instance_path, exist_ok=True)
    except OSError:  # pragma: no cover - read-only deployment filesystem
        app.logger.info("Instance folder is not writable; skipping creation.")

    _register_extensions(app)
    _register_blueprints(app)
    _register_template_helpers(app)
    _register_error_handlers(app)
    _configure_logging(app)

    with app.app_context():
        _ensure_database()
        _load_persisted_settings(app)

    return app


def _load_persisted_settings(app: Flask) -> None:
    """Apply admin-saved settings (site name, page size, Zia switch).

    These live in the ``app_settings`` table so they survive restarts;
    without this step a saved value would appear on the settings page but
    have no effect after the process restarts. Missing table or corrupt
    values fall back to the config defaults instead of crashing startup.
    """
    from app.extensions import db as _db

    try:
        if not _db.inspect(_db.engine).has_table("app_settings"):
            return
        rows = _db.session.execute(
            _db.text("SELECT key, value FROM app_settings")).fetchall()
    except Exception:  # pragma: no cover - never block startup
        app.logger.warning("Could not load persisted app settings.", exc_info=True)
        return

    stored = {k: v for k, v in rows}
    name = (stored.get("site_name") or "").strip()
    if name:
        app.config["APP_NAME"] = name

    try:
        per_page = int(stored.get("leads_per_page", ""))
        if 5 <= per_page <= 100:
            app.config["LEADS_PER_PAGE"] = per_page
    except (TypeError, ValueError):
        pass

    if "zia_llm_enabled" in stored:
        app.config["ZIA_LLM_ENABLED"] = stored["zia_llm_enabled"] == "1"


def _register_extensions(app: Flask) -> None:
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)


def _register_blueprints(app: Flask) -> None:
    from app.admin import bp as admin_bp
    from app.analytics import bp as analytics_bp
    from app.assistant import bp as assistant_bp
    from app.auth import bp as auth_bp
    from app.customers import bp as customers_bp
    from app.dashboard import bp as dashboard_bp
    from app.followups import bp as followups_bp
    from app.leads import bp as leads_bp
    from app.notifications import bp as notifications_bp
    from app.predictions import bp as predictions_bp
    from app.products import bp as products_bp
    from app.reports import bp as reports_bp
    from app.sales import bp as sales_bp

    for blueprint in (auth_bp, dashboard_bp, leads_bp, customers_bp,
                      followups_bp, sales_bp, products_bp, analytics_bp,
                      predictions_bp, assistant_bp, reports_bp,
                      notifications_bp, admin_bp):
        app.register_blueprint(blueprint)

    @app.route("/health")
    def health():  # pragma: no cover - trivial
        return {"status": "ok", "app": app.config.get("APP_NAME", APP_NAME)}


def _register_template_helpers(app: Flask) -> None:
    @app.context_processor
    def inject_globals():  # noqa: ANN202
        unread = 0
        if current_user.is_authenticated:
            from app.models import Notification

            unread = Notification.query.filter_by(user_id=current_user.id,
                                                  is_read=False).count()
        return {
            "app_name": app.config.get("APP_NAME", APP_NAME),
            "current_user": current_user,
            "unread_notifications": unread,
        }

    @app.template_filter("money")
    def money(value) -> str:
        try:
            return f"{float(value or 0):,.2f}"
        except (TypeError, ValueError):  # pragma: no cover
            return "0.00"

    @app.template_filter("dt")
    def dt_format(value, fmt: str = "%Y-%m-%d %H:%M") -> str:
        return value.strftime(fmt) if hasattr(value, "strftime") else "—"

    @app.template_global()
    def nav_active(endpoint: str) -> bool:
        try:
            return request.endpoint == endpoint or \
                request.endpoint.startswith(endpoint + ".")
        except Exception:  # pragma: no cover
            return False


def _register_error_handlers(app: Flask) -> None:
    """Safe error pages - no tracebacks leak to the user in production."""

    def _render(code: int, title: str, message: str):
        status = {
            403: ("Access denied", "You do not have permission to view this page."),
            404: ("Page not found", "The page you requested does not exist."),
            413: ("File too large", "The uploaded file exceeds the size limit."),
            500: ("Something broke", "An unexpected error occurred. It has been logged."),
        }.get(code, (title, message))
        return render_template("errors/error.html", code=code,
                               title=status[0], message=status[1]), code

    @app.errorhandler(403)
    def forbidden(e):  # noqa: ANN001, ANN202
        return _render(403, "Forbidden", str(e))

    @app.errorhandler(404)
    def not_found(e):  # noqa: ANN001, ANN202
        return _render(404, "Not found", str(e))

    @app.errorhandler(413)
    def too_large(e):  # noqa: ANN001, ANN202
        return _render(413, "Too large", str(e))

    @app.errorhandler(500)
    def server_error(e):  # noqa: ANN001, ANN202
        app.logger.exception("Unhandled exception: %s", e)
        return _render(500, "Server error", "An unexpected error occurred.")

    @app.errorhandler(400)
    def bad_request(e):  # noqa: ANN001, ANN202
        flash("That request could not be processed. Please try again.", "warning")
        return redirect(request.referrer or url_for("dashboard.index"))


def _configure_logging(app: Flask) -> None:
    if app.config.get("TESTING"):
        return
    if app.logger.handlers:
        return
    app.logger.setLevel(logging.INFO)
    handler = _file_log_handler(app.instance_path)
    if handler is not None:
        app.logger.addHandler(handler)


def _file_log_handler(log_dir: str) -> RotatingFileHandler | None:
    """Build a rotating file handler, or ``None`` when the disk is read-only.

    Serverless platforms (Vercel, AWS Lambda) mount the project directory
    read-only, so there is nowhere durable to write a log file. Returning
    ``None`` there keeps the app bootable and lets the platform capture
    stdout instead.
    """
    try:
        os.makedirs(log_dir, exist_ok=True)
        handler = RotatingFileHandler(
            os.path.join(log_dir, "databreakers.log"),
            maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    except OSError:  # pragma: no cover - read-only deployment filesystem
        return None
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.setLevel(logging.INFO)
    return handler


def _ensure_database() -> None:
    """Create tables on first run if they do not exist.

    This is a convenience for development. For a controlled initialisation
    (and admin account creation) use ``python scripts/init_db.py``.
    """
    from app import models  # noqa: F401  (import registers the models)

    inspector = db.inspect(db.engine)
    if not inspector.has_table("users"):
        db.create_all()

    _ensure_app_settings_table()


def _ensure_app_settings_table() -> None:
    """Create the ``app_settings`` key/value table if it is missing.

    The table is read and written with raw SQL (see ``app.admin.routes``)
    and deliberately has no ORM model, which means ``db.create_all()`'
    will never create it. Without this step ``save_settings()`` would
    silently do nothing and admin settings would not survive a restart.
    Safe to call on every startup for both existing and fresh databases.
    """
    db.session.execute(db.text(
        "CREATE TABLE IF NOT EXISTS app_settings ("
        "key VARCHAR(64) PRIMARY KEY, "
        "value TEXT NOT NULL)"
    ))
    db.session.commit()
