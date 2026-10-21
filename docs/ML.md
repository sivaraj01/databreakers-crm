# Machine learning methodology

DataBreakers ships two models. Both follow the same rule: **the application only ever
displays metrics that were measured on data the model did not train on.** If the model has
not been trained, the UI says "Model not trained" — it never invents an accuracy figure.

## 1. Lead conversion probability (binary classification)

**Script:** `ml/train_lead_model.py` · **Artefacts:** `ml/artifacts/lead_model.pkl`,
`ml/artifacts/lead_metrics.json` · **Service:** `app/predictions/service.py`

### Task

Predict the probability that a lead will convert (`converted = 1`).

### Features (all captured at lead-creation time)

| Type | Columns |
| --- | --- |
| Numeric | `expected_value`, `has_phone`, `has_company`, `days_since_created` |
| Categorical | `source` (one-hot encoded inside the pipeline) |

### Leakage prevention

Columns that could leak the answer are excluded **by construction** in
`ml/preprocessing.py → LEAD_EXCLUDED_COLUMNS`:

- `converted` — the target itself
- `status` — the final pipeline stage encodes the label (a `Converted` status *is* the label)
- `converted_at`, `customer_id`, `lost_reason` — only exist after the outcome is known
- `last_contact_at`, `next_followup_at`, `notes`, `updated_at` — post-creation activity
- `id`, `email`, `name`, `phone`, `company`, `assigned_to` — identity / pure ID columns

The label (`converted`) is derived from lead status at dataset build time, but status is
never fed back in as a feature.

### Split & model

- `train_test_split(X, y, test_size=0.25, random_state=42, stratify=y)` — stratified so the
  positive-class rate is the same in train and test.
- Pipeline: `OneHotEncoder(handle_unknown="ignore")` on `source` +
  `StandardScaler` on numerics + `LogisticRegression(max_iter=1000)`.
- Evaluation threshold 0.5.

### Metrics reported (held-out test set only)

`accuracy`, `precision`, `recall`, `f1`, `roc_auc`, plus `test_rows` and
`positive_rate_test` for context. `zero_division=0` so a degenerate test fold can never
crash or print a misleading score.

The Predictions page shows these verbatim from `lead_metrics.json`, together with
`n_training_rows` / `n_test_rows` and the training timestamp.

### Honest-failure behaviour

- If training data lacks both classes → script exits with an error and writes **nothing**.
- If no model file exists → `service.lead_model_status().available == False` and every
  endpoint returns `{"available": false, "message": ...}` — no scores, no fake percentages.

## 2. Sales forecast (time series regression)

**Script:** `ml/train_sales_model.py` · **Artefacts:** `ml/artifacts/sales_model.pkl`,
`ml/artifacts/sales_metrics.json`

### Task

Forecast monthly revenue for the next N months (default 6, clamped 1–24).

### Features

`build_sales_series()` resamples completed sales into a continuous monthly series (missing
months filled with 0.0) and builds `t = 0..n-1` (month index). The regressor is a
`Ridge(alpha=1.0)` on polynomial (degree 2 by default) features of `t`.

### Split — time-aware, never random

`ml/preprocessing.py → time_series_split()`:

```
cut = max(2, round(n × (1 − test_fraction)))   # default test_fraction = 0.25
train = rows[:cut]   (the past)
test  = rows[cut:]   (the most recent months)
```

A random split would let the future leak into training — this is explicitly rejected in
both the code and docstrings. The test window is always the *tail* of the series.

### Metrics

`MAE`, `RMSE`, `MAPE` (guarding against division by zero for zero months), `test_rows`, and
the train/test date range, written to `sales_metrics.json`.

### Honest-failure behaviour

- Fewer than 4 monthly periods → `ValueError` with a clear message; nothing written.
- No model file → forecast endpoint returns `{"available": false, "message": ...}` and the
  dashboard renders "Model not trained".

## Reproducing

```bash
# Use the bundled sample CSVs, or the live database:
python scripts/generate_samples.py     # writes ml/datasets/*.csv
python ml/train_lead_model.py          # or --csv ml/datasets/leads.csv
python ml/train_sales_model.py         # or --csv ml/datasets/sales.csv

# Compare both models' reports:
python ml/evaluate_models.py --model all
```

Training on the bundled **sample** dataset prints a `[note]` reminding you the numbers come
from synthetic data — the app also surfaces `data_source` from the metrics file so the UI
never presents sample-data metrics as if they came from your real pipeline.

## Known limitations

- A single train/test split (no k-fold) — deliberate for transparency and small datasets.
- The lead model assumes lead features are static post-creation; heavy re-scoring over
  long-lived leads may drift.
- The sales model extrapolates polynomial trend only; it does not model seasonality or
  external regressors (hence degree ≤ 2 by default to limit runaway curvature).
