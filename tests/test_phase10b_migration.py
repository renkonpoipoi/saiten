"""Phase 10B-1: 並び替え・抽選のための列を追加する migration。

検証方針は Phase 8C / 10A と同じで、**本番(Neon)へは一切接続しない。**
一時ディレクトリ(pytest の tmp_path)の使い捨て SQLite に対してのみ
`flask db upgrade` を走らせる。data/dev.sqlite3 と scores.sqlite3 には触れない。
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

PHASE10A_REVISION = "c1f7a04b9e26"
PHASE10B_REVISION = "d5b81c37f0ae"
DRAW_INDEX = "ux_subjects_project_draw"

_DB_ENV_KEYS = ("DATABASE_URL", "MIGRATION_DATABASE_URL", "APP_ENV", "SECRET_KEY")


def _clean_env(**overrides: str) -> dict:
    """親プロセスの DATABASE_URL 等を絶対に継承させない。

    これが無いと APP_ENV=development のとき app/config.py の既定で
    data/dev.sqlite3 へ接続してしまう(開発DBを壊す事故の原因)。
    """
    env = {k: v for k, v in os.environ.items() if k not in _DB_ENV_KEYS}
    env["FLASK_APP"] = "wsgi.py"
    env["SECRET_KEY"] = "phase10b-migration-test-only"
    env["APP_ENV"] = "development"
    env.update(overrides)
    return env


def _run_ok(env: dict, *args: str) -> str:
    result = subprocess.run(
        [".venv/bin/flask", "db", *args],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, f"{args}: {result.stderr}"
    return result.stdout


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _seed_legacy(path: Path) -> None:
    """Phase 10B 以前に作られた Project を再現する。"""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO projects (id, name, status, host_code_hash,"
            " allow_host_scoring, presentation_mode, created_at, updated_at)"
            " VALUES (1, '旧Project', 'DRAFT', ?, 0, 'BATCH',"
            " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (_hash("host_legacy"),),
        )
        for index, name in enumerate(["チームA", "チームB"]):
            conn.execute(
                "INSERT INTO subjects (id, project_id, name, sort_order,"
                " presentation_status, created_at)"
                " VALUES (?, 1, ?, ?, 'WAITING', CURRENT_TIMESTAMP)",
                (index + 1, name, index),
            )
        # 作成順(id順)が意味を持つ採点者
        for index, name in enumerate(["吉田", "田中", "佐藤"]):
            conn.execute(
                "INSERT INTO scorers (id, project_id, display_name,"
                " access_code_hash, is_host_scorer, is_active, created_at)"
                " VALUES (?, 1, ?, ?, 0, 1, CURRENT_TIMESTAMP)",
                (index + 1, name, _hash(f"scr_{index}")),
            )
        conn.commit()
    finally:
        conn.close()


def _rows(path: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _columns(path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _index_sql(path: Path) -> str | None:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (DRAW_INDEX,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _version(path: Path) -> str:
    return _rows(path, "SELECT version_num FROM alembic_version")[0][0]


@pytest.fixture()
def legacy_db(tmp_path):
    """10A revision まで上げて旧データを入れた、使い捨ての SQLite。"""
    path = tmp_path / "phase10b.sqlite3"
    env = _clean_env(DATABASE_URL=f"sqlite:///{path}")
    _run_ok(env, "upgrade", PHASE10A_REVISION)
    _seed_legacy(path)
    return path, env


# ---------------------------------------------------------------------------
# revision と方針
# ---------------------------------------------------------------------------


def test_phase10b_revision_extends_the_chain():
    revisions = sorted(p.name for p in MIGRATIONS_DIR.glob("*.py"))
    assert revisions == [
        "9c4e17a2b8d3_add_presentation_modes.py",
        "b37d61517847_initial_schema.py",
        "c1f7a04b9e26_enforce_one_host_scorer_per_project.py",
        "d5b81c37f0ae_add_ordering_and_draw_columns.py",
    ], revisions

    source = (MIGRATIONS_DIR / "d5b81c37f0ae_add_ordering_and_draw_columns.py").read_text(
        encoding="utf-8"
    )
    assert f"revision = '{PHASE10B_REVISION}'" in source
    assert f"down_revision = '{PHASE10A_REVISION}'" in source


def _is_docstring(node) -> bool:
    import ast

    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def test_phase10b_migration_is_expand_only():
    """既存カラムを触らず、既存行へのUPDATEも発行しない。"""
    import ast

    path = MIGRATIONS_DIR / "d5b81c37f0ae_add_ordering_and_draw_columns.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    upgrade = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
    )
    source = ast.unparse(upgrade)

    for forbidden in ("drop_column", "alter_column", "batch_alter_table",
                      "op.execute", "op.bulk_insert", "UPDATE "):
        assert forbidden not in source, forbidden
    assert source.count("op.add_column(") == 4
    assert "op.create_index(" in source

    # downgrade は追加したものを消すだけ
    downgrade = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
    )
    down_source = ast.unparse(downgrade)
    assert down_source.count("op.drop_column(") == 4
    assert down_source.count("op.drop_index(") == 1
    assert "op.execute" not in down_source


def test_phase10b_migration_is_a_single_revision():
    """Phase 10B で足す migration は1本だけ。"""
    added = [
        p for p in MIGRATIONS_DIR.glob("*.py")
        if p.name.startswith("d5b81c37f0ae")
    ]
    assert len(added) == 1


# ---------------------------------------------------------------------------
# upgrade / downgrade
# ---------------------------------------------------------------------------


def test_upgrade_adds_every_column(legacy_db):
    path, env = legacy_db
    _run_ok(env, "upgrade")

    assert _version(path) == PHASE10B_REVISION
    assert {"subject_order_mode", "draw_cursor"} <= _columns(path, "projects")
    assert "draw_order" in _columns(path, "subjects")
    assert "sort_order" in _columns(path, "scorers")

    sql = _index_sql(path)
    assert sql is not None
    assert "UNIQUE INDEX" in sql
    assert "subjects (project_id, draw_order)" in sql


def test_upgrade_defaults_existing_rows_without_writing_them(legacy_db):
    """★ 既存行へ UPDATE を発行せず、server_default だけで既定値になること。"""
    path, env = legacy_db
    before = _rows(path, "SELECT id, display_name FROM scorers ORDER BY id")

    _run_ok(env, "upgrade")

    assert _rows(path, "SELECT id, display_name FROM scorers ORDER BY id") == before
    assert _rows(path, "SELECT subject_order_mode, draw_cursor FROM projects") == [
        ("MANUAL", 0)
    ]
    assert _rows(path, "SELECT draw_order FROM subjects ORDER BY id") == [(None,), (None,)]
    assert _rows(path, "SELECT sort_order FROM scorers ORDER BY id") == [(0,), (0,), (0,)]


def test_existing_scorer_order_is_preserved(legacy_db):
    """★ 全員 sort_order=0 でも (sort_order, id) で従来の作成順になること。"""
    path, env = legacy_db
    _run_ok(env, "upgrade")

    ordered = _rows(
        path, "SELECT display_name FROM scorers ORDER BY sort_order, id"
    )
    assert [r[0] for r in ordered] == ["吉田", "田中", "佐藤"]


def test_legacy_project_still_loads_after_upgrade(legacy_db):
    path, env = legacy_db
    _run_ok(env, "upgrade")

    from app import create_app
    from app.models import Project, Scorer
    from app.services import project_service

    app = create_app({
        "APP_ENV": "testing", "SECRET_KEY": "x",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}",
    })
    with app.app_context():
        project = Project.query.filter_by(id=1).one()
        assert project.subject_order_mode == "MANUAL"
        assert project.draw_cursor == 0
        scorers = (
            Scorer.query.filter_by(project_id=1)
            .order_by(Scorer.sort_order, Scorer.id)
            .all()
        )
        assert [s.display_name for s in scorers] == ["吉田", "田中", "佐藤"]
        progress = project_service.get_progress(project)
        assert [s["display_name"] for s in progress["scorers"]] == ["吉田", "田中", "佐藤"]


def test_draw_order_uniqueness_allows_many_nulls(legacy_db):
    """MANUAL(全件NULL)と共存できること。"""
    path, env = legacy_db
    _run_ok(env, "upgrade")

    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO subjects (project_id, name, sort_order, presentation_status,"
            " created_at, draw_order) VALUES (1, 'C', 9, 'WAITING', CURRENT_TIMESTAMP, NULL)"
        )
        conn.commit()
        assert _rows(path, "SELECT COUNT(*) FROM subjects WHERE draw_order IS NULL") == [(3,)]

        conn.execute("UPDATE subjects SET draw_order = 0 WHERE id = 1")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE subjects SET draw_order = 0 WHERE id = 2")
            conn.commit()
    finally:
        conn.rollback()
        conn.close()


def test_downgrade_removes_everything(legacy_db):
    path, env = legacy_db
    _run_ok(env, "upgrade")
    before = _rows(path, "SELECT id, display_name FROM scorers ORDER BY id")

    _run_ok(env, "downgrade", PHASE10A_REVISION)

    assert _version(path) == PHASE10A_REVISION
    assert _index_sql(path) is None
    assert "draw_order" not in _columns(path, "subjects")
    assert "sort_order" not in _columns(path, "scorers")
    assert _rows(path, "SELECT id, display_name FROM scorers ORDER BY id") == before


def test_upgrade_downgrade_upgrade_is_repeatable(legacy_db):
    path, env = legacy_db
    before = _rows(path, "SELECT id, display_name FROM scorers ORDER BY id")

    _run_ok(env, "upgrade")
    _run_ok(env, "downgrade", PHASE10A_REVISION)
    _run_ok(env, "upgrade")

    assert _version(path) == PHASE10B_REVISION
    assert _rows(path, "SELECT id, display_name FROM scorers ORDER BY id") == before


# ---------------------------------------------------------------------------
# 両 dialect の DDL(接続はしない)
# ---------------------------------------------------------------------------


def _offline_sql(url: str, app_env: str) -> str:
    env = _clean_env(APP_ENV=app_env, DATABASE_URL=url, MIGRATION_DATABASE_URL=url)
    return _run_ok(env, "upgrade", "--sql", f"{PHASE10A_REVISION}:{PHASE10B_REVISION}")


def test_offline_sql_is_identical_on_both_dialects(tmp_path):
    sqlite_sql = _offline_sql(f"sqlite:///{tmp_path / 'offline.sqlite3'}", "development")
    postgres_sql = _offline_sql("postgresql://u:p@localhost/db", "production")

    for statement in (
        "ALTER TABLE projects ADD COLUMN subject_order_mode VARCHAR(16)",
        "ALTER TABLE projects ADD COLUMN draw_cursor INTEGER DEFAULT 0 NOT NULL",
        "ALTER TABLE subjects ADD COLUMN draw_order INTEGER",
        "ALTER TABLE scorers ADD COLUMN sort_order INTEGER DEFAULT 0 NOT NULL",
        f"CREATE UNIQUE INDEX {DRAW_INDEX} ON subjects (project_id, draw_order)",
    ):
        assert statement in sqlite_sql, statement
        assert statement in postgres_sql, statement


def test_offline_sql_has_no_table_rebuild_or_data_change(tmp_path):
    for url, app_env in (
        (f"sqlite:///{tmp_path / 'offline2.sqlite3'}", "development"),
        ("postgresql://u:p@localhost/db", "production"),
    ):
        sql = _offline_sql(url, app_env).upper()
        assert "CREATE TABLE" not in sql
        assert "DROP TABLE" not in sql
        assert "INSERT INTO" not in sql
        updates = [line for line in sql.splitlines() if line.strip().startswith("UPDATE")]
        assert all("ALEMBIC_VERSION" in line for line in updates), updates
