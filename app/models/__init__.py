"""SQLAlchemy models for DataBreakers CRM.

Nine domain models with relationships, timestamps and constraints:

    User, Lead, Customer, Product, Sale, FollowUp, Activity, Notification, AuditLog
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager


def utcnow() -> datetime:
    """Timezone-naive UTC timestamp (SQLite friendly)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
class LeadStatus(str, enum.Enum):
    NEW = "New"
    CONTACTED = "Contacted"
    INTERESTED = "Interested"
    QUALIFIED = "Qualified"
    CONVERTED = "Converted"
    LOST = "Lost"


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    EMPLOYEE = "employee"


class FollowUpStatus(str, enum.Enum):
    PENDING = "Pending"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"


class SaleStatus(str, enum.Enum):
    COMPLETED = "Completed"
    PENDING = "Pending"
    REFUNDED = "Refunded"


# Association helpers -------------------------------------------------------
lead_sources = [
    "Website", "Referral", "Cold Call", "Social Media",
    "Trade Show", "Email Campaign", "Walk-in", "Partner", "Other",
]

lead_statuses = [s.value for s in LeadStatus]
sale_statuses = [s.value for s in SaleStatus]
followup_types = ["Call", "Meeting", "Email", "Demo", "Site Visit"]


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    updated_at = db.Column(
        db.DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------
class User(UserMixin, db.Model, TimestampMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=UserRole.EMPLOYEE.value, index=True)
    is_active_flag = db.Column(db.Boolean, default=True, nullable=False)
    last_login_at = db.Column(db.DateTime)
    designations = db.Column(db.String(120))

    leads = db.relationship("Lead", back_populates="assignee", lazy="dynamic",
                            foreign_keys="Lead.assigned_to")
    assigned_followups = db.relationship("FollowUp", back_populates="assignee",
                                         lazy="dynamic", foreign_keys="FollowUp.assigned_to")

    # --- password helpers (plaintext is never stored) ---
    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(raw_password, method="pbkdf2:sha256")

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN.value

    # flask-login expects `is_active`; the DB column is named is_active_flag
    @property
    def is_active(self) -> bool:  # noqa: F811
        return bool(self.is_active_flag)

    @property
    def initials(self) -> str:
        parts = [p for p in (self.name or "").split() if p]
        return "".join(p[0].upper() for p in parts[:2]) or "U"

    @property
    def role_label(self) -> str:
        return "Administrator" if self.is_admin else "Sales Employee"

    def __str__(self) -> str:
        return self.name or self.email


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    return db.session.get(User, int(user_id))


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------
class Product(db.Model, TimestampMixin):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), unique=True, nullable=False, index=True)
    sku = db.Column(db.String(60), unique=True)
    category = db.Column(db.String(80), default="General", nullable=False)
    price = db.Column(db.Float, nullable=False, default=0.0)
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    sales = db.relationship("Sale", back_populates="product", lazy="dynamic")

    @property
    def units_sold(self) -> int:
        return self.sales.filter_by(status=SaleStatus.COMPLETED.value).count()

    @property
    def revenue(self) -> float:
        rows = self.sales.filter_by(status=SaleStatus.COMPLETED.value).all()
        return float(sum(r.quantity * r.amount for r in rows))

    def __str__(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# Lead
# ---------------------------------------------------------------------------
class Lead(db.Model, TimestampMixin):
    __tablename__ = "leads"
    __table_args__ = (
        db.UniqueConstraint("email", "created_at", name="uq_lead_email_created"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False, index=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    phone = db.Column(db.String(40))
    company = db.Column(db.String(160))
    source = db.Column(db.String(60), default="Website", nullable=False, index=True)
    product_interest = db.Column(db.String(160))
    expected_value = db.Column(db.Float, default=0.0, nullable=False)
    status = db.Column(db.String(20), default=LeadStatus.NEW.value,
                       nullable=False, index=True)
    notes = db.Column(db.Text)
    last_contact_at = db.Column(db.DateTime)
    next_followup_at = db.Column(db.DateTime, index=True)
    converted_at = db.Column(db.DateTime)
    lost_reason = db.Column(db.String(255))

    assigned_to = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    # use_alter=True breaks the leads <-> customers creation/drop cycle so the
    # metadata can be sorted (required for SQLite).
    customer_id = db.Column(db.Integer,
                            db.ForeignKey("customers.id", use_alter=True,
                                          name="fk_leads_customer_id"),
                            index=True)

    assignee = db.relationship("User", back_populates="leads",
                               foreign_keys=[assigned_to])
    # Both sides are many-to-one (lead.customer_id and customer.lead_id), so
    # they are independent relationships and cannot share a back_populates.
    customer = db.relationship("Customer", uselist=False,
                               foreign_keys=[customer_id])
    followups = db.relationship("FollowUp", back_populates="lead",
                                cascade="all, delete-orphan", lazy="dynamic")
    activities = db.relationship("Activity", backref="lead", lazy="dynamic")

    CONVERTED_STATUSES = (LeadStatus.CONVERTED.value,)
    OPEN_STATUSES = (
        LeadStatus.NEW.value, LeadStatus.CONTACTED.value,
        LeadStatus.INTERESTED.value, LeadStatus.QUALIFIED.value,
    )

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN_STATUSES

    @property
    def is_overdue(self) -> bool:
        return bool(self.next_followup_at and self.next_followup_at < utcnow()
                    and self.is_open)

    @property
    def status_tone(self) -> str:
        return {
            "New": "info", "Contacted": "primary", "Interested": "secondary",
            "Qualified": "warning", "Converted": "success", "Lost": "danger",
        }.get(self.status, "secondary")

    def __str__(self) -> str:
        return f"{self.name} ({self.status})"


# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------
class Customer(db.Model, TimestampMixin):
    __tablename__ = "customers"
    __table_args__ = (
        db.UniqueConstraint("email", name="uq_customer_email"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False, index=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    phone = db.Column(db.String(40))
    company = db.Column(db.String(160))
    city = db.Column(db.String(120))
    country = db.Column(db.String(120))
    segment = db.Column(db.String(40), default="Standard", nullable=False)
    industry = db.Column(db.String(120))
    notes = db.Column(db.Text)

    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    creator = db.relationship("User", foreign_keys=[created_by])

    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"))
    # Independent many-to-one link back to the originating lead.
    lead = db.relationship("Lead", uselist=False, foreign_keys=[lead_id])
    sales = db.relationship("Sale", back_populates="customer",
                            cascade="all, delete-orphan", lazy="dynamic")
    interactions = db.relationship("CustomerInteraction", back_populates="customer",
                                   cascade="all, delete-orphan", lazy="dynamic")
    followups = db.relationship("FollowUp", back_populates="customer", lazy="dynamic")

    @property
    def total_purchases(self) -> int:
        return self.sales.filter_by(status=SaleStatus.COMPLETED.value).count()

    @property
    def lifetime_value(self) -> float:
        rows = self.sales.filter_by(status=SaleStatus.COMPLETED.value).all()
        return float(sum(r.quantity * r.amount for r in rows))

    @property
    def last_purchase_at(self) -> datetime | None:
        row = (self.sales.filter_by(status=SaleStatus.COMPLETED.value)
               .order_by(Sale.sold_at.desc()).first())
        return row.sold_at if row else None

    @property
    def is_active(self) -> bool:
        from datetime import timedelta
        last = self.last_purchase_at
        return bool(last and (utcnow() - last) <= timedelta(days=365))

    def __str__(self) -> str:
        return self.name


class CustomerInteraction(db.Model, TimestampMixin):
    """Logged touch-points with a customer (call, meeting, email, note)."""

    __tablename__ = "customer_interactions"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"),
                            nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    kind = db.Column(db.String(40), default="Note", nullable=False)
    summary = db.Column(db.Text, nullable=False)

    customer = db.relationship("Customer", back_populates="interactions")
    user = db.relationship("User")

    def __str__(self) -> str:
        return self.summary[:60]


# ---------------------------------------------------------------------------
# Sale
# ---------------------------------------------------------------------------
class Sale(db.Model, TimestampMixin):
    __tablename__ = "sales"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"),
                            nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), index=True)
    amount = db.Column(db.Float, nullable=False)          # unit amount
    quantity = db.Column(db.Integer, nullable=False, default=1)
    sold_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    status = db.Column(db.String(20), nullable=False,
                       default=SaleStatus.COMPLETED.value, index=True)
    reference = db.Column(db.String(60), unique=True)
    notes = db.Column(db.Text)
    recorded_by = db.Column(db.Integer, db.ForeignKey("users.id"))

    customer = db.relationship("Customer", back_populates="sales")
    product = db.relationship("Product", back_populates="sales")
    recorder = db.relationship("User", foreign_keys=[recorded_by])

    @property
    def total(self) -> float:
        return float(self.amount) * int(self.quantity or 1)

    @property
    def revenue(self) -> float:
        """Counted revenue - only completed sales contribute to revenue."""
        return self.total if self.status == SaleStatus.COMPLETED.value else 0.0

    def __str__(self) -> str:
        return f"{self.reference or self.id} - {self.total:,.2f}"


# ---------------------------------------------------------------------------
# FollowUp
# ---------------------------------------------------------------------------
class FollowUp(db.Model, TimestampMixin):
    __tablename__ = "followups"

    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), index=True)
    assigned_to = db.Column(db.Integer, db.ForeignKey("users.id"),
                            nullable=False, index=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    due_at = db.Column(db.DateTime, nullable=False, index=True)
    kind = db.Column(db.String(40), default="Call", nullable=False)
    notes = db.Column(db.Text)
    outcome = db.Column(db.Text)
    status = db.Column(db.String(20), default=FollowUpStatus.PENDING.value,
                       nullable=False, index=True)
    completed_at = db.Column(db.DateTime)

    lead = db.relationship("Lead", back_populates="followups")
    customer = db.relationship("Customer", back_populates="followups")
    assignee = db.relationship("User", back_populates="assigned_followups",
                               foreign_keys=[assigned_to])
    creator = db.relationship("User", foreign_keys=[created_by])

    @property
    def is_due_today(self) -> bool:
        from datetime import date
        return self.status == FollowUpStatus.PENDING.value and self.due_at.date() == date.today()

    @property
    def is_overdue(self) -> bool:
        return (self.status == FollowUpStatus.PENDING.value
                and self.due_at < utcnow())

    @property
    def due_state(self) -> str:
        """'overdue' | 'today' | 'upcoming' | 'done' | 'cancelled'"""
        if self.status == FollowUpStatus.COMPLETED.value:
            return "done"
        if self.status == FollowUpStatus.CANCELLED.value:
            return "cancelled"
        if self.is_overdue:
            return "overdue"
        if self.is_due_today:
            return "today"
        return "upcoming"

    @property
    def subject(self) -> str:
        if self.lead:
            return f"Lead: {self.lead.name}"
        if self.customer:
            return f"Customer: {self.customer.name}"
        return "General follow-up"

    def __str__(self) -> str:
        return f"{self.kind} @ {self.due_at:%Y-%m-%d %H:%M}"


# ---------------------------------------------------------------------------
# Activity (audit trail for the UI activity feed)
# ---------------------------------------------------------------------------
class Activity(db.Model):
    __tablename__ = "activities"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), index=True)
    entity_type = db.Column(db.String(40), nullable=False, index=True)
    entity_id = db.Column(db.Integer)
    action = db.Column(db.String(40), nullable=False)
    summary = db.Column(db.String(400), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    user = db.relationship("User")

    def __str__(self) -> str:
        return self.summary


# ---------------------------------------------------------------------------
# Notification (in-app only)
# ---------------------------------------------------------------------------
class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                        nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False)
    body = db.Column(db.String(500))
    kind = db.Column(db.String(40), default="info", nullable=False)
    link = db.Column(db.String(255))
    is_read = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    user = db.relationship("User", backref="notifications")

    @property
    def icon(self) -> str:
        return {
            "lead": "bi-person-plus", "followup": "bi-calendar-check",
            "overdue": "bi-exclamation-triangle", "converted": "bi-check-circle",
            "sales": "bi-currency-dollar", "info": "bi-bell",
        }.get(self.kind, "bi-bell")

    def __str__(self) -> str:
        return self.title


# ---------------------------------------------------------------------------
# AuditLog (security-relevant actions)
# ---------------------------------------------------------------------------
class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    actor = db.Column(db.String(255))          # snapshot; survives user deletion
    action = db.Column(db.String(60), nullable=False, index=True)
    entity = db.Column(db.String(60))
    entity_id = db.Column(db.Integer)
    detail = db.Column(db.String(500))
    ip_address = db.Column(db.String(60))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    user = db.relationship("User")

    def __str__(self) -> str:
        return f"{self.action} by {self.actor}"
