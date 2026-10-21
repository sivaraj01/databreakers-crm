"""Prediction behaviour when models are unavailable, and when they exist."""
from __future__ import annotations

import json

import pytest

from app.predictions import service


# ---------------------------------------------------------------------------
# Models NOT trained (default state of a fresh checkout)
# ---------------------------------------------------------------------------
def test_lead_model_status_when_not_trained(app, monkeypatch, tmp_path):
    """With no artifact on disk the service must say so, not raise."""
    monkeypatch.setattr(service, "LEAD_MODEL_FILE", tmp_path / "missing.pkl")
    monkeypatch.setattr(service, "LEAD_METRICS_FILE", tmp_path / "missing.json")
    with app.app_context():
        status = service.lead_model_status()
    assert status.available is False
    assert "not been trained" in status.message
    assert "train_lead_model" in status.message


def test_sales_model_status_when_not_trained(app, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "SALES_MODEL_FILE", tmp_path / "missing.pkl")
    monkeypatch.setattr(service, "SALES_METRICS_FILE", tmp_path / "missing.json")
    with app.app_context():
        status = service.sales_model_status()
    assert status.available is False
    assert "train_sales_model" in status.message


def test_predict_lead_conversion_unavailable_does_not_raise(app, lead, monkeypatch,
                                                            tmp_path):
    monkeypatch.setattr(service, "LEAD_MODEL_FILE", tmp_path / "missing.pkl")
    monkeypatch.setattr(service, "LEAD_METRICS_FILE", tmp_path / "missing.json")
    with app.app_context():
        from app.models import Lead

        result = service.predict_lead_conversion(Lead.query.get(lead))
    assert result["available"] is False
    assert "not been trained" in result["message"]
    assert "probability" not in result


def test_forecast_sales_unavailable_does_not_raise(app, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "SALES_MODEL_FILE", tmp_path / "missing.pkl")
    monkeypatch.setattr(service, "SALES_METRICS_FILE", tmp_path / "missing.json")
    with app.app_context():
        result = service.forecast_sales(6)
    assert result["available"] is False
    assert "train_sales_model" in result["message"]


def test_corrupt_artifact_is_handled(app, monkeypatch, tmp_path):
    """A truncated/corrupt .pkl must degrade to 'unavailable', not crash."""
    bad_model = tmp_path / "bad.pkl"
    bad_model.write_bytes(b"this is not a pickle")
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(service, "LEAD_MODEL_FILE", bad_model)
    monkeypatch.setattr(service, "LEAD_METRICS_FILE", bad_json)
    with app.app_context():
        status = service.lead_model_status()
    assert status.available is False


def test_predictions_page_renders_without_models(admin_client, monkeypatch,
                                                 tmp_path):
    """Page renders the 'Model not trained' state when no artefacts exist."""
    monkeypatch.setattr(service, "LEAD_MODEL_FILE", tmp_path / "missing.pkl")
    monkeypatch.setattr(service, "LEAD_METRICS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(service, "SALES_MODEL_FILE", tmp_path / "missing.pkl")
    monkeypatch.setattr(service, "SALES_METRICS_FILE", tmp_path / "missing.json")
    resp = admin_client.get("/predictions/")
    assert resp.status_code == 200
    assert b"Model not trained" in resp.data


def test_predictions_page_never_fabricates_accuracy(admin_client, monkeypatch,
                                                    tmp_path):
    """With no metrics file, the page must not display any accuracy number."""
    monkeypatch.setattr(service, "LEAD_METRICS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(service, "SALES_METRICS_FILE", tmp_path / "missing.json")
    resp = admin_client.get("/predictions/")
    assert resp.status_code == 200
    assert b"accuracy" not in resp.data.lower() or b"not been trained" in resp.data


def test_lead_score_endpoint_unavailable(admin_client, lead, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "LEAD_MODEL_FILE", tmp_path / "missing.pkl")
    monkeypatch.setattr(service, "LEAD_METRICS_FILE", tmp_path / "missing.json")
    resp = admin_client.get(f"/predictions/lead/{lead}")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["available"] is False
    assert "message" in payload


def test_batch_scores_endpoint_unavailable(admin_client, lead, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "LEAD_MODEL_FILE", tmp_path / "missing.pkl")
    monkeypatch.setattr(service, "LEAD_METRICS_FILE", tmp_path / "missing.json")
    resp = admin_client.get("/predictions/batch")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["available"] is False
    assert payload["items"] == []


# ---------------------------------------------------------------------------
# Models ARE trained (tiny synthetic artifact written by the test itself)
# ---------------------------------------------------------------------------
def _write_fake_lead_artifacts(tmp_path):
    """Train a tiny real model on synthetic data so the loader path runs."""
    import pickle

    import pandas as pd
    from sklearn.linear_model import LogisticRegression

    df = pd.DataFrame({
        "source": ["Website", "Referral", "Cold Call", "Website",
                   "Referral", "Cold Call", "Website", "Referral"],
        "expected_value": [1000.0, 5000.0, 200.0, 1200.0,
                           5200.0, 300.0, 900.0, 4800.0],
        "has_phone": [1, 1, 0, 1, 1, 0, 1, 1],
        "has_company": [1, 1, 0, 0, 1, 0, 1, 1],
        "days_since_created": [1, 3, 9, 2, 4, 12, 5, 2],
        "converted": [0, 1, 0, 0, 1, 0, 0, 1],
    })
    # Minimal pipeline: one-hot source + logistic regression.
    from sklearn.compose import ColumnTransformer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder

    pre = ColumnTransformer([("src", OneHotEncoder(handle_unknown="ignore"),
                              ["source"])],
                            remainder="passthrough")
    model = Pipeline([("prep", pre),
                      ("clf", LogisticRegression(max_iter=500))])
    X = df.drop(columns=["converted"])
    y = df["converted"]
    model.fit(X, y)

    model_file = tmp_path / "lead_conversion_model.pkl"
    with model_file.open("wb") as fh:
        pickle.dump(model, fh)

    metrics_file = tmp_path / "lead_conversion_metrics.json"
    metrics_file.write_text(json.dumps({
        "model_type": "LogisticRegression",
        "trained_at": "2026-01-01 00:00:00 UTC",
        "data_source": "synthetic-test-fixture",
        "n_training_rows": 8,
        "n_test_rows": 2,
        "feature_names": ["source", "expected_value", "has_phone",
                          "has_company", "days_since_created"],
        "test_metrics": {"accuracy": 0.75, "precision": 0.7,
                         "recall": 0.66, "f1": 0.68, "roc_auc": 0.8},
    }), encoding="utf-8")
    return model_file, metrics_file


def test_prediction_works_when_model_exists(app, lead, monkeypatch, tmp_path):
    model_file, metrics_file = _write_fake_lead_artifacts(tmp_path)
    monkeypatch.setattr(service, "LEAD_MODEL_FILE", model_file)
    monkeypatch.setattr(service, "LEAD_METRICS_FILE", metrics_file)

    with app.app_context():
        from app.models import Lead

        result = service.predict_lead_conversion(Lead.query.get(lead))

    assert result["available"] is True
    assert 0.0 <= result["probability"] <= 1.0
    assert result["band"] in ("Low", "Medium", "High")
    # Metrics must come from the stored evaluation report, verbatim.
    assert result["metrics"]["accuracy"] == 0.75
    assert result["limitations"], "predictions must always disclose limitations"


def test_model_status_reports_stored_metrics_verbatim(app, monkeypatch, tmp_path):
    _, metrics_file = _write_fake_lead_artifacts(tmp_path)
    monkeypatch.setattr(service, "LEAD_MODEL_FILE", tmp_path / "lead_conversion_model.pkl")
    monkeypatch.setattr(service, "LEAD_METRICS_FILE", metrics_file)
    with app.app_context():
        status = service.lead_model_status()
    assert status.available is True
    assert status.metrics["f1"] == 0.68
    assert status.method == "LogisticRegression"
