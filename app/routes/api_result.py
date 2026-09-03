"""結果集計・発表用API。

result-summaryは同一Flaskアプリ内のresult_serviceから直接取得する
(旧resultアプリのような外部HTTPプロキシは使用しない)。
Host session必須で、LOCKED以降でのみ取得可能とする。
"""

from __future__ import annotations

from flask import Blueprint, Response, jsonify

from app.auth.decorators import require_host
from app.errors import ConflictError, NotFoundError
from app.extensions import db
from app.models import Project
from app.services import export_service, result_service

api_result_bp = Blueprint("api_result", __name__, url_prefix="/api")

_AVAILABLE_STATUSES = ("LOCKED", "PRESENTING", "FINISHED")


def _get_project_for_results(project_id: int) -> Project:
    """結果系エンドポイント共通のゲート。

    LOCKED以降でのみ結果を公開する。SEQUENTIALはSubjectを1件ずつ発表し終えるまで
    Project.statusがSCORINGのままなので、既に発表済みのSubjectがあっても
    プロジェクト全体の結果・分析・exportはまだ公開されない。採点中の裏で他Scorerの
    点数やfeedbackがHostに見えてしまうのを防ぐため、この扱いは意図的なもの。
    (発表中の1 Subjectだけはsubject単位のエンドポイントで開示する)
    """
    project = db.session.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found.")
    if project.status not in _AVAILABLE_STATUSES:
        raise ConflictError("Result summary is only available after the project is locked.")
    return project


@api_result_bp.get("/projects/<int:project_id>/result-summary")
@require_host
def get_result_summary(project_id: int):
    project = _get_project_for_results(project_id)
    return jsonify(result_service.build_result_summary(project))


@api_result_bp.get("/projects/<int:project_id>/analysis")
@require_host
def get_analysis(project_id: int):
    project = _get_project_for_results(project_id)
    return jsonify(result_service.build_analysis(project))


@api_result_bp.get("/projects/<int:project_id>/export.csv")
@require_host
def export_csv(project_id: int):
    project = _get_project_for_results(project_id)
    analysis = result_service.build_analysis(project)
    ascii_name, utf8_name = export_service.csv_filename(analysis)
    return Response(
        export_service.build_csv(analysis),
        mimetype="text/csv",
        headers={
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Disposition": export_service.content_disposition(ascii_name, utf8_name),
        },
    )


@api_result_bp.get("/projects/<int:project_id>/export.md")
@require_host
def export_markdown(project_id: int):
    project = _get_project_for_results(project_id)
    analysis = result_service.build_analysis(project)
    ascii_name, utf8_name = export_service.markdown_filename(analysis)
    return Response(
        export_service.build_markdown(analysis),
        mimetype="text/markdown",
        headers={
            "Content-Type": "text/markdown; charset=utf-8",
            "Content-Disposition": export_service.content_disposition(ascii_name, utf8_name),
        },
    )
