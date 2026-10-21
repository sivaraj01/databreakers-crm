"""Machine-learning prediction service.

Loads serialized artifacts produced by ``ml/train_*.py``. Every method is
safe to call when no model exists - it returns a structured "unavailable"
payload instead of raising, so the UI can show a helpful message.

Honesty rules enforced here:
  * We never report accuracy that was not measured at training time.
  * Metrics come from the persisted evaluation report, never invented.
  * Feature importances are only exposed when the model exposes them.
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models import Lead, LeadStatus

ARTIFACTS_DIR = Path(__file__).resolve().parents[2] / "ml" / "artifacts"

LEAD_MODEL_FILE = ARTIFACTS_DIR / "lead_conversion_model.pkl"
LEAD_METRICS_FILE = ARTIFACTS_DIR / "lead_conversion_metrics.json"
SALES_MODEL_FILE = ARTIFACTS_DIR / "sales_forecast_model.pkl"
SALES_METRICS_FILE = ARTIFACTS_DIR / "sales_forecast_metrics.json"

NOT_TRAINED_MESSAGE = (
    "This model has not been trained yet, so no prediction is shown. "
    "Run the training script to create it:"
)
LEAD_TRAIN_COMMAND = "python ml/train_lead_model.py"
SALES_TRAIN_COMMAND = "python ml/train_sales_model.py"


@dataclass
class ModelStatus:
    """Structured state of one ML artifact."""

    available: bool
    name: str
    message: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    trained_at: str = ""
    method: str = ""
    n_training_rows: int = 0
    features: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _load_pickle(path: Path):
    if not path.exists():
        return None
    try:
        with path.open("rb") as fh:
            return pickle.load(fh)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Lead conversion prediction
# ---------------------------------------------------------------------------
LEAD_LIMITATIONS = [
    "A probability is a model estimate, not a guarantee - treat it as a "
    "prioritisation hint.",
    "The model only knows the fields captured at lead-creation time. A lead "
    "that gathers new information after capture may behave differently.",
    "Performance depends on the historical data it was trained on. If your "
    "lead mix changes, re-train the model.",
    "The positive class is usually rare, so a high probability is meaningful "
    "but a low probability is not proof a lead is worthless.",
]


def lead_model_status() -> ModelStatus:
    metrics = _read_json(LEAD_METRICS_FILE)
    model = _load_pickle(LEAD_MODEL_FILE)
    if model is None or not metrics:
        return ModelStatus(
            available=False, name="Lead conversion prediction",
            message=f"{NOT_TRAINED_MESSAGE} {LEAD_TRAIN_COMMAND}",
        )
    return ModelStatus(
        available=True, name="Lead conversion prediction",
        message="Model loaded from disk.",
        metrics=metrics.get("test_metrics", {}),
        trained_at=metrics.get("trained_at", ""),
        method=metrics.get("model_type", ""),
        n_training_rows=metrics.get("n_training_rows", 0),
        features=metrics.get("feature_names", []),
        limitations=LEAD_LIMITATIONS,
    )


def predict_lead_conversion(lead: Lead) -> dict:
    """Return a conversion probability for one lead, or an unavailable payload."""
    status = lead_model_status()
    if not status.available:
        return {
            "available": False, "lead_id": lead.id,
            "message": status.message,
            "evaluated_at": status.trained_at,
            "metrics": {},
        }

    model = _load_pickle(LEAD_MODEL_FILE)
    try:
        row = _lead_feature_frame(lead)
        if hasattr(model, "predict_proba"):
            probability = float(model.predict_proba(row)[0][1])
        else:  # pragma: no cover - non-probabilistic fallback
            probability = float(model.predict(row)[0])
    except Exception as exc:
        return {
            "available": False, "lead_id": lead.id,
            "message": f"The trained model could not score this lead ({exc}). "
                       "Re-train with: " + LEAD_TRAIN_COMMAND,
            "metrics": status.metrics,
        }

    band = ("High" if probability >= 0.7 else "Medium" if probability >= 0.4 else "Low")
    return {
        "available": True, "lead_id": lead.id,
        "probability": round(probability, 4),
        "percent": round(probability * 100, 1),
        "band": band,
        "message": "",
        "metrics": status.metrics,
        "limitations": status.limitations,
        "evaluated_at": status.trained_at,
    }


def _lead_feature_frame(lead: Lead):
    """Build a one-row DataFrame with exactly the training feature names."""
    import pandas as pd

    features = _read_json(LEAD_METRICS_FILE).get("feature_names")
    source = (lead.source or "Other").lower().replace(" ", "_")
    payload = {
        "source": source,
        "expected_value": float(lead.expected_value or 0),
        "has_phone": 1 if lead.phone else 0,
        "has_company": 1 if lead.company else 0,
        "days_since_created": max(
            0, (datetime.utcnow() - (lead.created_at or datetime.utcnow())).days),
        "status": (lead.status or LeadStatus.NEW.value).lower(),
    }
    for s in ("contacted", "interested", "qualified", "converted", "lost"):
        payload[f"status_{s}"] = 1 if payload["status"] == s else 0
    for src in ("website", "referral", "cold_call", "social_media", "trade_show",
                "email_campaign", "walk_in", "partner"):
        payload[f"source_{src}"] = 1 if source == src else 0

    if features:
        frame = pd.DataFrame([payload])
        for name in features:
            if name not in frame.columns:
                frame[name] = 0
        return frame[features]
    return pd.DataFrame([payload])


# ---------------------------------------------------------------------------
# Sales forecasting
# ---------------------------------------------------------------------------
SALES_LIMITATIONS = [
    "A monthly forecast extrapolates the recent trend. It cannot account for "
    "seasonality it has never seen or for one-off large deals.",
    "Forecasts are only as good as the sales history supplied - a few months "
    "of data produces a wide uncertainty band.",
    "Compare the forecast against actuals each month and re-train when the "
    "gap grows."
]


def sales_model_status() -> ModelStatus:
    metrics = _read_json(SALES_METRICS_FILE)
    model = _load_pickle(SALES_MODEL_FILE)
    if model is None or not metrics:
        return ModelStatus(
            available=False, name="Sales forecast",
            message=f"{NOT_TRAINED_MESSAGE} {SALES_TRAIN_COMMAND}",
        )
    return ModelStatus(
        available=True, name="Sales forecast",
        message="Model loaded from disk.",
        metrics=metrics.get("test_metrics", {}),
        trained_at=metrics.get("trained_at", ""),
        method=metrics.get("model_type", ""),
        n_training_rows=metrics.get("n_periods", 0),
        limitations=SALES_LIMITATIONS,
    )


def forecast_sales(months_ahead: int = 6) -> dict:
    """Forecast future monthly revenue, or return an unavailable payload."""
    status = sales_model_status()
    if not status.available:
        return {"available": False, "message": status.message, "metrics": {}}

    model = _load_pickle(SALES_MODEL_FILE)
    try:
        import pandas as pd

        payload = _read_json(SALES_MODEL_FILE.with_name("sales_history.json"))
        history = payload.get("revenue", [])
        if not history:
            raise ValueError("no saved history")
        start_index = len(history)
        rows = [{"t": i} for i in range(start_index, start_index + months_ahead)]
        predictions = [max(0.0, round(float(v), 2))
                       for v in model.predict(pd.DataFrame(rows))]
    except Exception as exc:
        return {"available": False, "metrics": status.metrics,
                "message": f"Forecast failed ({exc}). Re-train with: {SALES_TRAIN_COMMAND}"}

    labels = []
    today = datetime.utcnow().replace(day=1)
    cursor = today
    for _ in range(months_ahead):
        labels.append(cursor.strftime("%b %y"))
        cursor = _add_month(cursor)

    history_labels = payload.get("labels", [])
    return {
        "available": True, "metrics": status.metrics,
        "limitations": status.limitations, "evaluated_at": status.trained_at,
        "history_labels": history_labels, "history_values": history,
        "forecast_labels": labels, "forecast_values": predictions,
    }


def _add_month(dt: datetime) -> datetime:
    month = dt.month + 1
    year = dt.year
    if month > 12:
        month, year = 1, year + 1
    return dt.replace(year=year, month=month)
