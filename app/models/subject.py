from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, text

from app.extensions import db


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
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )

    __table_args__ = (
        db.UniqueConstraint("project_id", "sort_order", name="ux_subjects_project_order"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Subject id={self.id} project_id={self.project_id} name={self.name!r}>"
