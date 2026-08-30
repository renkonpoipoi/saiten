"""結果集計・発表用API。

result-summaryは同一Flaskアプリ内のresult_serviceから直接取得する
(旧resultアプリのような外部HTTPプロキシは使用しない)。
Host session必須で、LOCKED以降でのみ取得可能とする。
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from app.auth.decorators import require_host
from app.errors import ConflictError, NotFoundError
from app.extensions import db
from app.models import Project
from app.services import result_service

api_result_bp = Blueprint("api_result", __name__, url_prefix="/api")

_AVAILABLE_STATUSES = ("LOCKED", "PRESENTING", "FINISHED")


@api_result_bp.get("/projects/<int:project_id>/result-summary")
@require_host
def get_result_summary(project_id: int):
    project = db.session.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found.")
    if project.status not in _AVAILABLE_STATUSES:
        raise ConflictError("Result summary is only available after the project is locked.")
    return jsonify(result_service.build_result_summary(project))
