"""Train the lead-conversion prediction model.

What this script does, in order:
  1. Loads labelled historical leads from the CRM database (or the bundled
     clearly-labelled SAMPLE dataset when the database has no converted leads
     yet).
  2. Builds an explicit feature matrix and target with ``ml.preprocessing`` -
     target leakage columns are excluded by construction.
  3. Splits into train/test with stratification.
  4. Trains a scikit-learn pipeline (imputer + scaler + one-hot + classifier).
  5. Evaluates on the held-out test set and writes BOTH the model artifact
     and the metrics report.
  6. Writes a JSON report of the measured metrics. The application reads this
     file and displays those numbers - it never invents accuracy.

Usage:
    python ml/train_lead_model.py
    python ml/train_lead_model.py --csv ml/datasets/sample_leads.csv
    python ml/train_lead_model.py --test-size 0.3
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, f1_score, precision_score,
                             recall_score, roc_auc_score)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ML_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ML_DIR.parent))

from ml.preprocessing import (CATEGORICAL_FEATURES, LEAD_FEATURES,  # noqa: E402
                              NUMERIC_FEATURES, RANDOM_STATE,
                              build_lead_dataset)

ARTIFACTS = ML_DIR / "artifacts"
DATASETS = ML_DIR / "datasets"
MODEL_PATH = ARTIFACTS / "lead_conversion_model.pkl"
METRICS_PATH = ARTIFACTS / "lead_conversion_metrics.json"
SAMPLE_CSV = DATASETS / "sample_leads.csv"


def load_leads(csv_path: Path | None = None) -> tuple[pd.DataFrame, str]:
    """Return (frame, provenance). Prefers the live database."""
    if csv_path and csv_path.exists():
        return pd.read_csv(csv_path), f"csv:{csv_path.name}"

    # Try the CRM database first.
    try:
        from app import create_app
        from app.extensions import db
        from app.models import Lead

        app = create_app()
        with app.app_context():
            rows = [{
                "source": l.source,
                "expected_value": l.expected_value or 0,
                "phone": l.phone,
                "company": l.company,
                "created_at": l.created_at.isoformat() if l.created_at else None,
                "converted": 1 if l.status == "Converted" else 0,
            } for l in Lead.query.all()]
        frame = pd.DataFrame(rows)
        # We need both classes and a usable sample size to train honestly.
        if not frame.empty and frame["converted"].nunique() == 2 and len(frame) >= 40:
            return frame, "database"
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"[warn] Could not read the database ({exc}); falling back to sample data.")

    if SAMPLE_CSV.exists():
        return pd.read_csv(SAMPLE_CSV), "sample"
    raise SystemExit(
        "[error] No training data. Either add leads to the CRM database or place "
        f"a labelled CSV at {SAMPLE_CSV}.")


def build_pipeline() -> Pipeline:
    numeric = Pipeline([("impute", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler())])
    categorical = Pipeline([("impute", SimpleImputer(strategy="most_frequent")),
                            ("onehot", OneHotEncoder(handle_unknown="ignore"))])
    preprocessor = ColumnTransformer(
        [("num", numeric, NUMERIC_FEATURES),
         ("cat", categorical, CATEGORICAL_FEATURES)])
    clf = LogisticRegression(max_iter=1000, random_state=RANDOM_STATE,
                             class_weight="balanced")
    return Pipeline([("prep", preprocessor), ("clf", clf)])


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the lead conversion model.")
    parser.add_argument("--csv", type=Path, default=None,
                        help="Optional labelled CSV instead of the database.")
    parser.add_argument("--test-size", type=float, default=0.25)
    args = parser.parse_args()

    raw, provenance = load_leads(args.csv)
    print(f"[info] Loaded {len(raw)} rows from {provenance}.")

    X, y, features = build_lead_dataset(raw)
    if X.empty or y.nunique() < 2:
        print("[error] Training data must contain both converted and non-converted "
              "leads. Nothing was written.")
        return 1

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=args.test_size, random_state=RANDOM_STATE, stratify=y)

    pipeline = build_pipeline()
    pipeline.fit(X_train, y_train)

    # ---- held-out evaluation (these numbers are reported verbatim in the app)
    proba = pipeline.predict_proba(X_test)[:, 1]
    preds = (proba >= 0.5).astype(int)
    metrics = {
        "accuracy": round(float(accuracy_score(y_test, preds)), 4),
        "precision": round(float(precision_score(y_test, preds, zero_division=0)), 4),
        "recall": round(float(recall_score(y_test, preds, zero_division=0)), 4),
        "f1": round(float(f1_score(y_test, preds, zero_division=0)), 4),
        "roc_auc": round(float(roc_auc_score(y_test, proba)), 4)
        if y_test.nunique() > 1 else None,
        "test_rows": int(len(y_test)),
        "positive_rate_test": round(float(y_test.mean()), 4),
    }
    metrics = {k: v for k, v in metrics.items() if v is not None}

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with MODEL_PATH.open("wb") as fh:
        pickle.dump(pipeline, fh)

    report = {
        "model_type": type(pipeline.named_steps["clf"]).__name__,
        "task": "lead_conversion_binary_classification",
        "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "data_source": provenance,
        "n_training_rows": int(len(X_train)),
        "n_test_rows": int(len(X_test)),
        "feature_names": features,
        "categorical_features": CATEGORICAL_FEATURES,
        "numeric_features": NUMERIC_FEATURES,
        "positive_class_rate": round(float(y.mean()), 4),
        "test_metrics": metrics,
        "notes": (
            "Metrics are measured on a held-out test set the model never saw "
            "during fitting. They are not estimates produced by the application."
        ),
    }
    METRICS_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"[ok] Model written to   {MODEL_PATH}")
    print(f"[ok] Metrics written to {METRICS_PATH}")
    print("[ok] Held-out test metrics:")
    for k, v in metrics.items():
        print(f"       {k:<16} {v}")
    if provenance == "sample":
        print("[note] Trained on the bundled SAMPLE dataset, not your real leads. "
              "Re-run this script after adding real data for a meaningful model.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
