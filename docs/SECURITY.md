# Security notes

## Roles

Two roles exist in `app/models/__init__.py → UserRole`:

| Role | Access |
| --- | --- |
| `admin` | Everything, plus User Management, admin panel, all-scope reports and exports, audit log. |
| `employee` | Own leads/customers/follow-ups/sales only (scope enforced server side). No user administration, no global exports. |

Enforcement is **server side only** — hiding a button in the UI is never the control.

## Access-control decorators

Defined in `app/utils/__init__.py`:

- `login_required` (Flask-Login) — anonymous users are redirected to login.
- `active_required` — deactivated accounts are logged out and refused with a flash message,
  even if their session cookie is still valid.
- `admin_required` — non-admin users get 403, including for every `admin` blueprint route
  and for admin-only CSV exports.

## Data scoping (horizontal permission)

`visible_lead_query()`, `visible_customer_query()`, `visible_sale_query()` and
`visible_followup_query()` return the query already filtered to the current user, with admins
bypassing the filter. Every list, detail, edit, delete and export path builds on these
helpers, so an employee cannot read or mutate another user's records by changing an ID in
the URL. Detail routes combine `visible_*_query()` with `first_or_404()`, which returns 404
(not 403) for records outside the caller's scope — existence is not leaked.

## Passwords

- Hashed with `werkzeug.security.generate_password_hash` (scrypt by default) in
  `User.set_password()`; verified with `check_password_hash` in `User.check_password()`.
- Passwords are **never** stored, logged, or returned by any route.
- The bootstrap admin is created by `scripts/init_db.py --create-admin`, which reads
  `ADMIN_PASSWORD` from the environment or prompts interactively. **No hardcoded admin
  password exists anywhere in the repository**, and the script refuses an empty/weak
  password.

## Secrets

- All configuration comes from environment variables, optionally loaded from `.env`
  (git-ignored; only `.env.example` with empty placeholders is committed).
- `SECRET_KEY` is required; production config raises at startup if it is missing.
- **No API key is ever sent to the browser.** Zia's LLM key (`ZIA_LLM_API_KEY`) is read on
  the server inside `_call_llm()`; templates, JS and CSVs never see it. With no key
  configured, Zia's local deterministic engine answers everything.

## CSRF, sessions, headers

- CSRF protection via Flask-WTF (`WTF_CSRF_ENABLED`); `hidden_tag()` renders the token on
  every form. Disabled only under the `testing` config so tests can POST without tokens.
- Session cookie: `HttpOnly` (default on), `SameSite=Lax` (default), `Secure` configurable
  via `SESSION_COOKIE_SECURE=1` for HTTPS deployments.
- Session lifetime and CSRF time limit configurable via env
  (`SESSION_LIFETIME_HOURS`, `WTF_CSRF_TIME_LIMIT`).
- All form validation is server side (Flask-WTF `validate_on_submit`), including email
  format (email-validator) and required fields.

## Input & export handling

- SQLAlchemy is used with parameter binding everywhere; no raw SQL string formatting.
- Date/integer query parameters are parsed with Flask's typed `request.args.get(..., type=)`
  and clamped to sane ranges (e.g. forecast months 1–24).
- CSV/Excel exports re-check role and scope before writing a single row; employees can only
  export their own data. Excel export degrades to CSV if `openpyxl` is absent rather than
  failing open.

## Audit trail

`log_audit()` and `log_activity()` record administrative and record-level actions
(action, entity, entity id, actor, timestamp) in the audit/activity log, visible to admins
in the admin panel.
