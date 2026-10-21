"""Initialise the DataBreakers CRM database.

Usage (PowerShell):
    python scripts/init_db.py                     # create tables only
    python scripts/init_db.py --create-admin      # prompt for admin credentials
    python scripts/init_db.py --create-admin --seed

The admin password is either prompted for interactively or read from the
ADMIN_EMAIL / ADMIN_PASSWORD environment variables. It is never hardcoded.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app          # noqa: E402
from app.extensions import db       # noqa: E402
from app.models import AuditLog, User, UserRole  # noqa: E402


def create_tables() -> None:
    with create_app().app_context():
        db.create_all()
        print("[ok] Tables created (existing tables are left untouched).")


def create_admin_interactive() -> None:
    email = (os.environ.get("ADMIN_EMAIL") or input("Admin email: ")).strip().lower()
    name = (os.environ.get("ADMIN_NAME") or input("Admin name [Administrator]: ")).strip() \
        or "Administrator"
    password = os.environ.get("ADMIN_PASSWORD") or ""
    if not password:
        password = getpass.getpass("Admin password (min 8 chars): ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("[error] Passwords do not match.")
            sys.exit(1)
    if len(password) < 8:
        print("[error] Password must be at least 8 characters.")
        sys.exit(1)
    if "@" not in email:
        print("[error] A valid email address is required.")
        sys.exit(1)

    app = create_app()
    with app.app_context():
        existing = User.query.filter(User.email == email).first()
        if existing:
            print(f"[skip] A user with {email} already exists (id={existing.id}).")
            return
        user = User(name=name, email=email, role=UserRole.ADMIN.value,
                    is_active_flag=True)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        db.session.add(AuditLog(actor="cli", action="admin_created",
                                entity="User", entity_id=user.id,
                                detail=email))
        db.session.commit()
        print(f"[ok] Admin account created: {email} (id={user.id}).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialise the CRM database.")
    parser.add_argument("--create-admin", action="store_true",
                        help="Create an administrator account after creating tables.")
    parser.add_argument("--seed", action="store_true",
                        help="Also load demo data (runs scripts/seed_demo_data.py).")
    args = parser.parse_args()

    create_tables()
    if args.create_admin:
        create_admin_interactive()
    if args.seed:
        from scripts.seed_demo_data import seed

        app = create_app()
        with app.app_context():
            seed()
    print("[done]")


if __name__ == "__main__":
    main()
