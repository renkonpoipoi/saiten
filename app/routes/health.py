"""Render等のプラットフォーム向け liveness check endpoint。

意図的に「プロセスが生きているか」だけを返す純粋なliveness checkとする。

- 認証不要(Host/Scorerいずれのsessionも要求しない)
- CSRF不要(GETのみ。CSRFProtectは状態変更メソッドのみ保護する)
- DBへの読み書きを一切行わない
- secret・Project情報・件数など内部状態を一切返さない
- rate limitの対象外(プラットフォームのヘルスチェックが遮断されないため)

DB接続の正常性はこのendpointでは検査しない。DBを含む疎通確認は
production smoke test(Phase 7D)で別途行う。
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from app.extensions import limiter

health_bp = Blueprint("health", __name__)


@health_bp.get("/healthz")
@limiter.exempt
def healthz():
    return jsonify({"status": "ok"}), 200
