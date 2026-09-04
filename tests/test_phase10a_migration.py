"""Phase 10A-5: 「1 Projectにつき is_host_scorer=true は最大1人」を課す
部分UNIQUE INDEXのmigration。

検証方針は Phase 8C のmigration testと同じで、**本番(Neon)へは一切接続しない。**
一時ディレクトリのSQLiteファイルに対して実際に `flask db upgrade` を走らせ、

  - 旧revisionまで上げて「旧方式で作られたProject」を投入する
  - 新revisionへupgradeしても既存行が1件も書き換わらない
  - indexが実際に2人目のhost scorerを弾く
  - downgradeがindexのdropだけで、行を触らない

ことを確かめる。PostgreSQL向けのDDLは offline (`--sql`) 生成で確認する
(接続はしない)。
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations" / "versions"

PHASE8_REVISION = "9c4e17a2b8d3"
PHASE10A_REVISION = "c1f7a04b9e26"
INDEX_NAME = "uq_scorers_one_host_per_project"

# 親プロセスに本番Neonの接続文字列が入っている場合がある。
# subprocessへ絶対に継承させない。
_DB_ENV_KEYS = ("DATABASE_URL", "MIGRATION_DATABASE_URL", "APP_ENV", "SECRET_KEY")


def _clean_env(**overrides: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _DB_ENV_KEYS}
    env["FLASK_APP"] = "wsgi.py"
    env["SECRET_KEY"] = "phase10a-migration-test-only"
    env["APP_ENV"] = "development"
    env.update(overrides)
    return env


def _run_ok(env: dict, *args: str) -> str:
    result = subprocess.run(
        [".venv/bin/flask", "db", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"{args}: {result.stderr}"
    return result.stdout


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _seed_legacy_rows(path: Path) -> None:
    """Phase 10A以前の方式で作られたProjectを再現する。

    project 1: 採点者3名 + 合成された「ホスト」Scorer (is_host_scorer=1)
    project 2: ホスト兼任なし
    """
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO projects (id, name, status, host_code_hash,"
            " allow_host_scoring, presentation_mode, created_at, updated_at)"
            " VALUES (1, '旧Project', 'DRAFT', ?, 1, 'BATCH',"
            " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (_hash("host_legacy_1"),),
        )
        for index, (name, flag) in enumerate(
            [("吉田", 0), ("田中", 0), ("佐藤", 0), ("ホスト", 1)], start=1
        ):
            conn.execute(
                "INSERT INTO scorers (id, project_id, display_name,"
                " access_code_hash, is_host_scorer, is_active, created_at)"
                " VALUES (?, 1, ?, ?, ?, 1, CURRENT_TIMESTAMP)",
                (index, name, _hash(f"scr_legacy_{index}"), flag),
            )
        conn.execute(
            "INSERT INTO projects (id, name, status, host_code_hash,"
            " allow_host_scoring, presentation_mode, created_at, updated_at)"
            " VALUES (2, '兼任なし', 'DRAFT', ?, 0, 'SEQUENTIAL',"
            " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (_hash("host_legacy_2"),),
        )
        conn.execute(
            "INSERT INTO scorers (id, project_id, display_name,"
            " access_code_hash, is_host_scorer, is_active, created_at)"
            " VALUES (9, 2, 'A', ?, 0, 1, CURRENT_TIMESTAMP)",
            (_hash("scr_legacy_9"),),
        )
        conn.commit()
    finally:
        conn.close()


def _scorer_rows(path: Path) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT id, project_id, display_name, is_host_scorer, access_code_hash"
            " FROM scorers ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def _index_sql(path: Path) -> str | None:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (INDEX_NAME,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _alembic_version(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture()
def legacy_db(tmp_path):
    """旧revisionまで上げて旧方式のデータを入れたSQLiteファイル。"""
    path = tmp_path / "phase10a.sqlite3"
    env = _clean_env(DATABASE_URL=f"sqlite:///{path}")
    _run_ok(env, "upgrade", PHASE8_REVISION)
    _seed_legacy_rows(path)
    return path, env


# ---------------------------------------------------------------------------
# revision の並び
# ---------------------------------------------------------------------------


def test_phase10a_revision_extends_the_existing_chain():
    revisions = sorted(p.name for p in MIGRATIONS_DIR.glob("*.py"))
    assert revisions == [
        "9c4e17a2b8d3_add_presentation_modes.py",
        "b37d61517847_initial_schema.py",
        "c1f7a04b9e26_enforce_one_host_scorer_per_project.py",
    ], revisions

    source = (
        MIGRATIONS_DIR / "c1f7a04b9e26_enforce_one_host_scorer_per_project.py"
    ).read_text(encoding="utf-8")
    assert f"revision = '{PHASE10A_REVISION}'" in source
    assert f"down_revision = '{PHASE8_REVISION}'" in source


def test_phase10a_migration_is_expand_only():
    """ADD/DROP COLUMN も table rebuild も既存行のUPDATEも行わない。

    方針を説明したdocstringには用語が出てくるので、実コードだけを検査する。
    """
    import ast

    path = MIGRATIONS_DIR / "c1f7a04b9e26_enforce_one_host_scorer_per_project.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    body = [node for node in tree.body if not _is_docstring(node)]
    source = "\n".join(ast.unparse(node) for node in body)

    for forbidden in (
        "add_column",
        "drop_column",
        "alter_column",
        "batch_alter_table",
        "op.execute",
        "op.bulk_insert",
        "UPDATE ",
    ):
        assert forbidden not in source, forbidden
    assert "op.create_index(" in source
    # downgrade は index の drop だけ
    downgrade = source[source.index("def downgrade():"):]
    assert downgrade.count("op.") == 1
    assert "op.drop_index(" in downgrade


def _is_docstring(node) -> bool:
    import ast

    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


# ---------------------------------------------------------------------------
# 実際の upgrade / downgrade (ローカルSQLiteのみ)
# ---------------------------------------------------------------------------


def test_upgrade_creates_the_partial_unique_index(legacy_db):
    path, env = legacy_db
    assert _index_sql(path) is None

    _run_ok(env, "upgrade")

    assert _alembic_version(path) == PHASE10A_REVISION
    sql = _index_sql(path)
    assert sql is not None
    assert "UNIQUE INDEX" in sql
    assert "scorers (project_id)" in sql
    assert "WHERE is_host_scorer" in sql


def test_upgrade_does_not_touch_any_existing_row(legacy_db):
    """★ 既存Projectのデータを1件も書き換えないこと。"""
    path, env = legacy_db
    before = _scorer_rows(path)

    _run_ok(env, "upgrade")

    after = _scorer_rows(path)
    assert after == before
    # 旧方式の合成Scorerもそのまま残る(backfillしない)
    assert ("ホスト", 1) in [(row[2], row[3]) for row in after]


def test_legacy_projects_still_load_after_the_migration(legacy_db):
    """migration後も既存Projectを通常どおり読み込めること。"""
    path, env = legacy_db
    _run_ok(env, "upgrade")

    from app import create_app
    from app.models import Project, Scorer
    from app.services import project_service

    app = create_app(
        {
            "APP_ENV": "testing",
            "SECRET_KEY": "x",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}",
        }
    )
    with app.app_context():
        legacy = Project.query.filter_by(id=1).one()
        assert legacy.allow_host_scoring is True
        scorers = Scorer.query.filter_by(project_id=1).order_by(Scorer.id).all()
        assert [s.display_name for s in scorers] == ["吉田", "田中", "佐藤", "ホスト"]
        assert [s.is_host_scorer for s in scorers] == [False, False, False, True]

        # 進捗などの既存クエリもそのまま通る
        progress = project_service.get_progress(legacy)
        assert len(progress["scorers"]) == 4
        assert progress["project_status"] == "DRAFT"


def test_index_rejects_a_second_host_scorer_after_upgrade(legacy_db):
    path, env = legacy_db
    _run_ok(env, "upgrade")

    conn = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE scorers SET is_host_scorer = 1 WHERE id = 1")
            conn.commit()
    finally:
        conn.rollback()
        conn.close()


def test_index_is_scoped_per_project(legacy_db):
    """別Projectならそれぞれ1人ずつhost scorerを持てる。"""
    path, env = legacy_db
    _run_ok(env, "upgrade")

    conn = sqlite3.connect(path)
    try:
        conn.execute("UPDATE scorers SET is_host_scorer = 1 WHERE id = 9")
        conn.commit()
        rows = conn.execute(
            "SELECT project_id, COUNT(*) FROM scorers WHERE is_host_scorer"
            " GROUP BY project_id ORDER BY project_id"
        ).fetchall()
        assert rows == [(1, 1), (2, 1)]
    finally:
        conn.close()


def test_reassignment_works_against_the_migrated_schema(legacy_db):
    """★ index が張られた実DBに対して、Host roleの付け替えが成功すること。

    project_service.set_host_scorer() は
    「旧Hostを降ろす -> flush -> 新Hostを立てる」の順序を守っているので、
    一時的に2人trueになってUNIQUE違反することがない。
    """
    path, env = legacy_db
    _run_ok(env, "upgrade")

    from app import create_app
    from app.models import Project, Scorer
    from app.services import project_service

    app = create_app(
        {
            "APP_ENV": "testing",
            "SECRET_KEY": "x",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}",
        }
    )
    with app.app_context():
        project = Project.query.filter_by(id=1).one()
        rows = Scorer.query.filter_by(project_id=1).order_by(Scorer.id).all()
        legacy_host = rows[3]
        assert legacy_host.display_name == "ホスト"

        # id大 -> id小 (flush順が逆転しうる向き)
        project_service.set_host_scorer(project, rows[0])
        flags = [
            s.is_host_scorer
            for s in Scorer.query.filter_by(project_id=1).order_by(Scorer.id).all()
        ]
        assert flags == [True, False, False, False]

        # id小 -> id大
        project_service.set_host_scorer(project, rows[2])
        flags = [
            s.is_host_scorer
            for s in Scorer.query.filter_by(project_id=1).order_by(Scorer.id).all()
        ]
        assert flags == [False, False, True, False]

        # 旧「ホスト」Scorerは自動削除されない
        assert Scorer.query.filter_by(project_id=1, display_name="ホスト").count() == 1


def test_downgrade_only_drops_the_index(legacy_db):
    path, env = legacy_db
    _run_ok(env, "upgrade")
    before = _scorer_rows(path)

    _run_ok(env, "downgrade", PHASE8_REVISION)

    assert _alembic_version(path) == PHASE8_REVISION
    assert _index_sql(path) is None
    assert _scorer_rows(path) == before


def test_upgrade_downgrade_upgrade_is_repeatable(legacy_db):
    path, env = legacy_db
    before = _scorer_rows(path)

    _run_ok(env, "upgrade")
    _run_ok(env, "downgrade", PHASE8_REVISION)
    _run_ok(env, "upgrade")

    assert _alembic_version(path) == PHASE10A_REVISION
    assert _index_sql(path) is not None
    assert _scorer_rows(path) == before


# ---------------------------------------------------------------------------
# PostgreSQL向けDDL (offline生成のみ。接続はしない)
# ---------------------------------------------------------------------------


def _offline_sql(url: str, app_env: str) -> str:
    env = _clean_env(APP_ENV=app_env, DATABASE_URL=url, MIGRATION_DATABASE_URL=url)
    return _run_ok(env, "upgrade", "--sql", f"{PHASE8_REVISION}:{PHASE10A_REVISION}")


def test_offline_sql_is_identical_on_both_dialects():
    sqlite_sql = _offline_sql("sqlite:///./data/offline-check.sqlite3", "development")
    postgres_sql = _offline_sql("postgresql://u:p@localhost/db", "production")

    statement = (
        f"CREATE UNIQUE INDEX {INDEX_NAME} ON scorers (project_id)"
        " WHERE is_host_scorer"
    )
    assert statement in sqlite_sql
    assert statement in postgres_sql


def test_offline_sql_contains_no_table_rebuild_or_data_change():
    for url, app_env in (
        ("sqlite:///./data/offline-check.sqlite3", "development"),
        ("postgresql://u:p@localhost/db", "production"),
    ):
        sql = _offline_sql(url, app_env).upper()
        assert "ALTER TABLE" not in sql
        assert "CREATE TABLE" not in sql
        assert "DROP TABLE" not in sql
        assert "INSERT INTO SCORERS" not in sql
        # alembic_version 以外へのUPDATEを出さない
        updates = [line for line in sql.splitlines() if line.strip().startswith("UPDATE")]
        assert all("ALEMBIC_VERSION" in line for line in updates), updates
