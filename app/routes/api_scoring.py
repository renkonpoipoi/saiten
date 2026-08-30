"""採点(Evaluation/EvaluationScore)関連API。実装はPhase 3で追加する。"""

from __future__ import annotations

from flask import Blueprint

api_scoring_bp = Blueprint("api_scoring", __name__, url_prefix="/api")
