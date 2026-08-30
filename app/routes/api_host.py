"""ホスト向け進捗確認API。"""

from __future__ import annotations

from flask import Blueprint, jsonify

from app.auth.decorators import require_host
from app.errors import NotFoundError
from app.extensions import db
from app.models import Project
from app.services import project_service

api_host_bp = Blueprint("api_host", __name__, url_prefix="/api")


@api_host_bp.get("/projects/<int:project_id>/progress")
@require_host
def get_progress(project_id: int):
    project = db.session.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found.")
    return jsonify(project_service.get_progress(project))
