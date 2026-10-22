"""Smoke-test the deployed DataBreakers CRM.

Logs in as the bootstrap admin and walks every sidebar route, asserting each
one renders (HTTP 200, no traceback) and that the DataBreakers logo is on the
page. Used to verify a Vercel + Neon deployment end to end.

    python scripts/verify_deployment.py [base_url]
"""
from __future__ import annotations

import http.cookiejar
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

CSRF_RE = re.compile(r'id="csrf_token"[^>]*value="([^"]+)"')

ROUTES = [
    "/", "/leads/", "/customers/", "/followups/", "/sales/", "/products/",
    "/analytics/", "/predictions/", "/zia/", "/reports/", "/notifications/",
    "/admin/", "/admin/users", "/admin/settings", "/activity", "/auth/profile",
]


def main() -> int:
    """Read the base URL from argv, credentials from the environment."""
    base = (sys.argv[1] if len(sys.argv) > 1
            else os.environ.get("DEPLOY_URL", "https://databreakers-crm.vercel.app")).rstrip("/")
    email = os.environ.get("ADMIN_EMAIL", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not email or not password:
        print("Set ADMIN_EMAIL and ADMIN_PASSWORD in the environment; "
              "this script never stores or hardcodes credentials.")
        return 2

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def get(path: str) -> tuple[int, str]:
        try:
            with opener.open(base + path, timeout=60) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    status, login_html = get("/auth/login")
    m = CSRF_RE.search(login_html)
    if status != 200 or not m:
        print(f"FAIL: could not load login page (status={status}, csrf={'yes' if m else 'no'})")
        return 1

    payload = urllib.parse.urlencode({
        "csrf_token": m.group(1),
        "email": email,
        "password": password,
        "submit": "Sign in",
    }).encode()
    req = urllib.request.Request(base + "/auth/login", data=payload,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with opener.open(req, timeout=60) as resp:
            status, after_login = resp.status, resp.read().decode("utf-8", "replace")
            final_url = resp.geturl()
    except urllib.error.HTTPError as exc:
        status, after_login = exc.code, exc.read().decode("utf-8", "replace")
        final_url = base + "/auth/login"

    flashes = re.findall(r'data-flash="([^"]+)"', after_login)
    if final_url.rstrip("/").endswith("auth/login") or flashes:
        print(f"FAIL: login rejected (status={status}, final={final_url}, flashes={flashes})")
        return 1
    print(f"login ok -> {final_url}")

    failures: list[str] = []
    for path in ROUTES:
        status, body = get(path)
        problems = []
        if status != 200:
            problems.append(f"HTTP {status}")
        if "databreakers-logo" not in body:
            problems.append("logo missing")
        if "Internal Server Error" in body or "Traceback" in body:
            problems.append("traceback in page")
        if problems:
            failures.append(f"{path}: {', '.join(problems)}")
        print(f"  {path:<18} {status}  {len(body):>7}B  "
              f"{'OK' if not problems else '; '.join(problems)}")

    print(f"\n{len(ROUTES) - len(failures)}/{len(ROUTES)} routes ok")
    if failures:
        print("FAILED:")
        for f in failures:
            print(f"  {f}")
        return 1
    print("all routes rendered with the logo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
