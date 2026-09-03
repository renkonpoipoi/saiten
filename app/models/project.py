from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import false, func, text

from app.extensions import db

PROJECT_STATUSES = ("DRAFT", "SCORING", "LOCKED", "PRESENTING", "FINISHED")

# 結果発表方式。BATCHは全Subjectの採点完了後にまとめて発表する従来方式、
# SEQUENTIALはSubject単位で「採点->締切->発表」を繰り返すM-1方式。
# 既存Project(Phase 8以前に作られたもの)はserver_defaultによりBATCHになる。
PRESENTATION_MODES = ("BATCH", "SEQUENTIAL")

# 全PK/FKはdb.Integer(SQLite/PostgreSQL双方でシンプルに動く整数型)を採用する。
# 実装計画v2のDDL例はPostgres向けにBIGINT/BIGSERIALを提示していたが、
# 本アプリの規模ではINTEGER(上限約21億)で十分であり、SQLiteのINTEGER
# PRIMARY KEY(ROWIDエイリアスによる自動採番)との相性を優先してIntegerに
# 統一している。


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    status = db.Column(
        db.String(16), nullable=False, default="DRAFT", server_default=text("'DRAFT'")
    )
    host_code_hash = db.Column(db.String(64), nullable=False)
    # CheckConstraintをcolumn levelで宣言しているのは、2本目migrationの
    # ADD COLUMNが出力するinline DDLと完全に同一の表現にするため。
    # SQLiteはALTER TABLE ... ADD CONSTRAINTを持たないが、ADD COLUMN時の
    # inline column constraintなら受け付けるので、これによりSQLiteと
    # PostgreSQLで同じCHECK制約を持てる(table rebuildは不要)。
    presentation_mode = db.Column(
        db.String(16),
        db.CheckConstraint(
            "presentation_mode IN ('BATCH','SEQUENTIAL')",
            name="ck_projects_presentation_mode",
        ),
        nullable=False,
        default="BATCH",
        server_default=text("'BATCH'"),
    )
    allow_host_scoring = db.Column(
        db.Boolean, nullable=False, default=False, server_default=false()
    )
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
    locked_at = db.Column(db.DateTime(timezone=True))
    presenting_at = db.Column(db.DateTime(timezone=True))
    finished_at = db.Column(db.DateTime(timezone=True))

    __table_args__ = (
        db.CheckConstraint(
            "status IN ('DRAFT','SCORING','LOCKED','PRESENTING','FINISHED')",
            name="ck_projects_status",
        ),
        db.UniqueConstraint("host_code_hash", name="uq_projects_host_code_hash"),
    )

    subjects = db.relationship(
        "Subject", backref="project", cascade="all, delete-orphan", passive_deletes=True
    )
    criteria = db.relationship(
        "Criterion", backref="project", cascade="all, delete-orphan", passive_deletes=True
    )
    scorers = db.relationship(
        "Scorer", backref="project", cascade="all, delete-orphan", passive_deletes=True
    )
    evaluations = db.relationship(
        "Evaluation", backref="project", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Project id={self.id} name={self.name!r} status={self.status}>"
