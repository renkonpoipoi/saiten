"""採点(Evaluation/EvaluationScore)の保存・確定を扱うサービス層。

autosave対象はEvaluation.status='draft'の場合のみ更新可能で、confirmed後は
不可逆(=submitted後の書き込みはサーバー側で常に拒否する)。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.errors import ConflictError, ValidationError
from app.extensions import db
from app.models import Criterion, Evaluation, EvaluationScore, Project, Subject
from app.services import project_service

EVALUATION_STATUS_NOT_STARTED = "not_started"


class AlreadySubmittedError(ConflictError):
    """確定済み(status='submitted')のEvaluationへの書き込みを拒否する際に送出する。"""


class ScoringClosedError(ConflictError):
    """Project.statusがSCORING以外(LOCKED以降)での書き込みを拒否する際に送出する。"""


def _require_scoring_open(evaluation: Evaluation) -> None:
    project = db.session.get(Project, evaluation.project_id)
    if project is None or project.status != "SCORING":
        raise ScoringClosedError("Scoring is not open for this project.")

    if project.presentation_mode == "SEQUENTIAL":
        # SEQUENTIALでは、いま発表順が回ってきているSubjectしか採点できない。
        # WAITING(先行採点)・LOCKED(締切後)・PRESENTED(発表済みの再編集)は全て拒否する。
        # UI側のdisabledはあくまでUXのためで、拒否の実体はここ。
        subject = db.session.get(Subject, evaluation.subject_id)
        if subject is None or subject.presentation_status != "SCORING":
            current = subject.presentation_status if subject else "unknown"
            raise ScoringClosedError(
                f"This subject is not open for scoring (current status: {current})."
            )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_scorer_dashboard(scorer_id: int) -> dict:
    """Scorer Dashboard向け: 担当する被採点者一覧と各評価の状態、全体進捗。"""
    evaluations = (
        Evaluation.query.filter_by(scorer_id=scorer_id)
        .join(Subject, Subject.id == Evaluation.subject_id)
        .order_by(Subject.sort_order)
        .all()
    )
    project = (
        db.session.get(Project, evaluations[0].project_id) if evaluations else None
    )

    rows = []
    for evaluation in evaluations:
        score_count = EvaluationScore.query.filter_by(evaluation_id=evaluation.id).count()
        if evaluation.status == "submitted":
            state = "submitted"
        elif score_count > 0:
            state = "in_progress"
        else:
            state = EVALUATION_STATUS_NOT_STARTED

        # SEQUENTIALでは順番が回ってきたSubjectだけが採点可能。
        # 画面はこのフラグで「待機中」を表示するが、拒否の実体はサーバー側にある。
        subject_status = project_service.subject_presentation_status(
            project, evaluation.subject
        )
        rows.append(
            {
                "evaluation_id": evaluation.id,
                "subject_id": evaluation.subject_id,
                "subject_name": evaluation.subject.name,
                "state": state,
                "subject_status": subject_status,
                "scorable": subject_status == "SCORING" and state != "submitted",
            }
        )
    submitted_count = sum(1 for r in rows if r["state"] == "submitted")
    return {
        "subjects": rows,
        "submitted_count": submitted_count,
        "total_count": len(rows),
    }


def get_evaluation_detail(evaluation: Evaluation) -> dict:
    criteria = (
        Criterion.query.filter_by(project_id=evaluation.project_id)
        .order_by(Criterion.sort_order)
        .all()
    )
    score_lookup = {s.criterion_id: s.score for s in evaluation.scores}
    return {
        "evaluation_id": evaluation.id,
        "status": evaluation.status,
        "feedback": evaluation.feedback,
        "subject": {"id": evaluation.subject.id, "name": evaluation.subject.name},
        "criteria": [
            {
                "id": c.id,
                "name": c.name,
                "max_score": c.max_score,
                "score": score_lookup.get(c.id),
            }
            for c in criteria
        ],
    }


def save_scores(evaluation: Evaluation, scores: dict, feedback: str | None) -> Evaluation:
    """scores: {criterion_id(str/int): score(int)}. draft状態でのみ更新可能。

    Project.statusがSCORINGでない場合(LOCKED以降)は、Evaluation自体が
    draftのままでも書き込みを拒否する。
    """
    _require_scoring_open(evaluation)
    if evaluation.status != "draft":
        raise AlreadySubmittedError("This evaluation has already been submitted.")

    criteria = {
        c.id: c for c in Criterion.query.filter_by(project_id=evaluation.project_id).all()
    }

    cleaned: dict[int, int] = {}
    for raw_criterion_id, raw_score in (scores or {}).items():
        try:
            criterion_id = int(raw_criterion_id)
        except (TypeError, ValueError):
            raise ValidationError("Invalid criterion id.") from None
        criterion = criteria.get(criterion_id)
        if criterion is None:
            raise ValidationError("Unknown criterion for this project.")
        if raw_score is None or raw_score == "":
            continue
        try:
            score = int(raw_score)
        except (TypeError, ValueError):
            raise ValidationError("Score must be an integer.") from None
        if score < 0 or score > criterion.max_score:
            raise ValidationError(f"Score must be between 0 and {criterion.max_score}.")
        cleaned[criterion_id] = score

    existing = {s.criterion_id: s for s in evaluation.scores}
    for criterion_id, score in cleaned.items():
        if criterion_id in existing:
            existing[criterion_id].score = score
        else:
            db.session.add(
                EvaluationScore(evaluation_id=evaluation.id, criterion_id=criterion_id, score=score)
            )

    if feedback is not None:
        evaluation.feedback = feedback

    evaluation.updated_at = _utcnow()
    db.session.commit()
    return evaluation


def submit_evaluation(evaluation: Evaluation) -> Evaluation:
    """draft -> submitted への不可逆遷移。全criteriaの採点が揃っていることを要求する。

    既にsubmitted済みの場合は安全に扱う(冪等: エラーにせず現在の状態を返す。
    project状態に関わらず、実質的に書き込みが発生しない読み取り相当の
    操作のため許可する)。
    """
    if evaluation.status == "submitted":
        return evaluation

    _require_scoring_open(evaluation)

    criterion_ids = {
        c.id for c in Criterion.query.filter_by(project_id=evaluation.project_id).all()
    }
    scored_ids = {s.criterion_id for s in evaluation.scores}
    if not criterion_ids.issubset(scored_ids):
        raise ValidationError("All criteria must be scored before submitting.")

    evaluation.status = "submitted"
    evaluation.submitted_at = _utcnow()
    db.session.commit()
    return evaluation
