"""Shared preprocessing helpers for the DataBreakers CRM ML pipeline.

Design rules enforced here:

* **Explicit feature/target split.** ``build_lead_dataset`` returns
  ``(X, y, feature_names)``; the target is never a column of ``X``.
* **No target leakage.** Fields that only exist *after* a lead converts
  (``converted_at``, ``customer_id``, actual revenue) are dropped by
  construction. ``status`` is deliberately excluded from the default feature
  set because the final statuses "Converted"/"Lost" are the label itself.
* **Reproducible.** A fixed random seed is used everywhere.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

RANDOM_STATE = 42

# The label: did this lead end up converted?
TARGET_COLUMN = "converted"

# Features allowed in the default lead-conversion model. Everything here is
# known at lead-creation time, which is what makes the prediction honest.
LEAD_FEATURES = [
    "source",            # categorical
    "expected_value",    # numeric
    "has_phone",         # derived from data captured at creation
    "has_company",       # derived from data captured at creation
    "days_since_created",# age of the lead at scoring time
]

CATEGORICAL_FEATURES = ["source"]
NUMERIC_FEATURES = ["expected_value", "has_phone", "has_company",
                    "days_since_created"]

# Columns that must never be used as features (leakage or pure ID).
LEAD_EXCLUDED_COLUMNS = [
    TARGET_COLUMN, "id", "email", "name", "phone", "company",
    "converted_at", "customer_id", "lost_reason", "notes",
    "last_contact_at", "next_followup_at", "updated_at", "assigned_to",
    "status",  # final status encodes the label -> leakage
]


def build_lead_dataset(leads_df: pd.DataFrame,
                       as_of: datetime | None = None) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    """Turn a raw leads frame into (X, y, feature_names).

    ``leads_df`` must contain at least the columns in ``LEAD_FEATURES`` plus a
    ``converted`` boolean/int column. Rows with a missing target are dropped.
    """
    if leads_df.empty:
        return (pd.DataFrame(columns=NUMERIC_FEATURES + CATEGORICAL_FEATURES),
                pd.Series(dtype=int), LEAD_FEATURES)

    df = leads_df.copy()
    df = df.dropna(subset=[TARGET_COLUMN])

    as_of = as_of or datetime.utcnow()
    created = pd.to_datetime(df.get("created_at"), errors="coerce")
    df["days_since_created"] = (
        (pd.Timestamp(as_of) - created).dt.days.clip(lower=0)
        if created.notna().any() else 0)
    df["days_since_created"] = df["days_since_created"].fillna(0)

    df["has_phone"] = df.get("phone").fillna("").astype(str).str.strip().ne("").astype(int) \
        if "phone" in df.columns else 0
    df["has_company"] = df.get("company").fillna("").astype(str).str.strip().ne("").astype(int) \
        if "company" in df.columns else 0
    df["expected_value"] = pd.to_numeric(df.get("expected_value"), errors="coerce").fillna(0.0)
    df["source"] = df.get("source", pd.Series(["Other"] * len(df))).fillna("Other")

    X = df[LEAD_FEATURES].copy()
    y = df[TARGET_COLUMN].astype(int)
    return X, y, list(LEAD_FEATURES)


def build_sales_series(sales_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate sales into a monthly revenue series with a time index.

    Returns a frame with columns ``t`` (integer month index) and ``revenue``.
    Months with no sales are filled with 0 so the series is continuous.
    """
    if sales_df.empty:
        return pd.DataFrame(columns=["t", "revenue", "label"])

    df = sales_df.copy()
    df["sold_at"] = pd.to_datetime(df["sold_at"], errors="coerce")
    df = df.dropna(subset=["sold_at"])
    if df.empty:
        return pd.DataFrame(columns=["t", "revenue", "label"])

    if "quantity" in df.columns:
        df["revenue"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0) * \
            pd.to_numeric(df["quantity"], errors="coerce").fillna(1)
    else:
        df["revenue"] = pd.to_numeric(df.get("amount"), errors="coerce").fillna(0)

    df = df.set_index("sold_at").sort_index()
    monthly = df["revenue"].resample("ME").sum()

    # Ensure a continuous month axis starting from the first sale.
    full_index = pd.date_range(monthly.index.min(), monthly.index.max(), freq="ME")
    monthly = monthly.reindex(full_index, fill_value=0.0)

    out = pd.DataFrame({
        "t": np.arange(len(monthly)),
        "revenue": monthly.values.astype(float),
        "label": [d.strftime("%b %y") for d in monthly.index],
    })
    return out


def time_series_split(df: pd.DataFrame, test_fraction: float = 0.25):
    """Time-aware split: train on the past, test on the most recent months.

    Using a random split here would leak the future into training, so we never
    do that for the sales model.
    """
    n = len(df)
    if n < 4:
        raise ValueError(f"Need at least 4 monthly periods to split, got {n}.")
    cut = max(2, int(round(n * (1 - test_fraction))))
    train, test = df.iloc[:cut], df.iloc[cut:]
    if test.empty:
        raise ValueError("Test window is empty; provide more history or lower test_fraction.")
    return train, test
