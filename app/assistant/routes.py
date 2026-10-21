"""Zia AI assistant blueprint: chat UI + JSON API."""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required
from flask_wtf import FlaskForm
from wtforms import SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length

from app.assistant import zia
from app.utils import active_required, log_activity

bp = Blueprint("assistant", __name__, url_prefix="/zia")


class QuestionForm(FlaskForm):
    question = TextAreaField("Ask Zia AI",
                             validators=[DataRequired(), Length(min=2, max=500)])
    submit = SubmitField("Ask")


@bp.route("/", methods=["GET", "POST"])
@login_required
@active_required
def index():
    form = QuestionForm()
    answer = None
    if form.validate_on_submit():
        answer = zia.ask(form.question.data)
        log_activity("zia_asked", f"Zia asked: {form.question.data[:80]}",
                     entity_type="Assistant")
    elif request.method == "POST":
        answer = zia.ZiaAnswer(question="", answer="Please type a question.",
                               source="local")
    return render_template("assistant/index.html", title="Zia AI",
                           form=form, answer=answer,
                           capabilities=zia.CAPABILITIES,
                           llm_configured=zia.llm_configured())


@bp.route("/ask", methods=["POST"])
@login_required
@active_required
def ask_api():
    """JSON endpoint used by the header search / quick-ask widget."""
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or request.form.get("question") or "").strip()
    if len(question) < 2:
        return jsonify({"error": "Please ask a question of at least 2 characters."}), 400
    answer = zia.ask(question)
    return jsonify({
        "question": answer.question,
        "answer": answer.answer,
        "source": answer.source,
        "numbers": answer.numbers,
        "llm_error": answer.llm_error,
    })


@bp.route("/status")
@login_required
@active_required
def status():
    return {
        "llm_configured": zia.llm_configured(),
        "capabilities": zia.CAPABILITIES,
        "note": ("An LLM provider is configured; Zia uses it to word answers "
                 "computed from your CRM data."
                 if zia.llm_configured() else
                 "No LLM provider is configured. Zia is answering with the built-in "
                 "deterministic analytics engine. To enable AI wording, set "
                 "ZIA_LLM_API_KEY in your .env file."),
    }
