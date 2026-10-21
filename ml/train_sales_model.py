"""Train the monthly sales-forecast model.

Design rules:
  * Data is aggregated into a continuous monthly revenue series.
  * The train/test split is **time-aware** (oldest months train, newest test).
    A random split would leak the future into training, so it is never used.
  * Evaluation metrics are computed on the held-out most-recent months and
    written to JSON. The application only ever displays these measured values.

Usage:
    python ml/train_sales_model.py
    python ml/train_sales_model.py --csv ml/datasets/sample_sales.csv --test-fraction 0.25
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import (mean_absolute_error, mean_absolute_percentage_error,
                             mean_squared_error, r2_score)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import PolynomialFeatures

ML_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ML_DIR.parent))

from ml.preprocessing import RANDOM_STATE, build_sales_series, time_series_split  # noqa: E402

ARTIFACTS = ML_DIR / "artifacts"
DATASETS = ML_DIR / "datasets"
MODEL_PATH = ARTIFACTS / "sales_forecast_model.pkl"
METRICS_PATH = ARTIFACTS / "sales_forecast_metrics.json"
HISTORY_PATH = ARTIFACTS / "sales_history.json"
SAMPLE_CSV = DATASETS / "sample_sales.csv"


def load_sales(csv_path: Path | None = None) -> tuple[pd.DataFrame, str]:
    if csv_path and csv_path.exists():
        return pd.read_csv(csv_path), f"csv:{csv_path.name}"
    try:
        from app import create_app
        from app.extensions import db
        from app.models import Sale, SaleStatus

        app = create_app()
        with app.app_context():
            rows = [{"sold_at": s.sold_at.isoformat() if s.sold_at else None,
                     "amount": s.amount, "quantity": s.quantity}
                    for s in Sale.query.filter_by(status=SaleStatus.COMPLETED.value).all()]
        frame = pd.DataFrame(rows)
        if not frame.empty and len(frame) >= 24:
            return frame, "database"
    except Exception as exc:  # pragma: no cover
        print(f"[warn] Could not read the database ({exc}); falling back to sample data.")

    if SAMPLE_CSV.exists():
        return pd.read_csv(SAMPLE_CSV), "sample"
    raise SystemExit("[error] No sales history. Add sales to the CRM or place a "
                     f"CSV at {SAMPLE_CSV}.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the sales forecast model.")
    parser.add_argument("--csv", type=Path, default=None)
    parser.add_argument("--test-fraction", type=float, default=0.25)
    parser.add_argument("--degree", type=int, default=2,
                        help="Polynomial degree for the trend model (default 2).")
    args = parser.parse_args()

    raw, provenance = load_sales(args.csv)
    series = build_sales_series(raw)
    if len(series) < 4:
        print(f"[error] Only {len(series)} monthly periods available. "
              "At least 4 are required to evaluate a forecast honestly. "
              "No artifact was written.")
        return 1

    train, test = time_series_split(series, args.test_fraction)
    print(f"[info] {len(series)} periods from {provenance}: "
          f"train={len(train)}, test={len(test)}.")

    pipeline = Pipeline([
        ("poly", PolynomialFeatures(degree=args.degree, include_bias=False)),
        ("model", Ridge(alpha=1.0, random_state=RANDOM_STATE)),
    ])
    pipeline.fit(train[["t"]], train["revenue"])

    predicted = pipeline.predict(test[["t"]])
    predicted = np.clip(predicted, 0, None)

    y_true = test["revenue"].to_numpy()
    metrics = {
        "mae": round(float(mean_absolute_error(y_true, predicted)), 2),
        "rmse": round(float(np.sqrt(mean_squared_error(y_true, predicted))), 2),
        "mape_percent": round(float(mean_absolute_percentage_error(y_true, predicted)) * 100, 2),
        "r2": round(float(r2_score(y_true, predicted)), 4) if len(test) > 1 else None,
        "test_periods": int(len(test)),
        "baseline_mae": round(float(mean_absolute_error(
            y_true, np.full_like(y_true, train["revenue"].mean()))), 2),
    }
    metrics = {k: v for k, v in metrics.items() if v is not None}

    # baseline_mae is the naive "predict the training mean" error; the model is
    # only useful if it beats this. Reported so nobody over-trusts it.
    useful = metrics["mae"] <= metrics["baseline_mae"]

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with MODEL_PATH.open("wb") as fh:
        pickle.dump(pipeline, fh)

    HISTORY_PATH.write_text(json.dumps({
        "labels": series["label"].tolist(),
        "revenue": [round(float(v), 2) for v in series["revenue"]],
    }, indent=2), encoding="utf-8")

    report = {
        "model_type": f"PolynomialFeatures(deg={args.degree})+Ridge",
        "task": "monthly_sales_forecast",
        "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "data_source": provenance,
        "n_periods": int(len(series)),
        "test_metrics": metrics,
        "beats_naive_baseline": bool(useful),
        "notes": (
            "Split is time-aware: the newest months are held out. 'baseline_mae' "
            "is the error of a naive model that always predicts the training "
            "mean; the forecast is only informative if 'mae' is lower."
        ),
    }
    METRICS_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"[ok] Model written to   {MODEL_PATH}")
    print(f"[ok] Metrics written to {METRICS_PATH}")
    print(f"[ok] History written to {HISTORY_PATH}")
    print("[ok] Held-out test metrics:")
    for k, v in metrics.items():
        print(f"       {k:<18} {v}")
    print(f"[{'ok' if useful else 'warn'}] "
          f"{'Beats' if useful else 'Does NOT beat'} the naive baseline "
          f"({metrics['mae']} vs {metrics['baseline_mae']}).")
    if provenance == "sample":
        print("[note] Trained on the bundled SAMPLE dataset, not your real sales.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
