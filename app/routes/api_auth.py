"""Host/Scorerのコード認証API。"""

from __future__ import annotations

from flask import Blueprint, jsonify, request, session

from app.errors import ForbiddenError, ValidationError
from app.extensions import db, limiter
from app.models import Project, Scorer
from app.services.code_service import hash_code

api_auth_bp = Blueprint("api_auth", __name__, url_prefix="/api/auth")

LOGIN_RATE_LIMIT = "10 per minute"


@api_auth_bp.post("/host-login")
@limiter.limit(LOGIN_RATE_LIMIT)
def host_login():
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    if not code:
        raise ValidationError("Host code is required.")

    project = Project.query.filter_by(host_code_hash=hash_code(code)).first()
    if project is None:
        # 認証に失敗した時点で、それまで保持していたHost権限は破棄する。
        # ログイン画面を開いた時点で利用者は「別のホストとして入り直す」意図を
        # 持っているため、誤ったコードを入力した結果として前のプロジェクトの
        # ホストのまま留まるのは意外性がある。
        # (Phase 8A-1でプロジェクト作成時にHost sessionを張るようになったため、
        #  この破棄がないと「作成直後に誤コードでログイン失敗しても権限が残る」
        #  という状態が生まれる。)
        session.pop("host_project_id", None)
        raise ForbiddenError("Invalid host code.")

    session["host_project_id"] = project.id
    return jsonify(
        {
            "project_id": project.id,
            "project_name": project.name,
            "status": project.status,
        }
    )


@api_auth_bp.post("/scorer-login")
@limiter.limit(LOGIN_RATE_LIMIT)
def scorer_login():
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    if not code:
        raise ValidationError("Scorer code is required.")

    scorer = Scorer.query.filter_by(access_code_hash=hash_code(code), is_active=True).first()
    if scorer is None:
        # host-loginと同じ方針で、認証失敗時は既存のScorer権限を破棄する。
        session.pop("scorer_id", None)
        session.pop("scorer_project_id", None)
        raise ForbiddenError("Invalid scorer code.")

    project = db.session.get(Project, scorer.project_id)

    session["scorer_id"] = scorer.id
    session["scorer_project_id"] = scorer.project_id
    return jsonify(
        {
            "scorer_id": scorer.id,
            "display_name": scorer.display_name,
            "project_id": project.id,
            "project_name": project.name,
            "project_status": project.status,
        }
    )
