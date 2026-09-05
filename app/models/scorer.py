from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import false, func, text, true

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
    # 本番の座席順など、Hostが決める並び順。Judge rail / 得点開示順の根拠になる。
    # 既存Projectはmigrationで全員0になるため、読み出しは必ず
    # (sort_order, id) で並べる。0で並んだ場合はid順=従来の作成順に一致する。
    sort_order = db.Column(db.Integer, nullable=False, default=0, server_default=text("0"))
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=true())
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )

    evaluations = db.relationship(
        "Evaluation", backref="scorer", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        db.UniqueConstraint("access_code_hash", name="uq_scorers_access_code_hash"),
        # 1 Projectにつき is_host_scorer=true は最大1人。部分UNIQUE INDEXは
        # PostgreSQL / SQLite の双方が対応しており、同一のDDLになる
        # (migration c1f7a04b9e26 と対応。テーブル再構築は不要)。
        #
        # この制約があるため、Host roleの付け替えは
        # 「旧Hostを降ろす -> flush -> 新Hostを立てる」の順序で行う必要がある
        # (project_service.set_host_scorer を参照)。
        db.Index(
            "uq_scorers_one_host_per_project",
            "project_id",
            unique=True,
            sqlite_where=text("is_host_scorer"),
            postgresql_where=text("is_host_scorer"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Scorer id={self.id} project_id={self.project_id} display_name={self.display_name!r}>"
