"""Zia AI business assistant.

Two tiers, always honest about which one answered:

1. **Local deterministic engine** (always available). It computes the answer
   from real CRM data using the same services the dashboard uses, then
   renders it as text. No LLM involved, no invented numbers.
2. **Optional LLM wording layer**. If ``ZIA_LLM_API_KEY`` is configured the
   numbers are handed to a language model *to phrase*, never to compute. If
   the call fails, we say so and fall back to the local answer.

Permission rule: every number is computed from the *scoped* queries in
``app.utils`` so an employee never sees another employee's restricted data.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from flask import current_app, url_for
from flask_login import current_user

from app.extensions import db
from app.models import (Customer, FollowUp, FollowUpStatus, Lead, LeadStatus,
                        Sale, SaleStatus)
from app.predictions import service as prediction_service
from app.services import AnalyticsService, DashboardService, FollowUpService
from app.utils import (last_n_months, visible_customer_query,
                       visible_followup_query, visible_lead_query,
                       visible_sale_query)


@dataclass
class ZiaAnswer:
    """One response from the assistant."""

    question: str
    answer: str
    source: str                      # "local" | "llm" | "llm-fallback"
    numbers: dict[str, Any] = field(default_factory=dict)
    llm_available: bool = False
    llm_error: str = ""
    permission_note: str = ""


# ---------------------------------------------------------------------------
# Capability discovery (tells the UI what Zia can do)
# ---------------------------------------------------------------------------
CAPABILITIES = [
    "Which leads need follow-up today?",
    "How many overdue follow-ups do I have?",
    "Summarize this month's sales.",
    "Which lead sources perform best?",
    "Explain the lead conversion metrics.",
    "Summarize customer activity.",
    "Explain prediction results.",
    "What is my total revenue?",
    "How many leads do I have?",
    "Show my top customers.",
]


# ---------------------------------------------------------------------------
# Intent routing
# ---------------------------------------------------------------------------
def _detect_intent(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ("follow-up today", "follow up today", "due today", "today's follow")):
        return "followups_today"
    if any(k in t for k in ("overdue", "missed follow")):
        return "followups_overdue"
    if any(k in t for k in ("this month's sales", "months sales", "monthly sales",
                            "sales summary", "summarize this month")):
        return "sales_month"
    if any(k in t for k in ("lead source", "sources perform", "best source", "source performance")):
        return "lead_sources"
    if any(k in t for k in ("conversion metric", "conversion rate", "explain conversion",
                            "convert rate")):
        return "conversion"
    if any(k in t for k in ("customer activity", "summarize customer", "customer summary")):
        return "customers"
    if any(k in t for k in ("prediction", "forecast", "model", "probability")):
        return "predictions"
    if any(k in t for k in ("total revenue", "revenue", "sales total")):
        return "revenue"
    if any(k in t for k in ("how many leads", "lead count", "number of leads")):
        return "lead_count"
    if any(k in t for k in ("top customer", "best customer", "highest value customer")):
        return "top_customers"
    return "help"


# ---------------------------------------------------------------------------
# Local deterministic answerers
# ---------------------------------------------------------------------------
def _fmt_money(v: float) -> str:
    return f"{v:,.2f}"


def _answer_followups_today() -> ZiaAnswer:
    buckets = FollowUpService.buckets()
    rows = buckets["today"]
    if not rows:
        body = "You have no follow-ups scheduled for today."
    else:
        lines = [f"- {f.kind} with {f.subject} at {f.due_at:%H:%M} "
                 f"(assigned to {f.assignee.name if f.assignee else 'unassigned'})"
                 for f in rows[:10]]
        body = (f"You have {len(rows)} follow-up(s) due today:\n" + "\n".join(lines))
        if len(rows) > 10:
            body += f"\n…and {len(rows) - 10} more."
    return ZiaAnswer(question="Which leads need follow-up today?", answer=body,
                     source="local", numbers={"due_today": len(rows)})


def _answer_followups_overdue() -> ZiaAnswer:
    rows = FollowUpService.buckets()["overdue"]
    if not rows:
        body = "Nothing is overdue - your follow-up queue is clear."
    else:
        lines = [f"- {f.kind} with {f.subject}, was due {f.due_at:%Y-%m-%d %H:%M}"
                 for f in rows[:10]]
        body = (f"You have {len(rows)} overdue follow-up(s):\n" + "\n".join(lines))
    return ZiaAnswer(question="How many overdue follow-ups do I have?", answer=body,
                     source="local", numbers={"overdue": len(rows)})


def _answer_sales_month() -> ZiaAnswer:
    now = datetime.utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    sales = visible_sale_query().filter(
        Sale.status == SaleStatus.COMPLETED.value,
        Sale.sold_at >= start).all()
    total = sum(s.total for s in sales)
    prev_start = (start - timedelta(days=1)).replace(day=1)
    prev_total = sum(s.total for s in visible_sale_query().filter(
        Sale.status == SaleStatus.COMPLETED.value,
        Sale.sold_at >= prev_start, Sale.sold_at < start).all())
    delta = round((total - prev_total) / prev_total * 100, 1) if prev_total else None

    by_product: dict[str, float] = {}
    for s in sales:
        key = s.product.name if s.product else "Unassigned"
        by_product[key] = by_product.get(key, 0.0) + s.total
    top = sorted(by_product.items(), key=lambda kv: -kv[1])[:5]

    parts = [
        f"{now:%B %Y} sales summary",
        f"Completed transactions: {len(sales)}",
        f"Revenue: {_fmt_money(total)}",
    ]
    if delta is not None:
        direction = "up" if delta >= 0 else "down"
        parts.append(f"vs previous month: {direction} {abs(delta)}% "
                     f"(previous {_fmt_money(prev_total)})")
    if top:
        parts.append("Top products: " +
                     ", ".join(f"{k} ({_fmt_money(v)})" for k, v in top))
    if not sales:
        parts.append("No completed sales recorded this month yet.")
    return ZiaAnswer(question="Summarize this month's sales.",
                     answer="\n".join(f"- {p}" for p in parts), source="local",
                     numbers={"revenue": round(total, 2), "transactions": len(sales),
                              "previous_month": round(prev_total, 2),
                              "wow_change_pct": delta})


def _answer_lead_sources() -> ZiaAnswer:
    data = AnalyticsService.source_performance()
    if data["empty"]:
        return ZiaAnswer(question="Which lead sources perform best?",
                         answer="There are no leads yet, so source performance "
                                "cannot be computed.", source="local",
                         numbers={"sources": 0})
    top = data["records"][:5]
    lines = [f"- {r['Source']}: {r['Leads']} leads, {r['Converted']} converted, "
             f"{r['Win rate %']}% win rate" for r in top]
    best = top[0]
    body = ("Best performing lead sources (by win rate = converted / total leads):\n"
            + "\n".join(lines)
            + f"\n\nOverall winner: {best['Source']} at {best['Win rate %']}%.")
    return ZiaAnswer(question="Which lead sources perform best?", answer=body,
                     source="local",
                     numbers={"sources": len(data["records"]),
                              "best": best["Source"],
                              "best_win_rate": best["Win rate %"]})


def _answer_conversion() -> ZiaAnswer:
    o = AnalyticsService.overview()
    body = (
        "Lead conversion metrics explained:\n"
        f"- Total leads: {o['total_leads']}\n"
        f"- Converted: {o['converted']}  |  Lost: {o['lost']}  |  Open: {o['open']}\n"
        f"- Conversion rate = converted / (converted + lost) x 100 = "
        f"{o['conversion_rate']}%\n"
        f"- Acquisition rate = converted / all leads x 100 = {o['acquisition_rate']}%\n\n"
        "The conversion rate only counts *decided* leads (converted or lost). "
        "Open leads are excluded so the figure is not diluted while deals are "
        "still in flight."
    )
    if o["total_leads"] == 0:
        body = (
            "No leads exist yet, so conversion metrics are all zero.\n"
            "For reference, once data exists:\n"
            "- Conversion rate = converted / (converted + lost) x 100 "
            "(only decided leads count)\n"
            "- Acquisition rate = converted / all leads x 100"
        )
    return ZiaAnswer(question="Explain the lead conversion metrics.", answer=body,
                     source="local", numbers=o)


def _answer_customers() -> ZiaAnswer:
    total = visible_customer_query().count()
    if total == 0:
        return ZiaAnswer(question="Summarize customer activity.",
                         answer="No customers exist yet.", source="local",
                         numbers={"customers": 0})
    cut = datetime.utcnow() - timedelta(days=30)
    new_this_month = visible_customer_query().filter(
        Customer.created_at >= cut.replace(day=1)).count()
    engagement = AnalyticsService.engagement(3)
    active = [r for r in engagement["records"] if r["Engagement"] != "Low"][:5]
    retention = AnalyticsService.retention()
    lines = [
        f"- Total customers: {total}",
        f"- New in the last 30 days: {new_this_month}",
        f"- Retention rate: {retention['retention_rate']}% "
        f"({retention['active_customers']} active of {total})",
    ]
    if active:
        lines.append("- Most engaged: " + ", ".join(r["Customer"] for r in active))
    return ZiaAnswer(question="Summarize customer activity.",
                     answer="\n".join(lines), source="local",
                     numbers={"customers": total, "new_30d": new_this_month,
                              "retention_rate": retention["retention_rate"]})


def _answer_predictions() -> ZiaAnswer:
    lead_status = prediction_service.lead_model_status()
    sales_status = prediction_service.sales_model_status()
    lines = []
    if lead_status.available:
        m = lead_status.metrics
        lines.append(f"- Lead conversion model: trained {lead_status.trained_at} "
                     f"using {lead_status.method} on {lead_status.n_training_rows} rows.")
        if m:
            lines.append("  Test metrics: " +
                         ", ".join(f"{k}={v}" for k, v in m.items()))
    else:
        lines.append(f"- Lead conversion model: NOT TRAINED. {lead_status.message}")
    if sales_status.available:
        m = sales_status.metrics
        lines.append(f"- Sales forecast model: trained {sales_status.trained_at} "
                     f"({sales_status.method}, {sales_status.n_training_rows} periods).")
        if m:
            lines.append("  Test metrics: " +
                         ", ".join(f"{k}={v}" for k, v in m.items()))
    else:
        lines.append(f"- Sales forecast model: NOT TRAINED. {sales_status.message}")
    lines.append("\nThese figures come from the saved evaluation report written "
                 "during training - they are not estimated here.")
    return ZiaAnswer(question="Explain prediction results.", answer="\n".join(lines),
                     source="local",
                     numbers={"lead_model": lead_status.available,
                              "sales_model": sales_status.available})


def _answer_revenue() -> ZiaAnswer:
    total = sum(s.total for s in visible_sale_query()
                .filter_by(status=SaleStatus.COMPLETED.value).all())
    return ZiaAnswer(question="What is my total revenue?",
                     answer=f"Total completed revenue: {_fmt_money(total)}. "
                            "Pending and refunded sales are excluded.",
                     source="local", numbers={"revenue": round(total, 2)})


def _answer_lead_count() -> ZiaAnswer:
    rows = (visible_lead_query().with_entities(Lead.status, db.func.count(Lead.id))
            .group_by(Lead.status).all())
    total = sum(n for _, n in rows)
    if not rows:
        body = "There are no leads visible to you."
    else:
        body = f"You have {total} lead(s):\n" + "\n".join(
            f"- {s}: {n}" for s, n in sorted(rows))
    return ZiaAnswer(question="How many leads do I have?", answer=body,
                     source="local", numbers={"total": total})


def _answer_top_customers() -> ZiaAnswer:
    customers = visible_customer_query().all()
    if not customers:
        return ZiaAnswer(question="Show my top customers.",
                         answer="No customers yet.", source="local",
                         numbers={"customers": 0})
    ranked = sorted(customers, key=lambda c: -c.lifetime_value)[:5]
    lines = [f"- {c.name} ({c.company or 'individual'}): {_fmt_money(c.lifetime_value)} "
             f"over {c.total_purchases} purchase(s)" for c in ranked]
    return ZiaAnswer(question="Show my top customers.",
                     answer="Top customers by lifetime value:\n" + "\n".join(lines),
                     source="local",
                     numbers={c.name: round(c.lifetime_value, 2) for c in ranked})


def _answer_help() -> ZiaAnswer:
    return ZiaAnswer(
        question="(help)",
        answer="I can answer questions from your CRM data. Try one of these:\n" +
               "\n".join(f"- {c}" for c in CAPABILITIES),
        source="local", numbers={})


LOCAL_HANDLERS = {
    "followups_today": _answer_followups_today,
    "followups_overdue": _answer_followups_overdue,
    "sales_month": _answer_sales_month,
    "lead_sources": _answer_lead_sources,
    "conversion": _answer_conversion,
    "customers": _answer_customers,
    "predictions": _answer_predictions,
    "revenue": _answer_revenue,
    "lead_count": _answer_lead_count,
    "top_customers": _answer_top_customers,
    "help": _answer_help,
}


# ---------------------------------------------------------------------------
# LLM wording layer (optional, never invents numbers)
# ---------------------------------------------------------------------------
def llm_configured() -> bool:
    # The admin "Enable Zia LLM" switch is persisted in app_settings and
    # loaded into config at startup; turning it off disables the LLM layer
    # without deleting the key.
    if current_app.config.get("ZIA_LLM_ENABLED") is False:
        return False
    key = current_app.config.get("ZIA_LLM_API_KEY") or os.environ.get("ZIA_LLM_API_KEY", "")
    return bool(key.strip())


def _call_llm(question: str, facts: str) -> tuple[str, str]:
    """Return (text, error). On any failure error is non-empty."""
    try:
        import urllib.error
        import urllib.request
        import json as _json

        api_key = (current_app.config.get("ZIA_LLM_API_KEY")
                   or os.environ.get("ZIA_LLM_API_KEY", "")).strip()
        base_url = (current_app.config.get("ZIA_LLM_BASE_URL")
                    or "https://api.openai.com/v1").rstrip("/")
        model = current_app.config.get("ZIA_LLM_MODEL", "gpt-4o-mini")
        timeout = int(current_app.config.get("ZIA_LLM_TIMEOUT_SECONDS", 30))

        system = (
            "You are Zia AI, the assistant inside the DataBreakers CRM. "
            "You are given verified numbers computed from the database. "
            "Use ONLY those numbers. Never invent, round up, or guess a value. "
            "If the facts do not answer the question, say so plainly. "
            "Keep the answer short and professional."
        )
        payload = _json.dumps({
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content":
                          f"Question: {question}\n\nVerified facts:\n{facts}"}],
            "temperature": 0.2,
            "max_tokens": 400,
        }).encode("utf-8")

        request_obj = urllib.request.Request(
            f"{base_url}/chat/completions", data=payload, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(request_obj, timeout=timeout) as resp:
            body = _json.loads(resp.read().decode("utf-8"))
        text = body["choices"][0]["message"]["content"].strip()
        if not text:
            return "", "The provider returned an empty response."
        return text, ""
    except urllib.error.HTTPError as exc:
        return "", f"Provider returned HTTP {exc.code}."
    except Exception as exc:  # network, timeout, malformed JSON
        return "", f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def ask(question: str, use_llm: bool = True) -> ZiaAnswer:
    """Answer a business question from scoped CRM data."""
    question = (question or "").strip()
    intent = _detect_intent(question)
    handler = LOCAL_HANDLERS.get(intent, _answer_help)
    local = handler()
    local.question = question or local.question

    if not llm_configured() or not use_llm:
        local.llm_available = False
        if intent == "help":
            return local
        local.answer += ("\n\n(configured with the built-in analytics engine - "
                         "set ZIA_LLM_API_KEY to enable AI wording)")
        return local

    facts = f"Question: {question}\nVerified facts:\n{local.answer}"
    text, error = _call_llm(question, facts)
    if text:
        return ZiaAnswer(question=question, answer=text, source="llm",
                         numbers=local.numbers, llm_available=True)
    # LLM was asked but failed - never pretend it worked.
    local.source = "llm-fallback"
    local.llm_available = True
    local.llm_error = error
    local.answer = (f"(The AI provider could not be reached: {error}. "
                    "Showing the built-in analytics answer instead.)\n\n" + local.answer)
    return local
