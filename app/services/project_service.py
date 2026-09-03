"""プロジェクトのライフサイクル(DRAFT/SCORING/LOCKED/PRESENTING/FINISHED)と
DRAFT編集(Project/Subject/Criterion/Scorer)を扱うサービス層。

route側はHTTPの入出力にのみ責務を持ち、業務ロジックはここに集約する。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func

from app.errors import ConflictError, ForbiddenError, ValidationError
from app.extensions import db
from app.models import Criterion, Evaluation, Project, Scorer, Subject
from app.services.code_service import generate_host_code, generate_scorer_code, hash_code

REQUIRED_CRITERION_COUNT = 5
DEFAULT_MAX_SCORE = 20
HOST_SCORER_DISPLAY_NAME = "ホスト"


class ProjectStateError(ConflictError):
    """状態遷移やDRAFT限定編集のガードに違反した操作を表す例外。"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clean_names(raw_names: list[str]) -> list[str]:
    return [n.strip() for n in raw_names if n and n.strip()]


def _require_draft(project: Project) -> None:
    if project.status != "DRAFT":
        raise ProjectStateError(
            "This operation is only allowed while the project is in DRAFT status."
        )


def _require_owned(project: Project, obj, *, label: str) -> None:
    if obj.project_id != project.id:
        raise ForbiddenError(f"{label} does not belong to this project.")


# ---------------------------------------------------------------------------
# 作成
# ---------------------------------------------------------------------------


def create_project(
    *,
    name: str,
    subject_names: list[str],
    scorer_names: list[str],
    criterion_names: list[str],
    allow_host_scoring: bool,
) -> dict:
    """Project/Subject/Criterion/Scorerを1トランザクションで一括作成する。

    戻り値には平文のhost_code・scorer codeを一度だけ含める(DBにはhashのみ
    保存する)。
    """
    name = (name or "").strip()
    if not name:
        raise ValidationError("Project name is required.")

    subject_names = _clean_names(subject_names or [])
    if not subject_names:
        raise ValidationError("At least one subject is required.")

    scorer_names = _clean_names(scorer_names or [])
    if not scorer_names:
        raise ValidationError("At least one scorer is required.")

    criterion_names = _clean_names(criterion_names or [])
    if len(criterion_names) != REQUIRED_CRITERION_COUNT:
        raise ValidationError(
            f"Exactly {REQUIRED_CRITERION_COUNT} criteria are required in this MVP."
        )

    host_code = generate_host_code()
    project = Project(
        name=name,
        status="DRAFT",
        host_code_hash=hash_code(host_code),
        allow_host_scoring=bool(allow_host_scoring),
    )
    db.session.add(project)
    db.session.flush()  # project.id を採番させる

    for order, subject_name in enumerate(subject_names):
        db.session.add(Subject(project_id=project.id, name=subject_name, sort_order=order))

    for order, criterion_name in enumerate(criterion_names):
        db.session.add(
            Criterion(
                project_id=project.id,
                name=criterion_name,
                max_score=DEFAULT_MAX_SCORE,
                sort_order=order,
            )
        )

    scorer_payload = []
    all_scorer_names = list(scorer_names)
    if allow_host_scoring:
        all_scorer_names.append(HOST_SCORER_DISPLAY_NAME)

    for index, scorer_name in enumerate(all_scorer_names):
        is_host_scorer = allow_host_scoring and index == len(all_scorer_names) - 1
        code = generate_scorer_code()
        scorer = Scorer(
            project_id=project.id,
            display_name=scorer_name,
            access_code_hash=hash_code(code),
            is_host_scorer=is_host_scorer,
        )
        db.session.add(scorer)
        db.session.flush()
        scorer_payload.append(
            {
                "id": scorer.id,
                "display_name": scorer.display_name,
                "code": code,
                "is_host_scorer": scorer.is_host_scorer,
            }
        )

    db.session.commit()

    return {
        "project": project,
        "host_code": host_code,
        "scorers": scorer_payload,
    }


# ---------------------------------------------------------------------------
# DRAFT限定編集
# ---------------------------------------------------------------------------


def update_project_name(project: Project, name: str) -> Project:
    _require_draft(project)
    name = (name or "").strip()
    if not name:
        raise ValidationError("Project name is required.")
    project.name = name
    db.session.commit()
    return project


def add_subject(project: Project, name: str) -> Subject:
    _require_draft(project)
    name = (name or "").strip()
    if not name:
        raise ValidationError("Subject name is required.")
    max_order = (
        db.session.query(func.max(Subject.sort_order)).filter_by(project_id=project.id).scalar()
    )
    subject = Subject(
        project_id=project.id, name=name, sort_order=(max_order + 1) if max_order is not None else 0
    )
    db.session.add(subject)
    db.session.commit()
    return subject


def update_subject(project: Project, subject: Subject, name: str) -> Subject:
    _require_draft(project)
    _require_owned(project, subject, label="Subject")
    name = (name or "").strip()
    if not name:
        raise ValidationError("Subject name is required.")
    subject.name = name
    db.session.commit()
    return subject


def delete_subject(project: Project, subject: Subject) -> None:
    _require_draft(project)
    _require_owned(project, subject, label="Subject")
    db.session.delete(subject)
    db.session.commit()


def update_criterion(project: Project, criterion: Criterion, name: str) -> Criterion:
    """MVPでは軸の個数(5件)は固定のため、名称編集のみ許可する。"""
    _require_draft(project)
    _require_owned(project, criterion, label="Criterion")
    name = (name or "").strip()
    if not name:
        raise ValidationError("Criterion name is required.")
    criterion.name = name
    db.session.commit()
    return criterion


def add_scorer(project: Project, display_name: str) -> tuple[Scorer, str]:
    _require_draft(project)
    display_name = (display_name or "").strip()
    if not display_name:
        raise ValidationError("Scorer name is required.")
    code = generate_scorer_code()
    scorer = Scorer(
        project_id=project.id, display_name=display_name, access_code_hash=hash_code(code)
    )
    db.session.add(scorer)
    db.session.commit()
    return scorer, code


def update_scorer_name(project: Project, scorer: Scorer, display_name: str) -> Scorer:
    _require_draft(project)
    _require_owned(project, scorer, label="Scorer")
    display_name = (display_name or "").strip()
    if not display_name:
        raise ValidationError("Scorer name is required.")
    scorer.display_name = display_name
    db.session.commit()
    return scorer


def delete_scorer(project: Project, scorer: Scorer) -> None:
    _require_draft(project)
    _require_owned(project, scorer, label="Scorer")
    db.session.delete(scorer)
    db.session.commit()


# ---------------------------------------------------------------------------
# コード再発行(DRAFT限定ではなく常時可能)
# ---------------------------------------------------------------------------


def regenerate_scorer_code(project: Project, scorer: Scorer) -> str:
    _require_owned(project, scorer, label="Scorer")
    code = generate_scorer_code()
    scorer.access_code_hash = hash_code(code)
    db.session.commit()
    return code


def regenerate_host_code(project: Project) -> str:
    code = generate_host_code()
    project.host_code_hash = hash_code(code)
    db.session.commit()
    return code


# ---------------------------------------------------------------------------
# eligible scorer判定(Phase 4で本格利用するが、進捗表示にも使うためここに置く)
# ---------------------------------------------------------------------------


def eligible_scorer_ids(project_id: int) -> set[int]:
    """そのプロジェクトの全Subjectに対してstatus='submitted'のEvaluationを
    持つactiveなScorerのIDを返す(=公式集計の対象になる採点者)。

    N+1を避けるため、Scorer単位でループせず集計クエリ1本で判定する。
    """
    total_subjects = (
        db.session.query(func.count(Subject.id)).filter(Subject.project_id == project_id).scalar()
    )
    if not total_subjects:
        return set()

    rows = (
        db.session.query(Evaluation.scorer_id, func.count(Evaluation.id))
        .join(Scorer, Scorer.id == Evaluation.scorer_id)
        .filter(
            Evaluation.project_id == project_id,
            Evaluation.status == "submitted",
            Scorer.is_active.is_(True),
        )
        .group_by(Evaluation.scorer_id)
        .all()
    )
    return {scorer_id for scorer_id, submitted_count in rows if submitted_count == total_subjects}


def participating_scorer_ids(project_id: int) -> set[int]:
    """SCORING開始時点で固定された参加Scorerの集合を返す。

    DRAFT->SCORING遷移がactive Scorer x SubjectのEvaluationを一括生成し、それ以降
    Scorerの追加・削除は_require_draftガードにより不可能なため(DRAFTへ戻る遷移が
    存在しない)、**Evaluation行そのものが参加Scorerのsnapshotになっている**。
    このため専用のsnapshotテーブルは持たない。

    この不変条件を壊さないため、SCORING開始後にScorerを増減させる操作や
    is_activeを書き換える操作を追加してはならない。
    """
    rows = (
        db.session.query(Evaluation.scorer_id)
        .join(Scorer, Scorer.id == Evaluation.scorer_id)
        .filter(Evaluation.project_id == project_id, Scorer.is_active.is_(True))
        .distinct()
        .all()
    )
    return {scorer_id for (scorer_id,) in rows}


def official_scorer_ids(project: Project) -> set[int]:
    """公式集計の対象になるScorer集合。モード差はこの1関数に閉じ込める。

    - BATCH:      全Subjectを提出し終えたScorerのみ(=eligible scorer)。
                  forced closeで未完了者を全Subjectから一律除外することで、
                  Subject間の審査員数を揃えている。
    - SEQUENTIAL: 参加Scorer全員。Subjectのlock条件が「全参加Scorerの提出」で
                  あるため、発表可能なSubjectでは常に全員分が揃っている。
                  (eligible判定は「全Subject提出済み」を要求するので、
                   後続Subjectが未採点なSEQUENTIALの途中では常に空集合になり使えない)
    """
    if project.presentation_mode == "SEQUENTIAL":
        return participating_scorer_ids(project.id)
    return eligible_scorer_ids(project.id)


# BATCHではSubject単位の進行状態を持たないため、Project.statusから導出する。
_BATCH_SUBJECT_STATUS = {
    "DRAFT": "WAITING",
    "SCORING": "SCORING",
    "LOCKED": "LOCKED",
    "PRESENTING": "PRESENTED",
    "FINISHED": "PRESENTED",
}


def subject_presentation_status(project: Project, subject: Subject) -> str:
    """Subjectの進行状態を、モードの違いを隠して1つの語彙で返す。

    subjects.presentation_status列はSEQUENTIALでのみ権威を持つ。BATCHでは
    Project.statusから導出するため、Phase 8以前に作られた既存Projectの
    'WAITING'という値が誤って表示されることはない(=migrationでの
    データbackfillが不要)。
    """
    if project.presentation_mode == "SEQUENTIAL":
        return subject.presentation_status
    return _BATCH_SUBJECT_STATUS[project.status]


# ---------------------------------------------------------------------------
# 状態遷移
# ---------------------------------------------------------------------------


def transition_to_scoring(project: Project) -> Project:
    if project.status != "DRAFT":
        raise ProjectStateError("Only DRAFT projects can start scoring.")

    active_scorers = Scorer.query.filter_by(project_id=project.id, is_active=True).all()
    subjects = Subject.query.filter_by(project_id=project.id).all()
    if not active_scorers:
        raise ProjectStateError("At least one active scorer is required to start scoring.")
    if not subjects:
        raise ProjectStateError("At least one subject is required to start scoring.")

    existing_pairs = {
        (e.scorer_id, e.subject_id)
        for e in Evaluation.query.filter_by(project_id=project.id).all()
    }
    for scorer in active_scorers:
        for subject in subjects:
            if (scorer.id, subject.id) in existing_pairs:
                continue
            db.session.add(
                Evaluation(project_id=project.id, scorer_id=scorer.id, subject_id=subject.id)
            )

    project.status = "SCORING"
    db.session.commit()
    return project


def transition_to_locked(project: Project) -> Project:
    if project.status != "SCORING":
        raise ProjectStateError("Only SCORING projects can be locked.")

    eligible_ids = eligible_scorer_ids(project.id)
    if not eligible_ids:
        raise ProjectStateError(
            "Cannot lock: no eligible scorer has completed all subjects yet."
        )

    project.status = "LOCKED"
    project.locked_at = _utcnow()
    db.session.commit()
    return project


def transition_to_presenting(project: Project) -> Project:
    if project.status != "LOCKED":
        raise ProjectStateError("Only LOCKED projects can start presenting.")
    project.status = "PRESENTING"
    project.presenting_at = _utcnow()
    db.session.commit()
    return project


def transition_to_finished(project: Project) -> Project:
    if project.status != "PRESENTING":
        raise ProjectStateError("Only PRESENTING projects can finish.")
    project.status = "FINISHED"
    project.finished_at = _utcnow()
    db.session.commit()
    return project


_TRANSITIONS = {
    ("DRAFT", "SCORING"): transition_to_scoring,
    ("SCORING", "LOCKED"): transition_to_locked,
    ("LOCKED", "PRESENTING"): transition_to_presenting,
    ("PRESENTING", "FINISHED"): transition_to_finished,
}


def transition(project: Project, target_status: str) -> Project:
    handler = _TRANSITIONS.get((project.status, target_status))
    if handler is None:
        raise ProjectStateError(
            f"Cannot transition project from {project.status} to {target_status}."
        )
    return handler(project)


# ---------------------------------------------------------------------------
# 進捗(Host Dashboard/Settings向け)
# ---------------------------------------------------------------------------


def get_progress(project: Project) -> dict:
    subjects = Subject.query.filter_by(project_id=project.id).order_by(Subject.sort_order).all()
    scorers = Scorer.query.filter_by(project_id=project.id, is_active=True).order_by(Scorer.id).all()

    eligible_ids = eligible_scorer_ids(project.id) if subjects else set()

    evaluations = Evaluation.query.filter_by(project_id=project.id).all()
    eval_lookup = {(e.scorer_id, e.subject_id): e.status for e in evaluations}

    scorer_rows = []
    for scorer in scorers:
        statuses = [eval_lookup.get((scorer.id, s.id), "not_started") for s in subjects]
        scorer_rows.append(
            {
                "scorer_id": scorer.id,
                "display_name": scorer.display_name,
                "is_host_scorer": scorer.is_host_scorer,
                # subjectsと同じ並び順のstatus一覧(Host Dashboardの
                # Scorer x Subjectマトリクス描画用)
                "statuses": statuses,
                "submitted_count": sum(1 for st in statuses if st == "submitted"),
                "subject_count": len(subjects),
                "eligible": scorer.id in eligible_ids,
            }
        )

    submitted_count = sum(1 for e in evaluations if e.status == "submitted")

    return {
        "project_status": project.status,
        "subjects": [{"id": s.id, "name": s.name} for s in subjects],
        "scorers": scorer_rows,
        "submitted_count": submitted_count,
        "total_count": len(evaluations),
        "eligible_scorer_count": len(eligible_ids),
        "incomplete_scorer_count": len(scorers) - len(eligible_ids),
    }
