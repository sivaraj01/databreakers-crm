"""Vercel serverless entry point.

Vercel's Python runtime looks for a WSGI application object in a file under
``api/``. Everything else (app factory, blueprints, templates, services) is
reused unchanged, so the exact same code runs locally, under gunicorn/Docker
and on Vercel.

The database must be a real server (Neon/Postgres) because a serverless
function has an ephemeral, read-only filesystem: set ``DATABASE_URL``.
"""
from __future__ import annotations

import os
import sys

# ``api/`` lives next to the project root, so put the project root on
# ``sys.path`` to make the ``app`` package and ``config`` module importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# A deployed instance must use the production configuration: it refuses to
# start without a real SECRET_KEY and marks session cookies Secure. This is
# set *before* importing ``config``, because python-dotenv never overrides an
# existing environment variable but would otherwise inject a local
# ``FLASK_ENV=development`` from a developer's ``.env`` file.
os.environ.setdefault("FLASK_ENV", "production")

from app import create_app  # noqa: E402  (must follow the sys.path setup)

app = create_app()
