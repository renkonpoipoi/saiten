from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import false, func, text

from app.extensions import db

PROJECT_STATUSES = ("DRAFT", "SCORING", "LOCKED", "PRESENTING", "FINISHED")

# 結果発表方式。BATCHは全Subjectの採点完了後にまとめて発表する従来方式、
# SEQUENTIALはSubject単位で「採点->締切->発表」を繰り返すM-1方式。
# 既存Project(Phase 8以前に作られたもの)はserver_defaultによりBATCHになる。
PRESENTATION_MODES = ("BATCH", "SEQUENTIAL")

# 被採点者の発表順の決め方。
# MANUAL:      Host Settings の並び順(Subject.sort_order)がそのまま正式な発表順。
# RANDOM_DRAW: 採点開始時にサーバーが秘密順(Subject.draw_order)を一度だけ確定し、
#              本番中に1組ずつ抽選で公開する。sort_order は管理画面上の表示順として残る。
SUBJECT_ORDER_MODES = ("MANUAL", "RANDOM_DRAW")

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
    # CheckConstraintをcolumn levelで宣言する理由は presentation_mode と同じ。
    subject_order_mode = db.Column(
        db.String(16),
        db.CheckConstraint(
            "subject_order_mode IN ('MANUAL','RANDOM_DRAW')",
            name="ck_projects_subject_order_mode",
        ),
        nullable=False,
        default="MANUAL",
        server_default=text("'MANUAL'"),
    )
    # RANDOM_DRAWで既に公開した抽選の件数。「公開済み」の唯一の定義であり、
    # subjects.draw_order < projects.draw_cursor が公開済みを意味する。
    # 抽選はこの値へのcompare-and-swapで進めるので、retryや同時POSTでも
    # 2組進んだり同じ組を2回消費したりしない(drawn_at のような別の真実源は持たない)。
    draw_cursor = db.Column(
        db.Integer, nullable=False, default=0, server_default=text("0")
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
