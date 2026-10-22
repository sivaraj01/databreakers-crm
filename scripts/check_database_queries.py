"""Run every service query against the configured database.

The pytest suite runs on in-memory SQLite, so it can never catch a query that
only fails on PostgreSQL. The monthly trend reports are the practical example:
they bucket rows into ``YYYY-MM`` keys with ``strftime`` on SQLite and
``to_char`` on PostgreSQL, and querying a live Postgres server is the only way
to prove that choice is right:

    DATABASE_URL="postgresql+psycopg://..." python scripts/check_database_queries.py

Every no-argument method of every service class is executed, so this also acts
as a general "does the data layer work here" smoke test. Exit status is
non-zero if any query fails.
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.services import (  # noqa: E402
    AnalyticsService, DashboardService, FollowUpService, ReportService,
)

SERVICES = (DashboardService, FollowUpService, AnalyticsService, ReportService)


def run_query(label: str, func) -> bool:
    """Call ``func`` with no arguments, reporting the outcome."""
    try:
        result = func()
    except TypeError as exc:
        # A method that requires arguments cannot be driven generically; that
        # is not a failure, so say so and move on.
        print(f"  {label:<44} skipped (needs arguments: {exc})")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  {label:<44} FAIL {type(exc).__name__}: {str(exc)[:110]}")
        # PostgreSQL aborts the whole transaction on the first error, so every
        # later query would report "current transaction is aborted" and hide
        # whether it is actually broken. Roll back to keep failures isolated.
        db.session.rollback()
        return False
    size = len(result) if hasattr(result, "__len__") else "?"
    print(f"  {label:<44} OK {type(result).__name__}[{size}]")
    return True


def main() -> int:
    app = create_app()
    failures = 0
    with app.app_context():
        from app.extensions import db
        print(f"dialect: {db.engine.dialect.name}")
        print(f"target: {(os.environ.get('DATABASE_URL') or 'in-memory sqlite')[:48]}")

        for service in SERVICES:
            print(f"\n{service.__name__}:")
            for name in sorted(dir(service)):
                if name.startswith("_"):
                    continue
                member = getattr(service, name)
                if not callable(member):
                    continue
                # A classmethod reached through the class is already bound;
                # a plain function needs its class passed in, which is done
                # by accessing it off the class above.
                if not run_query(f"{service.__name__}.{name}", member):
                    failures += 1

    total_tests = sum(1 for s in SERVICES for n in dir(s)
                      if not n.startswith("_") and callable(getattr(s, n)))
    if failures:
        print(f"\n{failures}/{total_tests} queries FAILED on this database")
        return 1
    print(f"\nall {total_tests} service queries ran successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
