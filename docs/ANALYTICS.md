# Analytics formulas

Every metric shown in the **Analytics** module, the dashboard KPI cards and Zia's answers is
computed from live database rows. The canonical source of these formulas in code is
`AnalyticsService.METRIC_DOCS` in `app/services/__init__.py` — this document mirrors it so
the numbers on screen can always be reproduced by hand.

## Funnel & conversion

| Metric | Formula | Notes |
| --- | --- | --- |
| Total leads | `COUNT(lead)` | All leads in scope of the current user (admins see all). |
| Closed leads | `converted + lost` | Only *decided* leads; open pipeline is excluded. |
| **Conversion rate** | `converted / (converted + lost) × 100` | Rounded to 1 decimal. `0.0` when nothing is decided — never a guessed value. |
| **Acquisition rate** | `converted / total_leads × 100` | Includes open leads in the denominator (lower by design). |
| Open leads | `total_leads − converted − lost` | Pipeline still in flight. |

Why two rates: conversion rate measures how well *decided* deals are won, acquisition rate
measures yield across everything ever created. Open leads dilute only the second.

## Lead sources

| Metric | Formula |
| --- | --- |
| **Win rate by source** | `converted leads from source / all leads from source × 100` |

Sorted by win rate descending, then volume. Sources with zero leads are omitted, not shown
as `0%`.

## Customers & retention

| Metric | Formula |
| --- | --- |
| **Retention rate** | `customers with ≥1 completed sale in the last 365 days / total customers × 100` |
| Acquisition trend | count of customers created per month (cumulative) |
| **CLV (customer lifetime value)** | `SUM(amount × quantity)` of that customer's completed sales |
| **Avg deal value** | `SUM(revenue) / COUNT(completed sales)` |

### CLV segments

Segment bands used by the segment analysis table:

| Segment | CLV |
| --- | --- |
| Platinum | ≥ 25,000 |
| Gold | ≥ 10,000 |
| Silver | ≥ 2,000 |
| Bronze | < 2,000 |

## Revenue

| Metric | Formula |
| --- | --- |
| **Revenue** | `SUM(amount × quantity)` of sales with status **Completed** |
| Revenue by product | revenue grouped by `product_id` |
| Revenue by employee | revenue grouped by `recorded_by` |
| Monthly revenue trend | revenue grouped by calendar month (last N months) |

**Expected deal value is not revenue.** A lead's `expected_value` is pipeline forecasting
only; it is shown as "expected pipeline" on the dashboard and never added to any revenue
figure. Only recorded, completed sales produce revenue.

## Engagement

| Metric | Formula |
| --- | --- |
| **Engagement score** | interactions + follow-ups per customer in the selected period |

Customers are banded High / Medium / Low by their score within the period (default 6
months). Follow-ups are counted from `FollowUp` rows linked to the customer's lead.

## Date-range reports (Pandas)

The Reports module builds DataFrames with `AnalyticsService.leads/customers/sales/followups/
conversion/monthly_analytics(start, end)` — each filters to the requested window first, then
aggregates, so partial periods are never blended with out-of-range rows. CSV exports contain
exactly the columns displayed on screen.

## Empty-data behaviour

With no rows in scope, every rate returns `0.0` (not a division error, not a fabricated
sample value) and tables render an explicit "No data" state. Zia reports
*"No leads exist yet, so conversion metrics are all zero"* and still explains the formula.
