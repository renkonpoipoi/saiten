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
from app.models import Project, Subject
from app.services import export_service, project_service, result_service

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


@api_result_bp.get("/projects/<int:project_id>/presentation-state")
@require_host
def get_presentation_state(project_id: int):
    """結果発表画面が次に何を出すべきかを決めるための状態。

    採点中でも取得できるが、返すのは進行状態と提出人数だけで、点数は含まない。
    """
    project = db.session.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found.")
    return jsonify(project_service.build_presentation_state(project))


@api_result_bp.get("/projects/<int:project_id>/subjects/<int:subject_id>/result")
@require_host
def get_subject_result(project_id: int, subject_id: int):
    """SEQUENTIAL: 締切済みの1 Subjectだけの発表データ。"""
    project = db.session.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found.")
    subject = db.session.get(Subject, subject_id)
    if subject is None:
        raise NotFoundError("Subject not found.")
    return jsonify(result_service.build_subject_result(project, subject))


@api_result_bp.get("/projects/<int:project_id>/interim-ranking")
@require_host
def get_interim_ranking(project_id: int):
    """SEQUENTIAL: 発表中/発表済みのSubjectだけで作る暫定ランキング。

    SEQUENTIALはSubjectを1件ずつ発表し終えるまでProject.statusがSCORINGのまま
    なのでresult-summaryは使えない。一方で暫定順位の表示には、いま発表中の
    Subject(=LOCKED)と発表済みSubject(=PRESENTED)の合計点が必要になる。

    開示範囲はsubject単位のエンドポイントと同じゲート
    (result_service.REVEALABLE_SUBJECT_STATUSES)に揃えてあり、
    WAITING / SCORING のSubjectの点数は一切含まない。
    順位はサーバー側のcompetition rankingが唯一の正解。
    """
    project = db.session.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found.")
    return jsonify(result_service.build_interim_ranking(project))


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
