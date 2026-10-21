"""Seed the database with clearly-labelled DEMO data.

Everything created here is synthetic and marked as demo. Run it any number of
times; it refuses to run twice so your real data is never overwritten.

Usage:
    python scripts/seed_demo_data.py
    python scripts/seed_demo_data.py --employees 3 --months 8
"""
from __future__ import annotations

import argparse
import os
import random
import secrets
import sys
import string
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app                     # noqa: E402
from app.extensions import db                  # noqa: E402
from app.models import (Customer, CustomerInteraction, FollowUp, Lead,
                        LeadStatus, Notification, Product, Sale, SaleStatus,
                        User, UserRole, utcnow)  # noqa: E402

FIRST_NAMES = ["Aarav", "Priya", "Karthik", "Meera", "Rahul", "Ananya",
               "Vikram", "Divya", "Sanjay", "Nisha", "Arjun", "Kavya",
               "Rohan", "Ishita", "Aditya", "Pooja", "Kiran", "Lakshmi"]
LAST_NAMES = ["Sharma", "Iyer", "Nair", "Reddy", "Kapoor", "Menon",
              "Chatterjee", "Patel", "Gupta", "Rao", "Joshi", "Verma"]
COMPANIES = ["Nexora Systems", "Brightline Labs", "Orbit Retail", "Helix Health",
             "Zenith Logistics", "Quantum Foods", "Aster Media", "Cobalt Energy",
             "Pinnacle Consulting", "Verdant Agro", "Lumen Telecom", "Ridge Capital"]
CITIES = ["Chennai", "Bengaluru", "Mumbai", "Hyderabad", "Pune", "Delhi",
          "Kochi", "Coimbatore", "Jaipur", "Indore"]
INDUSTRIES = ["Manufacturing", "Healthcare", "Retail", "Finance", "Education",
              "Logistics", "IT Services", "Energy"]
PRODUCTS = [
    ("Analytics Suite", "DB-ANL", "Software", 4500.0,
     "Dashboards and reporting for mid-size teams."),
    ("Data Pipeline Pro", "DB-PIPE", "Software", 7800.0,
     "Managed ETL and streaming ingestion."),
    ("CRM Add-on", "DB-CRM", "Software", 2400.0,
     "Lead scoring and pipeline automation."),
    ("Training Workshop", "DB-TRN", "Service", 1500.0,
     "Two-day on-site analytics workshop."),
    ("Support Retainer", "DB-SUP", "Service", 900.0,
     "Priority support, billed monthly."),
    ("Custom Integration", "DB-INT", "Service", 6200.0,
     "Bespoke connectors to ERP and billing."),
]


def _rand_email(name: str, used: set[str]) -> str:
    base = "".join(c for c in name.lower().replace(" ", ".") if c.isalpha() or c == ".")
    while True:
        candidate = f"{base}{random.randint(1, 9999)}@demo.example"
        if candidate not in used:
            used.add(candidate)
            return candidate


def seed(employees: int = 3, months: int = 8, leads_per_month: int = 12) -> dict:
    """Populate the database with demo records. Returns a summary dict."""
    if Lead.query.count() or Customer.query.count() or User.query.count() > 1:
        print("[skip] The database already contains data. Demo seeding aborted "
              "so nothing is overwritten. Use a fresh database to re-seed.")
        return {"skipped": True}

    rng = random.Random(20260101)
    now = utcnow()

    # --- demo users -------------------------------------------------------
    # Passwords are NEVER hardcoded: use DEMO_PASSWORD if provided, otherwise
    # generate a strong random one and print it once for this run.
    demo_password = os.environ.get("DEMO_PASSWORD", "").strip()
    generated = False
    if not demo_password:
        demo_password = "Db-" + secrets.token_urlsafe(11)
        generated = True
    admin = User(name="Demo Administrator", email="admin@demo.example",
                 role=UserRole.ADMIN.value, is_active_flag=True)
    admin.set_password(demo_password)
    db.session.add(admin)

    staff = []
    for i in range(employees):
        name = f"{FIRST_NAMES[i % len(FIRST_NAMES)]} {LAST_NAMES[(i + 3) % len(LAST_NAMES)]}"
        u = User(name=name, email=f"employee{i + 1}@demo.example",
                 role=UserRole.EMPLOYEE.value, is_active_flag=True)
        u.set_password(demo_password)
        db.session.add(u)
        staff.append(u)
    db.session.flush()

    # --- products ---------------------------------------------------------
    products = [Product(name=n, sku=sku, category=cat, price=price,
                        description=desc, is_active=True)
                for n, sku, cat, price, desc in PRODUCTS]
    db.session.add_all(products)
    db.session.flush()

    used_emails: set[str] = set()
    statuses = [LeadStatus.NEW.value, LeadStatus.CONTACTED.value,
                LeadStatus.INTERESTED.value, LeadStatus.QUALIFIED.value,
                LeadStatus.CONVERTED.value, LeadStatus.LOST.value]
    weights = [22, 20, 16, 12, 18, 12]
    sources = ["Website", "Referral", "Cold Call", "Social Media",
               "Trade Show", "Email Campaign", "Partner", "Walk-in"]

    all_leads: list[Lead] = []
    customers: list[Customer] = []

    for m in range(months):
        month_start = (now - timedelta(days=30 * m)).replace(day=1, hour=0,
                                                             minute=0, second=0,
                                                             microsecond=0)
        for _ in range(leads_per_month):
            name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
            created = month_start + timedelta(
                days=rng.randint(0, 27), hours=rng.randint(9, 18),
                minutes=rng.randint(0, 59))
            if created > now:
                created = now - timedelta(days=rng.randint(0, 20))
            status = rng.choices(statuses, weights=weights, k=1)[0]
            assignee = rng.choice(staff)
            lead = Lead(
                name=name, email=_rand_email(name, used_emails),
                phone=f"+91 9{rng.randint(100000000, 999999999)}" if rng.random() > .3 else None,
                company=rng.choice(COMPANIES) if rng.random() > .35 else None,
                source=rng.choice(sources),
                product_interest=rng.choice(products).name if rng.random() > .3 else None,
                expected_value=round(rng.uniform(800, 24000), 2),
                status=status, assigned_to=assignee.id,
                created_at=created,
                last_contact_at=created + timedelta(days=rng.randint(0, 10)),
                converted_at=(created + timedelta(days=rng.randint(3, 25))
                              if status == LeadStatus.CONVERTED.value else None),
                notes=(f"[DEMO] Interested in {rng.choice(products).name}. "
                       f"Budget discussed." if rng.random() > .5 else None),
            )
            if status == LeadStatus.NEW.value and rng.random() > .4:
                lead.next_followup_at = now + timedelta(
                    days=rng.randint(-3, 10), hours=rng.randint(0, 6))
            db.session.add(lead)
            all_leads.append(lead)

    db.session.flush()

    # --- customers + sales for converted leads ----------------------------
    for lead in all_leads:
        if lead.status != LeadStatus.CONVERTED.value:
            continue
        customer = Customer(
            name=lead.name, email=lead.email, phone=lead.phone,
            company=lead.company, city=rng.choice(CITIES),
            country="India", segment=rng.choice(["Standard", "Premium", "Enterprise"]),
            industry=rng.choice(INDUSTRIES), created_by=lead.assigned_to,
            created_at=lead.converted_at or lead.created_at, lead_id=lead.id)
        db.session.add(customer)
        customers.append(customer)
        db.session.flush()

        # 1-4 purchases spread over the following weeks
        for _ in range(rng.randint(1, 4)):
            product = rng.choice(products)
            sold_at = (lead.converted_at or lead.created_at) + timedelta(
                days=rng.randint(1, 60))
            if sold_at > now:
                sold_at = now - timedelta(days=rng.randint(0, 10))
            db.session.add(Sale(
                customer_id=customer.id, product_id=product.id,
                amount=product.price, quantity=rng.randint(1, 5),
                sold_at=sold_at,
                status=rng.choices([SaleStatus.COMPLETED.value,
                                    SaleStatus.PENDING.value,
                                    SaleStatus.REFUNDED.value],
                                   weights=[85, 10, 5], k=1)[0],
                reference=f"DEMO-{lead.id}-{rng.randint(100, 999)}",
                recorded_by=lead.assigned_to))

        # a couple of interactions
        for _ in range(rng.randint(1, 3)):
            db.session.add(CustomerInteraction(
                customer_id=customer.id, user_id=lead.assigned_to,
                kind=rng.choice(["Call", "Meeting", "Email", "Note"]),
                summary="[DEMO] Discussed renewal options and support tiers.",
                created_at=(lead.converted_at or lead.created_at)
                + timedelta(days=rng.randint(1, 30))))

    # --- follow-ups --------------------------------------------------------
    open_leads = [l for l in all_leads if l.status != LeadStatus.LOST.value]
    for lead in rng.sample(open_leads, min(len(open_leads), 40)):
        offset_days = rng.randint(-5, 14)
        db.session.add(FollowUp(
            lead_id=lead.id, assigned_to=lead.assigned_to,
            created_by=lead.assigned_to,
            due_at=now + timedelta(days=offset_days, hours=rng.randint(9, 18)),
            kind=rng.choice(["Call", "Meeting", "Email", "Demo"]),
            notes="[DEMO] Follow up on proposal and pricing.",
            status=("Completed" if offset_days < -2 and rng.random() > .5
                    else "Pending")))

    # --- notifications for demo staff --------------------------------------
    for u in staff:
        db.session.add(Notification(
            user_id=u.id, title="Welcome to DataBreakers CRM",
            body="[DEMO] This account was created by the demo data seeder.",
            kind="info"))

    db.session.commit()

    summary = {
        "users": User.query.count(),
        "products": Product.query.count(),
        "leads": Lead.query.count(),
        "customers": Customer.query.count(),
        "sales": Sale.query.count(),
        "followups": FollowUp.query.count(),
    }
    print("[ok] Demo data seeded:")
    for k, v in summary.items():
        print(f"     {k:<11} {v}")
    print(f"\n[info] Demo sign-in: admin@demo.example / {demo_password}")
    if generated:
        print("[info] A strong random password was generated for this run "
              "(set DEMO_PASSWORD to choose your own). Save it now - it is "
              "shown only once.")
    print("[info] Every seeded record is synthetic (demo.example domain, "
          "[DEMO] notes). Replace or delete before real use.")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed DEMO data.")
    parser.add_argument("--employees", type=int, default=3)
    parser.add_argument("--months", type=int, default=8)
    parser.add_argument("--leads-per-month", type=int, default=12)
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        db.create_all()
        seed(employees=args.employees, months=args.months,
             leads_per_month=args.leads_per_month)


if __name__ == "__main__":
    main()
