"""Phase 8-0: 2本目migration (add presentation modes) の検証。

Phase 7Aで「SQLite上のautogenerateがPostgreSQLで壊れるDDLを生成する」罠を踏んだため、
Phase 8では SQLite / PostgreSQL の双方を明示的に検証する。

- PostgreSQL: 実接続を持たない offline (`flask db upgrade --sql`) でDDLを検証する。
- SQLite: 一時ファイルDBに **実際に** migrationをonline適用し、schemaと制約の
  実挙動、および既存データの互換性を検証する。

Neon(本番)へは一切接続しない。
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSIONS_DIR = REPO_ROOT / "migrations" / "versions"
INITIAL_MIGRATION = VERSIONS_DIR / "b37d61517847_initial_schema.py"
PHASE8_MIGRATION = VERSIONS_DIR / "9c4e17a2b8d3_add_presentation_modes.py"

INITIAL_REVISION = "b37d61517847"
PHASE8_REVISION = "9c4e17a2b8d3"

# Neon本番へ適用済みのinitial migrationは、いかなる理由でも書き換えてはならない。
# 変更されるとNeonのalembic_versionと実schemaの対応が崩れる。
INITIAL_MIGRATION_SHA256 = (
    "a16c7bce74ce0e27ea9dce7965d6501dd1e5fcaf868de281ae447900add11d45"
)


def _flask_env(database_url: str) -> dict:
    return dict(
        os.environ,
        FLASK_APP="wsgi.py",
        APP_ENV="production" if database_url.startswith("postgresql") else "development",
        SECRET_KEY="migration-test-only",
        DATABASE_URL=database_url,
        MIGRATION_DATABASE_URL=database_url,
    )


def _run_flask_db(database_url: str, *args: str) -> str:
    result = subprocess.run(
        [".venv/bin/flask", "db", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=_flask_env(database_url),
    )
    assert result.returncode == 0, f"{args}: {result.stderr}"
    return result.stdout


def _offline_sql(database_url: str, *args: str) -> str:
    return _run_flask_db(database_url, "upgrade", "--sql", *args)


@pytest.fixture()
def sqlite_url(tmp_path):
    return f"sqlite:///{tmp_path / 'phase8.sqlite3'}", tmp_path / "phase8.sqlite3"


# ---------------------------------------------------------------------------
# 1. initial migrationの不変性 / 2. revision chain
# ---------------------------------------------------------------------------


def test_initial_migration_is_never_modified():
    """Neon本番へ適用済みのinitial migrationがバイト単位で不変であること。"""
    digest = hashlib.sha256(INITIAL_MIGRATION.read_bytes()).hexdigest()
    assert digest == INITIAL_MIGRATION_SHA256, (
        "b37d61517847_initial_schema.py が変更されている。"
        "Neon本番へ適用済みのため絶対に書き換えてはならない。"
    )


def test_phase8_migration_follows_initial_migration():
    source = PHASE8_MIGRATION.read_text(encoding="utf-8")
    assert f"revision = '{PHASE8_REVISION}'" in source
    assert f"down_revision = '{INITIAL_REVISION}'" in source


def test_only_two_migrations_exist():
    revisions = sorted(p.name for p in VERSIONS_DIR.glob("*.py"))
    assert revisions == [
        "9c4e17a2b8d3_add_presentation_modes.py",
        "b37d61517847_initial_schema.py",
    ]


def test_phase8_migration_is_expand_only():
    """ADD COLUMN のみ。DROP/RENAME/型変更/DEFAULT変更を含まないこと。"""
    import ast

    tree = ast.parse(PHASE8_MIGRATION.read_text(encoding="utf-8"))
    ops = {}
    for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
        ops[fn.name] = [
            node.func.attr
            for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "op"
        ]

    assert ops["upgrade"] == ["add_column"] * 4
    assert ops["downgrade"] == ["drop_column"] * 4

    # 説明文(module docstring)を除いた実コードだけを検査する
    module = ast.parse(PHASE8_MIGRATION.read_text(encoding="utf-8"))
    module.body = [
        node
        for node in module.body
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
    ]
    code = ast.unparse(module)

    for forbidden in ("alter_column", "drop_table", "rename", "op.execute"):
        assert forbidden not in code, f"expand-only違反: {forbidden}"
    # 既存行への明示UPDATEを発行しないこと(server_defaultによる暗黙backfillのみ)
    assert "UPDATE" not in code.upper()


# ---------------------------------------------------------------------------
# 3. 空SQLiteへの通し適用 / 5. 実schemaでのCHECK制約
# ---------------------------------------------------------------------------


def test_sqlite_upgrade_from_empty_database(sqlite_url):
    url, path = sqlite_url
    _run_flask_db(url, "upgrade")

    conn = sqlite3.connect(path)
    try:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert version == PHASE8_REVISION

        project_cols = {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
        assert "presentation_mode" in project_cols

        subject_cols = {row[1] for row in conn.execute("PRAGMA table_info(subjects)")}
        assert {"presentation_status", "locked_at", "presented_at"} <= subject_cols
    finally:
        conn.close()


def test_sqlite_check_constraints_are_actually_enforced(sqlite_url):
    """SQLite実DB上でも新CHECK制約が効くこと(PostgreSQLとのparity)。"""
    url, path = sqlite_url
    _run_flask_db(url, "upgrade")

    conn = sqlite3.connect(path)
    try:
        schema = {
            name: sql
            for name, sql in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table'"
            )
        }
        assert "ck_projects_presentation_mode" in schema["projects"]
        assert "ck_subjects_presentation_status" in schema["subjects"]

        conn.execute(
            "INSERT INTO projects (id, name, status, host_code_hash, allow_host_scoring,"
            " created_at, updated_at, presentation_mode)"
            " VALUES (1,'p','DRAFT','h',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'SEQUENTIAL')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO projects (id, name, status, host_code_hash, allow_host_scoring,"
                " created_at, updated_at, presentation_mode)"
                " VALUES (2,'p','DRAFT','h2',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'BOGUS')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO subjects (id, project_id, name, sort_order, created_at,"
                " presentation_status) VALUES (1,1,'s',0,CURRENT_TIMESTAMP,'BOGUS')"
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 4. 既存データ互換 (本番Neonの既存BATCH Projectを模した検証)
# ---------------------------------------------------------------------------


def test_existing_pre_phase8_rows_survive_and_default_to_batch(sqlite_url):
    """initialだけ適用したDB(=Phase 8以前の本番相当)にデータを入れてから
    Phase 8 migrationを適用しても、行が失われずBATCH/WAITINGとして読めること。
    """
    url, path = sqlite_url
    _run_flask_db(url, "upgrade", INITIAL_REVISION)

    conn = sqlite3.connect(path)
    try:
        assert (
            conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == INITIAL_REVISION
        )
        # Phase 8以前のカラム構成のみでINSERTする(新カラムは存在しない)
        assert "presentation_mode" not in {
            row[1] for row in conn.execute("PRAGMA table_info(projects)")
        }
        conn.execute(
            "INSERT INTO projects (id, name, status, host_code_hash, allow_host_scoring,"
            " created_at, updated_at, finished_at)"
            " VALUES (1,'本番スモークテスト','FINISHED','existinghash',0,"
            " CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        conn.executemany(
            "INSERT INTO subjects (id, project_id, name, sort_order, created_at)"
            " VALUES (?,?,?,?,CURRENT_TIMESTAMP)",
            [(1, 1, "チームA", 0), (2, 1, "チームB", 1)],
        )
        conn.commit()
    finally:
        conn.close()

    _run_flask_db(url, "upgrade")

    conn = sqlite3.connect(path)
    try:
        assert (
            conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == PHASE8_REVISION
        )
        rows = conn.execute(
            "SELECT id, name, status, presentation_mode FROM projects"
        ).fetchall()
        assert rows == [(1, "本番スモークテスト", "FINISHED", "BATCH")]

        subjects = conn.execute(
            "SELECT id, name, presentation_status, locked_at, presented_at"
            " FROM subjects ORDER BY sort_order"
        ).fetchall()
        assert subjects == [
            (1, "チームA", "WAITING", None, None),
            (2, "チームB", "WAITING", None, None),
        ]
    finally:
        conn.close()


def test_sqlite_downgrade_restores_previous_schema(sqlite_url):
    url, path = sqlite_url
    _run_flask_db(url, "upgrade")
    _run_flask_db(url, "downgrade", INITIAL_REVISION)

    conn = sqlite3.connect(path)
    try:
        assert (
            conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == INITIAL_REVISION
        )
        assert "presentation_mode" not in {
            row[1] for row in conn.execute("PRAGMA table_info(projects)")
        }
        assert "presentation_status" not in {
            row[1] for row in conn.execute("PRAGMA table_info(subjects)")
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 6-8. PostgreSQL offline SQL 検証
# ---------------------------------------------------------------------------


def _postgres_upgrade_sql() -> str:
    return _offline_sql("postgresql://u:p@localhost/db")


def test_postgres_add_column_has_default_and_named_check():
    sql = _postgres_upgrade_sql()

    mode = re.search(
        r"ALTER TABLE projects ADD COLUMN presentation_mode ([^;]+);", sql
    )
    assert mode, "presentation_mode の ADD COLUMN が見つからない"
    clause = mode.group(1)
    assert "VARCHAR(16)" in clause
    assert "DEFAULT 'BATCH'" in clause
    assert "NOT NULL" in clause
    assert "CONSTRAINT ck_projects_presentation_mode" in clause
    assert "CHECK (presentation_mode IN ('BATCH','SEQUENTIAL'))" in clause

    status = re.search(
        r"ALTER TABLE subjects ADD COLUMN presentation_status ([^;]+);", sql
    )
    assert status, "presentation_status の ADD COLUMN が見つからない"
    clause = status.group(1)
    assert "DEFAULT 'WAITING'" in clause
    assert "NOT NULL" in clause
    assert "CONSTRAINT ck_subjects_presentation_status" in clause
    assert (
        "CHECK (presentation_status IN ('WAITING','SCORING','LOCKED','PRESENTED'))"
        in clause
    )


def test_postgres_timestamp_columns_are_nullable_without_default():
    """SQLiteのADD COLUMNは非定数DEFAULTを許さないため、両dialectで
    nullable / DEFAULT無しになっていること。"""
    sql = _postgres_upgrade_sql()
    for column in ("locked_at", "presented_at"):
        line = re.search(rf"ALTER TABLE subjects ADD COLUMN {column} ([^;]+);", sql)
        assert line, f"{column} の ADD COLUMN が見つからない"
        assert "TIMESTAMP WITH TIME ZONE" in line.group(1)
        assert "DEFAULT" not in line.group(1)
        assert "NOT NULL" not in line.group(1)


def test_postgres_sql_contains_no_sqlite_specific_expressions():
    sql = _postgres_upgrade_sql()
    for token in ("AUTOINCREMENT", "PRAGMA", "sqlite_"):
        assert token not in sql, f"SQLite固有表現が含まれている: {token}"


def test_postgres_sql_has_no_table_rebuild():
    """batch_alter_tableによるテーブル再構築を行っていないこと。"""
    sql = _postgres_upgrade_sql()
    assert "_alembic_tmp" not in sql
    for token in ("DROP TABLE projects", "DROP TABLE subjects"):
        assert token not in sql


def test_phase8_add_column_sql_is_identical_across_dialects(tmp_path):
    """新カラムのADD COLUMN句がSQLite/PostgreSQLで同一表現になること
    (= schema parityが取れていることの直接的な証拠)。"""
    pg = _postgres_upgrade_sql()
    lite = _offline_sql(f"sqlite:///{tmp_path / 'offline.sqlite3'}")

    for column in ("presentation_mode", "presentation_status"):
        pg_line = re.search(rf"ADD COLUMN {column} ([^;]+);", pg).group(1)
        lite_line = re.search(rf"ADD COLUMN {column} ([^;]+);", lite).group(1)
        assert pg_line == lite_line, f"{column}: {pg_line!r} != {lite_line!r}"


def test_downgrade_sql_generates_for_both_dialects(tmp_path):
    for url in (
        "postgresql://u:p@localhost/db",
        f"sqlite:///{tmp_path / 'down.sqlite3'}",
    ):
        sql = _run_flask_db(
            url, "downgrade", "--sql", f"{PHASE8_REVISION}:{INITIAL_REVISION}"
        )
        for column in ("presentation_mode", "presentation_status", "locked_at", "presented_at"):
            assert f"DROP COLUMN {column}" in sql, f"{url}: {column} のDROPが無い"


# ---------------------------------------------------------------------------
# 9. model metadata と migration適用後schemaの一致
# ---------------------------------------------------------------------------


def test_flask_db_check_reports_no_drift(sqlite_url):
    """migrationを適用したschemaとmodel metadataに差分が無いこと。"""
    url, _ = sqlite_url
    _run_flask_db(url, "upgrade")
    output = _run_flask_db(url, "check")
    assert "No new upgrade operations detected" in output, output
