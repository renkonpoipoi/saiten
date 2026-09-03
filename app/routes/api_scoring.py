"""採点(Evaluation/EvaluationScore)関連API。"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.auth.decorators import current_scorer_id, require_scorer
from app.errors import ForbiddenError, NotFoundError
from app.extensions import db
from app.models import Evaluation
from app.services import scoring_service

api_scoring_bp = Blueprint("api_scoring", __name__, url_prefix="/api")


def _get_own_evaluation_or_404(evaluation_id: int) -> Evaluation:
    evaluation = db.session.get(Evaluation, evaluation_id)
    if evaluation is None:
        raise NotFoundError("Evaluation not found.")
    if evaluation.scorer_id != current_scorer_id():
        raise ForbiddenError("This evaluation does not belong to the current scorer.")
    return evaluation


@api_scoring_bp.get("/scorer/me/evaluations")
@require_scorer
def scorer_dashboard():
    return jsonify(scoring_service.get_scorer_dashboard(current_scorer_id()))


@api_scoring_bp.get("/evaluations/<int:evaluation_id>")
@require_scorer
def get_evaluation(evaluation_id: int):
    evaluation = _get_own_evaluation_or_404(evaluation_id)
    return jsonify(scoring_service.get_evaluation_detail(evaluation))


@api_scoring_bp.post("/evaluations/<int:evaluation_id>/scores")
@require_scorer
def save_evaluation_scores(evaluation_id: int):
    evaluation = _get_own_evaluation_or_404(evaluation_id)
    data = request.get_json(silent=True) or {}
    scoring_service.save_scores(evaluation, data.get("scores") or {}, data.get("feedback"))
    return jsonify(scoring_service.get_evaluation_detail(evaluation))


@api_scoring_bp.post("/evaluations/<int:evaluation_id>/submit")
@require_scorer
def submit_evaluation(evaluation_id: int):
    evaluation = _get_own_evaluation_or_404(evaluation_id)
    scoring_service.submit_evaluation(evaluation)
    return jsonify(scoring_service.get_evaluation_detail(evaluation))
