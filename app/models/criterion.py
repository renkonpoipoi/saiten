from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, text

from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Criterion(db.Model):
    """採点軸。個数・名称はプロジェクトごとに可変(MVPでは5件運用)。"""

    __tablename__ = "criteria"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer, db.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(240))
    max_score = db.Column(db.Integer, nullable=False, default=20, server_default=text("20"))
    sort_order = db.Column(db.Integer, nullable=False, default=0, server_default=text("0"))
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )

    __table_args__ = (
        db.UniqueConstraint("project_id", "sort_order", name="ux_criteria_project_order"),
        db.CheckConstraint("max_score > 0", name="ck_criteria_max_score_positive"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Criterion id={self.id} project_id={self.project_id} name={self.name!r}>"
