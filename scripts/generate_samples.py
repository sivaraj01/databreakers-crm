"""Generate clearly-labelled SAMPLE datasets for the ML training scripts.

These are synthetic. They exist so the training pipeline can be exercised
before real CRM data exists. The application labels anything produced from
them as "sample" data.

Usage:
    python scripts/generate_samples.py
"""
from __future__ import annotations

import math
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SAMPLES = Path(__file__).resolve().parents[1] / "ml" / "datasets"
SEED = 20260101

SOURCES = ["Website", "Referral", "Cold Call", "Social Media",
           "Trade Show", "Email Campaign", "Partner", "Walk-in"]

# Underlying win propensity per source, so the model has a real signal to learn.
SOURCE_PROPENSITY = {
    "Referral": .42, "Partner": .36, "Trade Show": .30,
    "Website": .24, "Email Campaign": .20, "Social Media": .17,
    "Cold Call": .12, "Walk-in": .28,
}


def make_leads(n: int = 400) -> pd.DataFrame:
    rng = random.Random(SEED)
    rows = []
    base = datetime(2025, 1, 1)
    for i in range(n):
        source = rng.choice(SOURCES)
        propensity = SOURCE_PROPENSITY[source]
        expected_value = round(rng.lognormvariate(8.6, .8), 2)
        has_phone = 1 if rng.random() > .3 else 0
        has_company = 1 if rng.random() > .35 else 0
        created = base + timedelta(days=rng.randint(0, 300),
                                   hours=rng.randint(0, 23))
        # Higher-value, contactable, referral leads convert more often.
        score = propensity
        score += .06 if has_phone else -.04
        score += .05 if has_company else -.03
        score += min(.15, expected_value / 200000)
        converted = 1 if rng.random() < min(.92, max(.02, score)) else 0
        rows.append({
            "lead_id": i + 1,
            "source": source,
            "expected_value": expected_value,
            "has_phone": has_phone,
            "has_company": has_company,
            "created_at": created.isoformat(),
            "converted": converted,
        })
    return pd.DataFrame(rows)


def make_sales(months: int = 36, seed: int = SEED) -> pd.DataFrame:
    """Daily sales with a linear trend plus mild seasonality and noise."""
    rng = random.Random(seed)
    start = datetime(2023, 1, 1)
    rows = []
    for day_offset in range(months * 30):
        d = start + timedelta(days=day_offset)
        trend = 400 + day_offset * 2.2
        seasonal = 160 * math.sin(day_offset / 365 * 2 * math.pi)
        noise = rng.gauss(0, 130)
        amount = max(0.0, trend + seasonal + noise)
        rows.append({
            "sold_at": d.strftime("%Y-%m-%d"),
            "amount": round(amount, 2),
            "quantity": 1,
        })
    return pd.DataFrame(rows)


def main() -> None:
    SAMPLES.mkdir(parents=True, exist_ok=True)

    leads = make_leads()
    leads_csv = SAMPLES / "sample_leads.csv"
    leads.to_csv(leads_csv, index=False)
    print(f"[ok] {leads_csv}  ({len(leads)} rows, "
          f"positive rate {leads['converted'].mean():.2%})")

    sales = make_sales()
    sales_csv = SAMPLES / "sample_sales.csv"
    sales.to_csv(sales_csv, index=False)
    print(f"[ok] {sales_csv}  ({len(sales)} rows)")

    print("\n[info] These datasets are SYNTHETIC and provided only so the ML "
          "pipeline can be demonstrated. Train on your real CRM data for a "
          "meaningful model.")


if __name__ == "__main__":
    main()
