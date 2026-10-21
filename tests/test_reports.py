"""Tests for reports, CSV export and permission scoping of exports."""
from __future__ import annotations


def test_reports_page_renders(admin_client):
    resp = admin_client.get("/reports/")
    assert resp.status_code == 200
    assert b"Reports" in resp.data


def test_report_type_parameter_validated(admin_client):
    """An unknown report type must fall back safely, never 500."""
    resp = admin_client.get("/reports/?type=does-not-exist")
    assert resp.status_code == 200
    assert b"Leads" in resp.data


def test_report_date_range_validated(admin_client):
    """start > end must warn instead of erroring."""
    resp = admin_client.get("/reports/?type=leads&start=2026-12-31&end=2026-01-01")
    assert resp.status_code == 200


def test_report_includes_seeded_lead(admin_client, lead):
    resp = admin_client.get("/reports/?type=leads")
    assert b"Test Prospect" in resp.data


def test_report_monthly_renders(admin_client):
    resp = admin_client.get("/reports/monthly")
    assert resp.status_code == 200


def test_leads_csv_export(admin_client, lead):
    resp = admin_client.get("/reports/export.csv?type=leads")
    assert resp.status_code == 200
    assert resp.mimetype.startswith("text/csv")
    body = resp.data.decode("utf-8-sig")
    assert "Test Prospect" in body
    assert "Disposition" not in body  # sanity: real header row
    assert "Name" in body.splitlines()[0]


def test_sales_csv_export(admin_client, sale):
    resp = admin_client.get("/reports/export.csv?type=sales")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8-sig")
    assert "Counted Revenue" in body.splitlines()[0]


def test_followups_csv_export(admin_client, followup):
    resp = admin_client.get("/reports/export.csv?type=followups")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8-sig")
    assert "Subject" in body.splitlines()[0]


def test_conversion_csv_export(admin_client, lead):
    resp = admin_client.get("/reports/export.csv?type=conversion")
    assert resp.status_code == 200


def test_csv_export_unknown_type_redirects(admin_client):
    resp = admin_client.get("/reports/export.csv?type=bogus")
    assert resp.status_code == 302


def test_csv_export_respects_permissions(employee_client, lead,
                                         other_employee_lead):
    """An employee's export must not contain other people's leads."""
    resp = employee_client.get("/reports/export.csv?type=leads")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8-sig")
    assert "Test Prospect" in body
    assert "Foreign Lead" not in body


def test_empty_csv_export_informs_instead_of_breaking(admin_client):
    resp = admin_client.get("/reports/export.csv?type=leads",
                            follow_redirects=True)
    assert resp.status_code == 200
    assert b"No rows to export" in resp.data


def test_sales_export_button_available(admin_client):
    resp = admin_client.get("/sales/")
    assert b"export" in resp.data.lower() or b"CSV" in resp.data


def test_excel_export_graceful_without_openpyxl(admin_client, lead):
    """Excel export must either succeed or flash a clear install hint."""
    resp = admin_client.get("/reports/export.xlsx?type=leads", follow_redirects=True)
    assert resp.status_code == 200
    # Two acceptable outcomes: a file, or a helpful message. Never a 500.
    if resp.mimetype == "text/html":
        assert (b"openpyxl" in resp.data) or (b"export" in resp.data.lower())
