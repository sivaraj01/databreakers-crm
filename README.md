# DataBreakers — CRM & Lead Management System with Customer Analytics and Predictive Insights

**DataBreakers** is a full-stack CRM web application built with Flask. It covers the complete
lead-to-cash lifecycle — lead capture, follow-ups, sales recording, customer analytics and
predictive insights — with role-based access control, an in-app notification centre and a
deterministic AI assistant (Zia) that never fabricates numbers.

---

## Feature overview

| Module | What it does |
| --- | --- |
| **Auth** | Register / login / logout, password hashing (Werkzeug), role-based access (Admin, Manager, Employee), deactivated accounts blocked, profile + password change. |
| **Dashboard** | Live KPI cards and Chart.js charts computed from the database: pipeline value, revenue, conversion rate, follow-up backlog, monthly trends, source & status breakdowns. |
| **Leads** | Full CRUD, search, filtering (status/source/owner), sorting, pagination, validation, status pipeline (New → Contacted → Qualified → Proposal → Converted / Lost), duplicate-email prevention, assignment to users. |
| **Customers** | Full CRUD with search/pagination/validation, segments, linked sales and interaction history, duplicate prevention, protected deletion when sales exist. |
| **Follow-ups** | Schedule calls/emails/meetings against leads, due/overdue reminders, status tracking, in-app notification on creation & due dates. |
| **Sales** | Record actual closed-won sales (amount, quantity, product, customer) **separate** from a lead's expected deal value — expected value never counts as revenue anywhere. |
| **Products** | Product catalogue with SKU, category, price, active flag; used by sales entries. |
| **Analytics** | Pandas-powered reports: conversion funnel, source ROI, revenue trends, segment analysis, engagement scoring — every metric documented with its formula in `docs/ANALYTICS.md`. |
| **Predictions** | Scikit-learn **lead conversion probability** classifier and **sales forecast** regressor, trained with proper train/test splits and leakage prevention; honest metrics only (never fabricates accuracy). |
| **Zia AI** | In-app assistant with a local, deterministic analytics engine as the default answer path. Optional LLM wording layer (key read from server env only) that may only rephrase verified facts. |
| **Reports** | CSV exports with permission enforcement (admins export everything; employees only their own scope). Excel export when `openpyxl` is installed, graceful degradation when it is not. |
| **Notifications** | In-app notification centre (no email/WhatsApp): unread badges, mark-read / mark-all-read. |
| **User Management** | Admin-only: create/deactivate users, change roles, reset access without hardcoded credentials. |
| **Settings** | Profile, preferences, and application configuration surfaced in-app. |

The sidebar navigation contains **no dead links** — every entry points to a registered route.

---

## Tech stack

- **Backend:** Flask 3, Flask-SQLAlchemy, Flask-Login, Flask-WTF/WTForms
- **Database:** SQLite (file) by default — `DATABASE_URL` overridable for any SQLAlchemy URI
- **Analysis / ML:** Pandas, NumPy, Scikit-learn
- **Frontend:** Jinja2 templates, Bootstrap 5, Chart.js, custom `crm.css` / `crm.js`
- **Testing:** pytest
- **Packaging:** Docker + docker-compose

---

## Quick start (local)

```bash
# 1. Create a virtual environment and install dependencies
python -m venv .venv
.venv\Scripts\activate          # Windows:  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt

# 2. Configure the environment (secrets are NEVER hardcoded)
copy .env.example .env          # macOS/Linux: cp .env.example .env
#    edit .env and set SECRET_KEY to a long random value:
python -c "import secrets; print(secrets.token_hex(32))"

# 3. Initialise the database and create the first admin account
#    (the password is prompted interactively or read from ADMIN_PASSWORD env — never hardcoded)
python scripts/init_db.py --create-admin

# 4. (Optional) load realistic demo data + train the models
python scripts/seed_demo_data.py
python ml/train_lead_model.py
python ml/train_sales_model.py

# 5. Run the app
python app.py
# → open http://127.0.0.1:5000
```

`scripts/init_db.py --seed` creates the schema **and** demo data in one step.

---

## Running the tests

```bash
python -m pytest
```

The suite uses an isolated in-memory SQLite database per test (see `tests/conftest.py`), so
tests are order-independent. Test fixtures use `example.com` / `example.org` domains because
the `email-validator` library rejects reserved TLDs such as `.test` / `.local`.

---

## Docker

```bash
# Build and run (app on http://localhost:5000, data persisted in a named volume)
docker compose up --build

# Create the first admin inside the container (prompts for the password)
docker compose exec web python scripts/init_db.py --create-admin
```

`SECRET_KEY` and other secrets are passed in via the environment (see `docker-compose.yml` /
`.env`) — nothing sensitive is baked into the image.

---

## Deploy to Vercel + Neon (serverless)

The same app factory also runs as a WSGI serverless function. `api/index.py`
exports the Flask app and `vercel.json` routes every path to it, so no code
change is needed — only a real database, because a serverless filesystem is
read-only and ephemeral (SQLite will not work).

### 1. Create the database (Neon)

Create a Postgres project in the Neon console and copy the connection string
(it looks like `postgres://user:password@ep-xxx.aws.neon.tech/neondb?sslmode=require`).

Create the schema and the first admin from your machine:

```bash
DATABASE_URL="<neon connection string>" \
ADMIN_EMAIL="you@example.com" ADMIN_NAME="Your Name" \
ADMIN_PASSWORD="<a strong password>" \
python scripts/init_db.py --create-admin
```

`init_db.py` is idempotent, so it is safe to re-run. The app also creates
missing tables on first boot (`_ensure_database()`), so this step is only
required to bootstrap the admin account.

### 2. Deploy the app (Vercel)

Push the repository to GitHub, then import it in Vercel (Framework preset
"Other" — the `builds` array in `vercel.json` is already configured).

Set these environment variables for the Production scope:

| Variable | Value |
| --- | --- |
| `SECRET_KEY` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | the Neon connection string |
| `SESSION_COOKIE_SECURE` | `1` |
| `REMEMBER_COOKIE_SECURE` | `1` |

`FLASK_ENV` does **not** need to be set: `api/index.py` forces `production`
(which refuses to start without a `SECRET_KEY`) before the config loads.

### Notes and limits

- **Cold starts.** scikit-learn + pandas + scipy make the function bundle
  sizeable (~200 MB unpacked). Keep an eye on Vercel's function size limit;
  if you hit it, drop to a container host (the provided `Dockerfile` works
  as-is) and keep Neon for the data.
- **Pinned ML versions.** `requirements.txt` pins `scikit-learn`/`numpy`/
  `pandas`/`scipy` to the exact versions that produced the committed model
  pickles. Bump them together with `ml/train_*.py`.
- **No file uploads persist.** Anything the app writes to disk is lost when
  the function recycles; only the database is durable.

---

## Project structure

```
DataBreakers-CRM/
├── app/
│   ├── __init__.py          # Application factory, blueprint & error-handler registration
│   ├── extensions.py        # db, login_manager, csrf instances
│   ├── models/              # User, Lead, Customer, FollowUp, Sale, Product, Notification, ...
│   ├── auth/ dashboard/ leads/ customers/ followups/ sales/ products/
│   ├── analytics/ predictions/ assistant/ reports/ notifications/ admin/
│   ├── services/            # Shared business logic (scoped queries, aggregations)
│   ├── utils/               # Permission/scoping helpers (_scope_user, visible_*_query)
│   ├── templates/           # Jinja2 templates (base, macros, per-module pages, errors)
│   └── static/              # crm.css, crm.js, images/databreakers-logo.png
├── ml/
│   ├── preprocessing.py     # Feature building, leakage-safe splits
│   ├── train_lead_model.py  # Lead conversion classifier
│   ├── train_sales_model.py # Sales forecast regressor
│   ├── evaluate_models.py   # Honest metrics reporting
│   ├── datasets/            # Generated sample CSVs
│   └── artifacts/           # Trained model + metrics (git-ignored)
├── scripts/
│   ├── init_db.py           # Schema creation + admin bootstrap (no hardcoded password)
│   ├── seed_demo_data.py    # Realistic demo data
│   └── generate_samples.py  # Synthetic training datasets
├── tests/                   # pytest suite
├── docs/                    # Analytics formulas, ML methodology, security notes
├── config.py                # Development / Testing / Production configs (env-driven)
├── app.py                   # Entry point
├── Dockerfile
├── docker-compose.yml
└── .env.example             # Documented environment template (no real secrets)
```

---

## Configuration & secrets

All configuration is read from environment variables (optionally loaded from `.env`):

| Variable | Purpose | Default |
| --- | --- | --- |
| `SECRET_KEY` | Flask session signing key (**required in production**) | — |
| `DATABASE_URL` | SQLAlchemy database URI | `sqlite:///databreakers_crm.db` |
| `FLASK_ENV` | `development` / `testing` / `production` | `development` |
| `SESSION_COOKIE_SECURE` | Set `1` behind HTTPS | `0` |
| `ZIA_LLM_API_KEY` | Optional LLM key for Zia's wording layer (server-side only) | empty |
| `ZIA_LLM_BASE_URL` / `ZIA_LLM_MODEL` / `ZIA_LLM_TIMEOUT_SECONDS` | LLM provider settings | OpenAI / `gpt-4o-mini` / `30` |
| `SMTP_HOST` | SMTP server for the password-reset code (empty disables mail) | empty |
| `SMTP_PORT` | `587` for STARTTLS, `465` for implicit SSL | `587` |
| `SMTP_USER` / `SMTP_PASSWORD` | SMTP credentials (**use an app password, never your account password**) | empty |
| `MAIL_FROM` | The From: header; falls back to `SMTP_USER` | `SMTP_USER` |
| `MAIL_TIMEOUT` | Socket timeout in seconds, so a slow provider cannot hang a request | `10` |
| `PASSWORD_RESET_OTP_TTL_MINUTES` / `PASSWORD_RESET_MAX_ATTEMPTS` / `PASSWORD_RESET_COOLDOWN_SECONDS` / `PASSWORD_RESET_WINDOW_MINUTES` | Reset-code lifetime, guess budget, resend throttle, and how long a verified code unlocks the password form | `10` / `5` / `60` / `15` |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` / `ADMIN_NAME` | Read by `init_db.py --create-admin` | prompted |

**Security rules enforced in this project**

- No hardcoded admin password — the bootstrap account is created interactively or from env.
- No plaintext passwords — hashed with Werkzeug's `generate_password_hash`. Reset codes are
  hashed the same way, so the database never holds a usable code.
- No API keys in the frontend — Zia's LLM key is read only on the server, and the local
  deterministic engine is the default so the app works with no key at all. The SMTP password
  likewise stays in the environment and is never logged.
- Server-side permission checks on every mutating route; CSV/Excel exports re-check scope.

---

## Password reset (email one-time code)

The login page links to **Forgot password?**, which follows the same three steps as a
consumer account:

1. **Enter your email** — `/auth/forgot-password`
2. **Enter the 6-digit code** — `/auth/verify-otp`, with a throttled "send a new code" option
3. **Choose a new password** — `/auth/reset-password`

Nothing about the account is revealed to the caller. Step 1 answers with the same wording
whether or not the address exists, wrong/expired/exhausted codes all produce one identical
message, and the cooldown a caller sees comes only from their own browser session. Delivery
problems are written to the audit log rather than shown, because a message that appears only
for real accounts would turn the form into an account-enumeration oracle. The single
exception is SMTP being unconfigured, which is a property of the deployment and is stated
plainly.

Enforced server-side: codes are 6 digits from `secrets` (not `random`), hashed at rest,
single-use, expire after 10 minutes, are capped at 5 attempts so 1e6 possibilities cannot be
brute-forced, and only one code per account is ever live — requesting again burns the old
one. The verified code unlocks the password form for 15 minutes via the session, and that
proof is dropped as soon as a password is set.

### Recovery when email is not configured

An administrator can reset any account directly, with no email involved: **User Management →
Edit → Password**. It applies the same composition rules a user meets when changing their own
password, so an admin cannot assign a weaker one, and it is recorded as its own
`user_password_reset` audit action rather than folded into ordinary profile edits. Tell the
user the new password out of band and have them change it from **Profile** afterwards.

### Configuring mail

Without `SMTP_HOST` the feature reports itself as unconfigured instead of pretending to send.
For Gmail, Google will not accept your account password — create an **app password**
(*Google Account → Security → 2-Step Verification → App passwords*), then set:

```bash
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=<the 16-character app password>
MAIL_FROM=you@gmail.com
```

The mailer uses only the standard library, so there is no extra dependency and no third-party
API key. Every send is bounded by `MAIL_TIMEOUT` and fails soft: an unreachable provider
records an audit entry instead of failing the request.

---

## Documentation

- [`docs/ANALYTICS.md`](docs/ANALYTICS.md) — every analytics metric with its exact formula
- [`docs/ML.md`](docs/ML.md) — model methodology, train/test split, leakage prevention, metrics honesty
- [`docs/SECURITY.md`](docs/SECURITY.md) — roles, permissions, secrets handling
- [`docs/TESTING.md`](docs/TESTING.md) — how to run and extend the test suite

---

## Known limitations

- SQLite is the default store; for concurrent multi-user production use set `DATABASE_URL`
  to PostgreSQL/MySQL and install the matching driver.
- The ML models ship untrained until you run the training scripts (or use the sample CSVs) —
  the UI clearly shows "Model not trained" rather than a fabricated accuracy figure.
- Zia's local engine answers from the built-in metric intents; the LLM layer only rewords
  verified facts and is optional.
