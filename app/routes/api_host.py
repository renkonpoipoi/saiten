"""ホスト向け進捗確認・締切API。実装はPhase 4で追加する。"""

from __future__ import annotations

from flask import Blueprint

api_host_bp = Blueprint("api_host", __name__, url_prefix="/api")
