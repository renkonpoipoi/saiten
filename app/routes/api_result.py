"""結果集計・発表用API。実装はPhase 5で追加する。"""

from __future__ import annotations

from flask import Blueprint

api_result_bp = Blueprint("api_result", __name__, url_prefix="/api")
