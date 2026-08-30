"""プロジェクト作成・DRAFT編集・状態遷移API。"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.auth.decorators import require_host
from app.errors import NotFoundError
from app.extensions import db
from app.models import Criterion, Project, Scorer, Subject
from app.services import project_service

api_projects_bp = Blueprint("api_projects", __name__, url_prefix="/api")


def _get_project_or_404(project_id: int) -> Project:
    project = db.session.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found.")
    return project


def _get_subject_or_404(subject_id: int) -> Subject:
    subject = db.session.get(Subject, subject_id)
    if subject is None:
        raise NotFoundError("Subject not found.")
    return subject


def _get_scorer_or_404(scorer_id: int) -> Scorer:
    scorer = db.session.get(Scorer, scorer_id)
    if scorer is None:
        raise NotFoundError("Scorer not found.")
    return scorer


def _get_criterion_or_404(criterion_id: int) -> Criterion:
    criterion = db.session.get(Criterion, criterion_id)
    if criterion is None:
        raise NotFoundError("Criterion not found.")
    return criterion


def _serialize_project_detail(project: Project) -> dict:
    subjects = Subject.query.filter_by(project_id=project.id).order_by(Subject.sort_order).all()
    criteria = Criterion.query.filter_by(project_id=project.id).order_by(Criterion.sort_order).all()
    scorers = Scorer.query.filter_by(project_id=project.id).order_by(Scorer.id).all()
    return {
        "id": project.id,
        "name": project.name,
        "status": project.status,
        "allow_host_scoring": project.allow_host_scoring,
        "subjects": [{"id": s.id, "name": s.name, "sort_order": s.sort_order} for s in subjects],
        "criteria": [
            {"id": c.id, "name": c.name, "max_score": c.max_score, "sort_order": c.sort_order}
            for c in criteria
        ],
        "scorers": [
            {
                "id": sc.id,
                "display_name": sc.display_name,
                "is_host_scorer": sc.is_host_scorer,
                "is_active": sc.is_active,
            }
            for sc in scorers
        ],
    }


@api_projects_bp.post("/projects")
def create_project():
    data = request.get_json(silent=True) or {}
    result = project_service.create_project(
        name=data.get("name", ""),
        subject_names=data.get("subjects") or [],
        scorer_names=data.get("scorers") or [],
        criterion_names=data.get("criteria") or [],
        allow_host_scoring=bool(data.get("allow_host_scoring")),
    )
    project = result["project"]
    return (
        jsonify(
            {
                "project_id": project.id,
                "project_name": project.name,
                "host_code": result["host_code"],
                "scorers": result["scorers"],
            }
        ),
        201,
    )


@api_projects_bp.get("/projects/<int:project_id>")
@require_host
def get_project(project_id: int):
    project = _get_project_or_404(project_id)
    return jsonify(_serialize_project_detail(project))


@api_projects_bp.patch("/projects/<int:project_id>")
@require_host
def update_project(project_id: int):
    project = _get_project_or_404(project_id)
    data = request.get_json(silent=True) or {}
    project_service.update_project_name(project, data.get("name", ""))
    return jsonify(_serialize_project_detail(project))


@api_projects_bp.post("/projects/<int:project_id>/subjects")
@require_host
def create_subject(project_id: int):
    project = _get_project_or_404(project_id)
    data = request.get_json(silent=True) or {}
    subject = project_service.add_subject(project, data.get("name", ""))
    return jsonify({"id": subject.id, "name": subject.name, "sort_order": subject.sort_order}), 201


@api_projects_bp.patch("/projects/<int:project_id>/subjects/<int:subject_id>")
@require_host
def update_subject(project_id: int, subject_id: int):
    project = _get_project_or_404(project_id)
    subject = _get_subject_or_404(subject_id)
    data = request.get_json(silent=True) or {}
    project_service.update_subject(project, subject, data.get("name", ""))
    return jsonify({"id": subject.id, "name": subject.name, "sort_order": subject.sort_order})


@api_projects_bp.delete("/projects/<int:project_id>/subjects/<int:subject_id>")
@require_host
def delete_subject(project_id: int, subject_id: int):
    project = _get_project_or_404(project_id)
    subject = _get_subject_or_404(subject_id)
    project_service.delete_subject(project, subject)
    return "", 204


@api_projects_bp.post("/projects/<int:project_id>/scorers")
@require_host
def create_scorer(project_id: int):
    project = _get_project_or_404(project_id)
    data = request.get_json(silent=True) or {}
    scorer, code = project_service.add_scorer(project, data.get("display_name", ""))
    return (
        jsonify({"id": scorer.id, "display_name": scorer.display_name, "code": code}),
        201,
    )


@api_projects_bp.patch("/projects/<int:project_id>/scorers/<int:scorer_id>")
@require_host
def update_scorer(project_id: int, scorer_id: int):
    project = _get_project_or_404(project_id)
    scorer = _get_scorer_or_404(scorer_id)
    data = request.get_json(silent=True) or {}
    project_service.update_scorer_name(project, scorer, data.get("display_name", ""))
    return jsonify({"id": scorer.id, "display_name": scorer.display_name})


@api_projects_bp.delete("/projects/<int:project_id>/scorers/<int:scorer_id>")
@require_host
def delete_scorer(project_id: int, scorer_id: int):
    project = _get_project_or_404(project_id)
    scorer = _get_scorer_or_404(scorer_id)
    project_service.delete_scorer(project, scorer)
    return "", 204


@api_projects_bp.post("/projects/<int:project_id>/scorers/<int:scorer_id>/regenerate-code")
@require_host
def regenerate_scorer_code(project_id: int, scorer_id: int):
    project = _get_project_or_404(project_id)
    scorer = _get_scorer_or_404(scorer_id)
    code = project_service.regenerate_scorer_code(project, scorer)
    return jsonify({"id": scorer.id, "display_name": scorer.display_name, "code": code})


@api_projects_bp.post("/projects/<int:project_id>/regenerate-host-code")
@require_host
def regenerate_host_code(project_id: int):
    project = _get_project_or_404(project_id)
    code = project_service.regenerate_host_code(project)
    return jsonify({"host_code": code})


@api_projects_bp.patch("/projects/<int:project_id>/criteria/<int:criterion_id>")
@require_host
def update_criterion(project_id: int, criterion_id: int):
    project = _get_project_or_404(project_id)
    criterion = _get_criterion_or_404(criterion_id)
    data = request.get_json(silent=True) or {}
    project_service.update_criterion(project, criterion, data.get("name", ""))
    return jsonify({"id": criterion.id, "name": criterion.name, "max_score": criterion.max_score})


@api_projects_bp.post("/projects/<int:project_id>/transition")
@require_host
def transition_project(project_id: int):
    project = _get_project_or_404(project_id)
    data = request.get_json(silent=True) or {}
    target_status = data.get("target_status", "")
    project_service.transition(project, target_status)
    return jsonify({"id": project.id, "status": project.status})
