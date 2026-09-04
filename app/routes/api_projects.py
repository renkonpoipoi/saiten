"""プロジェクト作成・DRAFT編集・状態遷移API。"""

from __future__ import annotations

from flask import Blueprint, jsonify, request, session

from app.auth.decorators import require_host
from app.errors import ConflictError, NotFoundError, ValidationError
from app.extensions import db, limiter
from app.models import Criterion, Project, Scorer, Subject
from app.services import project_service

api_projects_bp = Blueprint("api_projects", __name__, url_prefix="/api")

# 作成APIは認証を要求できない(まだプロジェクトが存在しないため)一方で、
# 成功時にHost sessionを発行する。無制限にProject+sessionを量産されないよう
# rate limitをかける。ログイン用の制限(10 per minute)とは用途が違うため別値。
PROJECT_CREATE_RATE_LIMIT = "20 per hour"


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
@limiter.limit(PROJECT_CREATE_RATE_LIMIT)
def create_project():
    data = request.get_json(silent=True) or {}
    result = project_service.create_project(
        name=data.get("name", ""),
        subject_names=data.get("subjects") or [],
        scorer_names=data.get("scorers") or [],
        criterion_names=data.get("criteria") or [],
        allow_host_scoring=bool(data.get("allow_host_scoring")),
        presentation_mode=data.get("presentation_mode") or "BATCH",
        # allow_host_scoringがTrueのとき、入力済みScorerのうち誰がHostを兼ねるかを
        # 指すindex(空文字除去後のscorers配列基準)。検証はservice側で行う。
        host_scorer_index=data.get("host_scorer_index"),
    )
    project = result["project"]

    # 作成したブラウザはそのプロジェクトのHostであることが自明(平文のhost_codeを
    # このレスポンスで受け取っている)ため、host codeの再入力を求めずsessionを張る。
    # host code自体は引き続き発行・hash保存され、別ブラウザ・別端末・session消失後の
    # ログインには必須のまま。既存のhost-loginと同様、sessionが保持できるHost権限は
    # 常に1プロジェクト分だけである点も変わらない。
    session["host_project_id"] = project.id

    return (
        jsonify(
            {
                "project_id": project.id,
                "project_name": project.name,
                "presentation_mode": project.presentation_mode,
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


@api_projects_bp.post("/projects/<int:project_id>/host-scorer-session")
@require_host
def open_host_scorer_session(project_id: int):
    """Host本人が、コード入力なしに自分の採点画面へ入れるようにする。

    対象のScorerは **サーバー側が project_id + is_host_scorer から決める。**
    clientからscorer_idを受け取らないので、この経路で他のScorerへ
    なりすますことはできない。

    権限昇格にもならない: 呼び出しにはそのProjectのHost session
    (=host codeを知っていること) が必要で、付与されるのはHost自身が
    作成時に指定したScorerの権限だけ。そのScorerの平文コードはHostが
    作成時に一度受け取っている。

    Host sessionは破棄しない。session["host_project_id"] と
    session["scorer_id"] は別keyなので、元タブのHost Dashboardは
    そのまま使い続けられる。
    """
    project = _get_project_or_404(project_id)
    scorer = (
        Scorer.query.filter_by(
            project_id=project.id, is_host_scorer=True, is_active=True
        )
        .order_by(Scorer.id)
        .first()
    )
    if scorer is None:
        raise ConflictError(
            "This project has no host scorer. Assign one in the project settings."
        )

    session["scorer_id"] = scorer.id
    session["scorer_project_id"] = scorer.project_id
    return jsonify({"scorer_id": scorer.id, "display_name": scorer.display_name})


@api_projects_bp.patch("/projects/<int:project_id>/host-scorer")
@require_host
def set_host_scorer(project_id: int):
    """DRAFT中にHost兼任のScorerを付け替える(scorer_id: null で解除)。

    Host roleはScorerの属性なので、ここで行うのはフラグの移動だけ。
    Scorerの追加・削除は一切行わない。旧方式で作られたProjectに残っている
    「ホスト」という名前のScorerも、ここでは自動削除しない
    (「ホスト」という名前の正規のScorerである可能性を否定できないため)。
    不要なScorerの削除は従来どおりDRAFT編集の明示操作で行う。
    """
    project = _get_project_or_404(project_id)
    data = request.get_json(silent=True) or {}
    raw_id = data.get("scorer_id")

    scorer = None
    if raw_id is not None:
        if isinstance(raw_id, bool) or not isinstance(raw_id, int):
            raise ValidationError("scorer_id must be an integer or null.")
        scorer = _get_scorer_or_404(raw_id)

    project_service.set_host_scorer(project, scorer)
    return jsonify(_serialize_project_detail(project))


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


def _serialize_subject_state(subject: Subject) -> dict:
    return {
        "id": subject.id,
        "name": subject.name,
        "sort_order": subject.sort_order,
        "presentation_status": subject.presentation_status,
        "locked_at": subject.locked_at.isoformat() if subject.locked_at else None,
        "presented_at": subject.presented_at.isoformat() if subject.presented_at else None,
    }


@api_projects_bp.post("/projects/<int:project_id>/subjects/<int:subject_id>/lock")
@require_host
def lock_subject(project_id: int, subject_id: int):
    """SEQUENTIAL: 1 Subjectの採点を締め切る。参加Scorer全員の提出が前提。"""
    project = _get_project_or_404(project_id)
    subject = _get_subject_or_404(subject_id)
    project_service.lock_subject(project, subject)
    return jsonify(_serialize_subject_state(subject))


@api_projects_bp.post("/projects/<int:project_id>/subjects/<int:subject_id>/present")
@require_host
def present_subject(project_id: int, subject_id: int):
    """SEQUENTIAL: 発表済みとして確定し、次のSubjectを採点可能にする。"""
    project = _get_project_or_404(project_id)
    subject = _get_subject_or_404(subject_id)
    _, next_subject = project_service.present_subject(project, subject)
    return jsonify(
        {
            "subject": _serialize_subject_state(subject),
            "next_subject": (
                _serialize_subject_state(next_subject) if next_subject else None
            ),
        }
    )


@api_projects_bp.post("/projects/<int:project_id>/transition")
@require_host
def transition_project(project_id: int):
    project = _get_project_or_404(project_id)
    data = request.get_json(silent=True) or {}
    target_status = data.get("target_status", "")
    project_service.transition(project, target_status)
    return jsonify({"id": project.id, "status": project.status})
