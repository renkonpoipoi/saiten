from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, text

from app.extensions import db

EVALUATION_STATUSES = ("draft", "submitted")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Evaluation(db.Model):
    """1採点者 x 1被採点者。DRAFT->SCORING遷移時にactive Scorer x Subjectの
    組み合わせで一括生成される想定(Phase 2で実装)。以降の採点操作はUPDATEのみ。
    """

    __tablename__ = "evaluations"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer, db.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    scorer_id = db.Column(
        db.Integer, db.ForeignKey("scorers.id", ondelete="CASCADE"), nullable=False
    )
    subject_id = db.Column(
        db.Integer, db.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False
    )
    feedback = db.Column(db.Text, nullable=False, default="", server_default=text("''"))
    status = db.Column(
        db.String(12), nullable=False, default="draft", server_default=text("'draft'")
    )
    submitted_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )

    __table_args__ = (
        db.UniqueConstraint("scorer_id", "subject_id", name="ux_evaluations_scorer_subject"),
        db.CheckConstraint("status IN ('draft','submitted')", name="ck_evaluations_status"),
    )

    subject = db.relationship(
        "Subject",
        backref=db.backref("evaluations", cascade="all, delete-orphan", passive_deletes=True),
    )
    scores = db.relationship(
        "EvaluationScore", backref="evaluation", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Evaluation id={self.id} scorer_id={self.scorer_id} "
            f"subject_id={self.subject_id} status={self.status}>"
        )


class EvaluationScore(db.Model):
    """採点軸別スコア。可変軸数に対応する正規化テーブル(固定score_1..5カラムは使わない)。"""

    __tablename__ = "evaluation_scores"

    id = db.Column(db.Integer, primary_key=True)
    evaluation_id = db.Column(
        db.Integer, db.ForeignKey("evaluations.id", ondelete="CASCADE"), nullable=False
    )
    # criterionは採点済みデータの整合性を守るためRESTRICT(誤ってcriterionを
    # 削除してスコア履歴が消えることを防ぐ)。
    criterion_id = db.Column(
        db.Integer, db.ForeignKey("criteria.id", ondelete="RESTRICT"), nullable=False
    )
    score = db.Column(db.Integer, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )

    __table_args__ = (
        db.UniqueConstraint(
            "evaluation_id", "criterion_id", name="ux_evaluation_scores_eval_criterion"
        ),
        db.CheckConstraint("score >= 0", name="ck_evaluation_scores_score_nonnegative"),
    )

    criterion = db.relationship("Criterion")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<EvaluationScore id={self.id} evaluation_id={self.evaluation_id} "
            f"criterion_id={self.criterion_id} score={self.score}>"
        )
