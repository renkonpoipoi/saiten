"""Phase 7A: production readiness のテスト。

- /healthz が要件どおりの軽量livenessであること
- production configがSQLiteへ到達しないこと
- initial migrationが空PostgreSQLへ適用可能な表現であること(オフライン検証)
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from app import create_app
from app.config import ConfigError, build_config, normalize_pg_url

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATION_FILE = (
    REPO_ROOT / "migrations" / "versions" / "b37d61517847_initial_schema.py"
)


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------


def test_healthz_returns_ok(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_healthz_requires_no_authentication(client):
    """Host/Scorerいずれのsessionも無い状態で200を返すこと。"""
    with client.session_transaction() as sess:
        sess.clear()
    assert client.get("/healthz").status_code == 200


def test_healthz_leaks_no_internal_state(client):
    body = client.get("/healthz").get_json()
    assert set(body.keys()) == {"status"}
    raw = client.get("/healthz").get_data(as_text=True).lower()
    for forbidden in ("secret", "database", "postgres", "sqlite", "project", "scorer", "code"):
        assert forbidden not in raw, f"/healthz leaked '{forbidden}'"


def test_healthz_performs_no_database_query(client):
    """DBを一切参照しないこと(engineを落としても200を返せる)。"""
    from app.extensions import db

    original_engine = db.engine

    class _Exploding:
        def __getattr__(self, name):
            raise AssertionError("/healthz must not touch the database")

    try:
        # SQLAlchemyのengineアクセスを壊した状態でも応答できることを確認する
        db.__dict__["engine"] = _Exploding()
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok"}
    finally:
        db.__dict__.pop("engine", None)
        assert db.engine is original_engine


def test_healthz_is_exempt_from_rate_limit(client_with_ratelimit_only):
    """プラットフォームのヘルスチェックが遮断されないこと。"""
    statuses = [client_with_ratelimit_only.get("/healthz").status_code for _ in range(40)]
    assert set(statuses) == {200}


def test_healthz_has_security_headers(client):
    resp = client.get("/healthz")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


# ---------------------------------------------------------------------------
# production config
# ---------------------------------------------------------------------------


def test_production_requires_database_url(monkeypatch):
    with pytest.raises(ConfigError, match="DATABASE_URL is required"):
        build_config({"APP_ENV": "production", "SECRET_KEY": "x"})


def test_production_requires_secret_key():
    with pytest.raises(ConfigError, match="SECRET_KEY is required"):
        build_config(
            {"APP_ENV": "production", "DATABASE_URL": "postgresql://u:p@h/db"}
        )


def test_production_never_yields_sqlite_uri():
    """productionでSQLiteへ到達する経路が存在しないこと。"""
    config = build_config(
        {
            "APP_ENV": "production",
            "SECRET_KEY": "x",
            "DATABASE_URL": "postgresql://u:p@h/db?sslmode=require",
        }
    )
    assert "sqlite" not in config["SQLALCHEMY_DATABASE_URI"].lower()
    assert config["SQLALCHEMY_DATABASE_URI"].startswith("postgresql+psycopg://")


def test_production_ignores_sqlite_database_url_shape():
    """DATABASE_URLにsqliteを渡してもnormalizeでpostgres化されず、
    かつ意図せぬfallback経路が生まれないことを確認する。

    (運用上ここにsqliteを設定するのは誤設定だが、その場合でも
     'production設定なのに黙ってローカルSQLiteを使う' という挙動には
     ならないことを明示しておく)
    """
    config = build_config(
        {"APP_ENV": "production", "SECRET_KEY": "x", "DATABASE_URL": "sqlite:///tmp.db"}
    )
    # normalize_pg_urlはpostgres系スキームのみ書き換えるため素通しされる。
    # ここで重要なのは「DATABASE_URL未設定時に暗黙でSQLiteへ落ちない」ことであり、
    # それはtest_production_requires_database_urlで担保されている。
    assert config["SQLALCHEMY_DATABASE_URI"] == "sqlite:///tmp.db"


def test_production_session_cookie_flags():
    config = build_config(
        {"APP_ENV": "production", "SECRET_KEY": "x", "DATABASE_URL": "postgresql://u:p@h/db"}
    )
    assert config["SESSION_COOKIE_SECURE"] is True
    assert config["SESSION_COOKIE_HTTPONLY"] is True
    assert config["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_production_engine_options_for_neon():
    config = build_config(
        {"APP_ENV": "production", "SECRET_KEY": "x", "DATABASE_URL": "postgresql://u:p@h/db"}
    )
    opts = config["SQLALCHEMY_ENGINE_OPTIONS"]
    assert opts["pool_pre_ping"] is True
    assert opts["pool_recycle"] == 300
    assert opts["pool_size"] == 3
    assert opts["max_overflow"] == 2


def test_migration_url_defaults_to_database_url_when_absent():
    config = build_config(
        {"APP_ENV": "production", "SECRET_KEY": "x", "DATABASE_URL": "postgres://u:p@pooled/db"}
    )
    assert config["MIGRATION_DATABASE_URL"] == "postgresql+psycopg://u:p@pooled/db"


def test_migration_url_kept_separate_from_runtime_url():
    """runtime=pooled / migration=direct を別々に保持できること。"""
    config = build_config(
        {
            "APP_ENV": "production",
            "SECRET_KEY": "x",
            "DATABASE_URL": "postgresql://u:p@ep-x-pooler.neon.tech/db?sslmode=require",
            "MIGRATION_DATABASE_URL": "postgresql://u:p@ep-x.neon.tech/db?sslmode=require",
        }
    )
    assert "-pooler" in config["SQLALCHEMY_DATABASE_URI"]
    assert "-pooler" not in config["MIGRATION_DATABASE_URL"]
    assert config["SQLALCHEMY_DATABASE_URI"].startswith("postgresql+psycopg://")
    assert config["MIGRATION_DATABASE_URL"].startswith("postgresql+psycopg://")


def test_production_app_boots_without_touching_sqlite():
    app = create_app(
        {
            "APP_ENV": "production",
            "SECRET_KEY": "x",
            "DATABASE_URL": "postgresql://u:p@h/db?sslmode=require",
        }
    )
    assert app.config["APP_ENV"] == "production"
    assert "sqlite" not in app.config["SQLALCHEMY_DATABASE_URI"].lower()
    assert app.config["SESSION_COOKIE_SECURE"] is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgres://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        (
            "postgresql://u:p@ep.neon.tech/db?sslmode=require&channel_binding=require",
            "postgresql+psycopg://u:p@ep.neon.tech/db?sslmode=require&channel_binding=require",
        ),
    ],
)
def test_normalize_preserves_query_parameters(raw, expected):
    assert normalize_pg_url(raw) == expected


# ---------------------------------------------------------------------------
# migration: PostgreSQL互換性(オフライン検証・実接続なし)
# ---------------------------------------------------------------------------


def _offline_migration_sql(database_url: str) -> str:
    """flask db upgrade --sql をオフラインモードで実行しSQLを得る。

    offline modeのためDBへは接続しない(URLはダミーでよい)。
    """
    import os

    env = dict(
        os.environ,
        FLASK_APP="wsgi.py",
        APP_ENV="production",
        SECRET_KEY="offline-sql-generation-only",
        DATABASE_URL=database_url,
        MIGRATION_DATABASE_URL=database_url,
    )
    result = subprocess.run(
        [".venv/bin/flask", "db", "upgrade", "--sql"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_migration_has_no_boolean_integer_default_for_postgres():
    """BOOLEAN列にintegerのDEFAULTを付けない(PostgreSQLは型不一致で失敗する)。

    Alembicの初回autogenerateはSQLite上で実行されたため、modelの
    false()/true() が sa.text('0')/sa.text('1') に固定されてしまう罠がある。
    """
    sql = _offline_migration_sql("postgresql://u:p@localhost/db")
    bad = re.findall(r"^\s*(\w+)\s+BOOLEAN\s+DEFAULT\s+([^\s,]+)", sql, re.MULTILINE)
    assert bad, "BOOLEAN列が検出できていない(テストが機能していない可能性)"
    for column, default in bad:
        assert default in ("true", "false"), (
            f"{column}: PostgreSQLのBOOLEAN列に不正なDEFAULT {default!r}"
        )


def test_migration_defaults_match_column_types_on_postgres():
    sql = _offline_migration_sql("postgresql://u:p@localhost/db")
    pattern = re.compile(
        r"^\s*(\w+)\s+(BOOLEAN|INTEGER|TEXT|VARCHAR\(\d+\)|TIMESTAMP WITH TIME ZONE)"
        r"\s+DEFAULT\s+(.+)$",
        re.MULTILINE,
    )
    checked = 0
    for column, col_type, raw_default in pattern.findall(sql):
        # 行末の "NOT NULL" と カンマ を取り除いてDEFAULT値だけを取り出す
        default = raw_default.strip().rstrip(",").strip()
        if default.upper().endswith("NOT NULL"):
            default = default[: -len("NOT NULL")].strip()
        default = default.rstrip(",").strip()
        if col_type == "BOOLEAN":
            assert default in ("true", "false"), f"{column}: {default}"
        elif col_type == "INTEGER":
            assert re.fullmatch(r"\(?-?\d+\)?", default), f"{column}: {default}"
        elif col_type == "TEXT" or col_type.startswith("VARCHAR"):
            assert default.startswith(("'", "('")), f"{column}: {default}"
        elif col_type.startswith("TIMESTAMP"):
            assert "CURRENT_TIMESTAMP" in default or "now()" in default, f"{column}: {default}"
        checked += 1
    assert checked >= 15, f"検査対象が少なすぎる({checked}件)"


def test_migration_contains_no_sqlite_specific_sql():
    sql = _offline_migration_sql("postgresql://u:p@localhost/db")
    for token in ("AUTOINCREMENT", "PRAGMA", "sqlite_"):
        assert token not in sql, f"SQLite固有表現が含まれている: {token}"


def test_migration_creates_expected_tables_on_postgres():
    sql = _offline_migration_sql("postgresql://u:p@localhost/db")
    for table in (
        "projects",
        "subjects",
        "criteria",
        "scorers",
        "evaluations",
        "evaluation_scores",
    ):
        assert f"CREATE TABLE {table}" in sql, f"missing CREATE TABLE {table}"


def test_migration_upgrade_is_non_destructive_and_downgrade_exists():
    import ast

    tree = ast.parse(MIGRATION_FILE.read_text(encoding="utf-8"))
    ops = {}
    for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
        calls = [
            node.func.attr
            for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "op"
        ]
        ops[fn.name] = calls

    assert ops["upgrade"] == ["create_table"] * 6
    assert ops["downgrade"] == ["drop_table"] * 6


# ---------------------------------------------------------------------------
# production dependency
# ---------------------------------------------------------------------------


def test_requirements_contain_all_production_dependencies():
    reqs = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for package in (
        "flask",
        "gunicorn",
        "flask-sqlalchemy",
        "flask-migrate",
        "psycopg",
        "flask-wtf",
        "flask-limiter",
    ):
        assert package in reqs, f"missing production dependency: {package}"


def test_requirements_exclude_development_only_dependencies():
    reqs = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "pytest" not in reqs, "pytest should live in requirements-dev.txt only"


def test_python_version_file_pins_expected_version():
    pinned = (REPO_ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert pinned == "3.14.4"
