# Testing

## Running the suite

```bash
python -m pytest              # whole suite, quiet summary (pytest.ini sets -q)
python -m pytest tests/test_leads.py -v
python -m pytest -k conversion -v
```

Configuration lives in `pytest.ini` (`testpaths = tests`, `--strict-markers`,
Deprecation/UserWarning noise filtered).

## Layout

| File | Covers |
| --- | --- |
| `conftest.py` | Fixtures: app, fresh DB per test, clients per role, domain objects, `login()` helper |
| `test_auth.py` | Login/logout, roles, deactivated accounts, registration validation, password change, CSRF rendering |
| `test_leads.py` | CRUD, search, filters, pagination, validation, duplicate email, assignment, scoping |
| `test_customers.py` | CRUD, duplicate prevention (create *and* edit conflict), segments, protected delete |
| `test_followups.py` | Scheduling, due/overdue reminders, status transitions, notifications |
| `test_sales_notifications.py` | Sales CRUD, expected-value ≠ revenue separation, notification creation/mark-read/mark-all |
| `test_dashboard_analytics.py` | Dashboard renders with live DB data, KPI correctness, formula presence |
| `test_predictions.py` | Unavailable-model handling, corrupt artefacts, forecast clamping, metrics honesty |
| `test_reports.py` | CSV export content, permission enforcement, Excel graceful degradation |
| `test_admin.py` | Admin-only routes, user create/deactivate/role change, 403s for employees |
| `test_zia.py` | Every local intent, empty-DB safety, no-fabrication guarantees, help output |

## Isolation model

- The `app` fixture is **session-scoped**; an `autouse` `_fresh_db` fixture drops and
  recreates the schema **around every test**, so tests are order-independent and can be run
  individually or with `-k`.
- Each test client (`admin_client`, `employee_client`) logs in through the real
  `/auth/login` route — auth is exercised, not mocked.
- `tmp_path` + `monkeypatch` are used to point model artefacts at missing/corrupt files
  without touching `ml/artifacts/`.

## Conventions

- Test emails use `example.com` / `example.org` (and `acme.example.com`,
  `globex.example.com`) because `email-validator` rejects reserved TLDs such as `.test`,
  `.example` and `.local` — a `.test` address fails validation and the test would exercise
  the wrong code path.
- CSRF is disabled by the `testing` config so POSTs can omit tokens; tests that assert on
  CSRF rendering enable it explicitly for their own request.
- Assertions check **behaviour** (status codes, DB state, response JSON contracts), not
  markup trivia, except where the UI contract itself is under test.

## Adding a test

1. Pick the closest existing file; add a `def test_<behaviour>()` using the fixtures you
   need (`admin_client`, `lead`, `customer`, ...).
2. For new domain objects, prefer a fixture in `conftest.py` over inline setup when two or
   more tests need it.
3. Never depend on data created by another test — `_fresh_db` guarantees an empty database.
