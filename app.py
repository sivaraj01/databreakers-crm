"""Entry point for the DataBreakers CRM web application.

Run locally:
    python app.py

Or with the Flask CLI (recommended in production):
    set FLASK_APP=app:create_app
    flask run
"""
from __future__ import annotations

import os

from app import create_app

app = create_app(os.environ.get("FLASK_ENV", "development"))


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 5000))
    debug = app.config.get("DEBUG", False)
    print(f"\n  DataBreakers CRM running on http://{host}:{port}  (debug={debug})\n")
    app.run(host=host, port=port, debug=debug)
