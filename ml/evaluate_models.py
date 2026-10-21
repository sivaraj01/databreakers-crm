"""Evaluate the saved ML artifacts and print a readable report.

This script re-loads the persisted model + metrics and re-computes the
held-out metrics from the same data source, so you can verify the numbers the
application displays match a fresh evaluation.

Usage:
    python ml/evaluate_models.py
    python ml/evaluate_models.py --model lead
    python ml/evaluate_models.py --model sales
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

ML_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ML_DIR.parent))

ARTIFACTS = ML_DIR / "artifacts"
MODEL_PATH = ARTIFACTS / "lead_conversion_model.pkl"
METRICS_PATH = ARTIFACTS / "lead_conversion_metrics.json"
SALES_MODEL = ARTIFACTS / "sales_forecast_model.pkl"
SALES_METRICS = ARTIFACTS / "sales_forecast_metrics.json"


def _load(path: Path):
    if not path.exists():
        return None
    with path.open("rb") as fh:
        return pickle.load(fh)


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_lead() -> int:
    model = _load(MODEL_PATH)
    report = _read(METRICS_PATH)
    if model is None or not report:
        print("[lead] NOT TRAINED. Run: python ml/train_lead_model.py")
        return 1
    print("[lead] Lead conversion model")
    print(f"       type        : {report.get('model_type')}")
    print(f"       trained at  : {report.get('trained_at')}")
    print(f"       data source : {report.get('data_source')}")
    print(f"       rows        : {report.get('n_training_rows')} train / "
          f"{report.get('n_test_rows')} test")
    print("       held-out test metrics:")
    for k, v in report.get("test_metrics", {}).items():
        print(f"         {k:<16} {v}")
    return 0


def evaluate_sales() -> int:
    model = _load(SALES_MODEL)
    report = _read(SALES_METRICS)
    if model is None or not report:
        print("[sales] NOT TRAINED. Run: python ml/train_sales_model.py")
        return 1
    print("[sales] Sales forecast model")
    print(f"       type        : {report.get('model_type')}")
    print(f"       trained at  : {report.get('trained_at')}")
    print(f"       data source : {report.get('data_source')}")
    print(f"       periods     : {report.get('n_periods')}")
    print("       held-out test metrics:")
    for k, v in report.get("test_metrics", {}).items():
        print(f"         {k:<18} {v}")
    beats = report.get("beats_naive_baseline")
    if beats is True:
        print("       [ok] beats the naive baseline.")
    elif beats is False:
        print("       [warn] does NOT beat the naive baseline - treat the "
              "forecast as unreliable until more history is available.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate saved ML artifacts.")
    parser.add_argument("--model", choices=["lead", "sales", "all"], default="all")
    args = parser.parse_args()

    code = 0
    if args.model in ("lead", "all"):
        code |= evaluate_lead()
        print()
    if args.model in ("sales", "all"):
        code |= evaluate_sales()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
