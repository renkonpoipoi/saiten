from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, text

from app.extensions import db

# Subject単位の進行状態。SEQUENTIAL modeでのみ権威を持つ値で、BATCH modeでは
# 参照しない(BATCHのSubject表示状態はProject.statusから導出する。
# project_service.subject_presentation_status()を参照)。
# WAITING -> SCORING -> LOCKED -> PRESENTED の片道遷移のみ。
SUBJECT_PRESENTATION_STATUSES = ("WAITING", "SCORING", "LOCKED", "PRESENTED")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Subject(db.Model):
    """被採点者/チーム。"""

    __tablename__ = "subjects"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer, db.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name = db.Column(db.String(80), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0, server_default=text("0"))
    # CheckConstraintをcolumn levelで宣言する理由はProject.presentation_modeと同じ
    # (2本目migrationのADD COLUMNが出力するinline DDLと同一表現にし、SQLiteでも
    #  table rebuild無しで同じCHECK制約を持たせるため)。
    presentation_status = db.Column(
        db.String(16),
        db.CheckConstraint(
            "presentation_status IN ('WAITING','SCORING','LOCKED','PRESENTED')",
            name="ck_subjects_presentation_status",
        ),
        nullable=False,
        default="WAITING",
        server_default=text("'WAITING'"),
    )
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )
    locked_at = db.Column(db.DateTime(timezone=True))
    presented_at = db.Column(db.DateTime(timezone=True))

    __table_args__ = (
        db.UniqueConstraint("project_id", "sort_order", name="ux_subjects_project_order"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Subject id={self.id} project_id={self.project_id} name={self.name!r}>"
