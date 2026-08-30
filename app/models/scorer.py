from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import false, func, true

from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Scorer(db.Model):
    __tablename__ = "scorers"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer, db.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    display_name = db.Column(db.String(80), nullable=False)
    access_code_hash = db.Column(db.String(64), nullable=False)
    # (旧is_host) 表示用フラグのみ。Host権限の判定には絶対に使わない。
    # Host権限は projects.host_code_hash 起因のセッションでのみ判定する。
    is_host_scorer = db.Column(
        db.Boolean, nullable=False, default=False, server_default=false()
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=true())
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )

    evaluations = db.relationship(
        "Evaluation", backref="scorer", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        db.UniqueConstraint("access_code_hash", name="uq_scorers_access_code_hash"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Scorer id={self.id} project_id={self.project_id} display_name={self.display_name!r}>"
