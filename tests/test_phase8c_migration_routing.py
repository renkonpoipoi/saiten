"""Phase 8C-1で判明した migration connection routing バグのregression test。

## バグの内容

`migrations/env.py` は `config.set_main_option('sqlalchemy.url', ...)` へ
MIGRATION_DATABASE_URL を書き込んでいたが、この値を読むのは
`run_migrations_offline()` だけだった。online mode
(`flask db upgrade` の通常実行)は Flask-Migrate 既定テンプレートのまま
`connectable = get_engine()` を使っており、これはアプリ本体のengine
(= DATABASE_URL = 本番ではNeon **pooled**)である。

その結果:

- offline (`--sql`) → MIGRATION_DATABASE_URL (direct) を使う
- online  (`db upgrade`) → DATABASE_URL (pooled) を使う

という不整合が生じ、本番のRender build (`flask db upgrade`) は
pooled connection 経由でDDLを流していた。

## 検証方針

Neon(本番)へは一切接続しない。SQLiteの一時ファイルを
「runtime用」「migration用」の2つに分け、`flask db upgrade` が
**どちらのファイルへ書いたか**で接続先選択を実証する。
修正前は runtime 側のファイルにschemaが作られ、migration側は
そもそも生成されなかった。
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.config import build_config, resolve_migration_url

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PY = REPO_ROOT / "migrations" / "env.py"
PHASE8_REVISION = "9c4e17a2b8d3"
# `flask db upgrade` は常に head まで進む。ここで見たいのは
# 「どちらのDBへ書いたか」であって head revision 値そのものではない。
HEAD_REVISION = "d5b81c37f0ae"

# 親プロセス(開発者のshell)には本番Neonの接続文字列が入っている場合がある。
# subprocessへ絶対に継承させない。
_DB_ENV_KEYS = ("DATABASE_URL", "MIGRATION_DATABASE_URL", "APP_ENV", "SECRET_KEY")


def _clean_env(**overrides: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _DB_ENV_KEYS}
    env["FLASK_APP"] = "wsgi.py"
    env["SECRET_KEY"] = "migration-routing-test-only"
    env.update(overrides)
    return env


def _run_flask_db(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [".venv/bin/flask", "db", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


def _run_ok(env: dict, *args: str) -> str:
    result = _run_flask_db(env, *args)
    assert result.returncode == 0, f"{args}: {result.stderr}"
    return result.stdout


def _alembic_version(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        conn.close()


def _columns(path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


@pytest.fixture()
def split_urls(tmp_path):
    """runtime用 / migration用に別々のSQLiteファイルを割り当てる。

    本番の「DATABASE_URL=pooled / MIGRATION_DATABASE_URL=direct」を、
    実接続を伴わずに再現できる最小の構成。
    """
    runtime = tmp_path / "runtime.sqlite3"
    migration = tmp_path / "migration.sqlite3"
    return (
        runtime,
        migration,
        _clean_env(
            APP_ENV="development",
            DATABASE_URL=f"sqlite:///{runtime}",
            MIGRATION_DATABASE_URL=f"sqlite:///{migration}",
        ),
    )


# ---------------------------------------------------------------------------
# A. online migration が MIGRATION_DATABASE_URL 側へ接続すること
# ---------------------------------------------------------------------------


def test_online_upgrade_uses_migration_database_url(split_urls):
    """`flask db upgrade`(online)が MIGRATION_DATABASE_URL 側へ適用されること。

    修正前はruntime側(=本番ならpooled)へ適用されていた。
    """
    runtime, migration, env = split_urls
    _run_ok(env, "upgrade")

    assert migration.exists(), "migration側DBが作成されていない(routingが効いていない)"
    assert _alembic_version(migration) == HEAD_REVISION
    assert "presentation_mode" in _columns(migration, "projects")
    assert {"presentation_status", "locked_at", "presented_at"} <= _columns(
        migration, "subjects"
    )


def test_online_upgrade_does_not_touch_runtime_database_url(split_urls):
    """runtime(DATABASE_URL)側へは一切書き込まないこと。"""
    runtime, _migration, env = split_urls
    _run_ok(env, "upgrade")

    assert not runtime.exists(), (
        "runtime(DATABASE_URL)側のDBが作られている。"
        "online migrationがpooled connectionへ接続している。"
    )


def test_online_current_and_check_use_migration_database_url(split_urls):
    """current / check といった他のonlineコマンドも同じ接続先を使うこと。"""
    _runtime, _migration, env = split_urls
    _run_ok(env, "upgrade")

    assert HEAD_REVISION in _run_ok(env, "current")
    assert "No new upgrade operations detected" in _run_ok(env, "check")


def test_online_downgrade_uses_migration_database_url(split_urls):
    """downgradeもmigration側へ向くこと(routingがコマンド横断であること)。"""
    runtime, migration, env = split_urls
    _run_ok(env, "upgrade")
    _run_ok(env, "downgrade", "b37d61517847")

    assert _alembic_version(migration) == "b37d61517847"
    assert "presentation_mode" not in _columns(migration, "projects")
    assert not runtime.exists()


# ---------------------------------------------------------------------------
# B. MIGRATION_DATABASE_URL 未設定時は DATABASE_URL へfallback
# ---------------------------------------------------------------------------


def test_online_upgrade_falls_back_to_database_url(tmp_path):
    runtime = tmp_path / "fallback.sqlite3"
    env = _clean_env(APP_ENV="development", DATABASE_URL=f"sqlite:///{runtime}")
    assert "MIGRATION_DATABASE_URL" not in env

    _run_ok(env, "upgrade")

    assert runtime.exists()
    assert _alembic_version(runtime) == HEAD_REVISION
    assert "presentation_mode" in _columns(runtime, "projects")


def test_resolve_migration_url_falls_back_when_absent():
    config = build_config(
        {"APP_ENV": "production", "SECRET_KEY": "x", "DATABASE_URL": "postgres://u:p@pooled/db"}
    )
    # build_config()自体がDATABASE_URLへfallbackさせている
    assert config["MIGRATION_DATABASE_URL"] == "postgresql+psycopg://u:p@pooled/db"
    # configにキーが無い場合(testing config等)もruntime URLへ落ちる
    assert (
        resolve_migration_url({}, "sqlite:///runtime.sqlite3")
        == "sqlite:///runtime.sqlite3"
    )
    assert (
        resolve_migration_url({"MIGRATION_DATABASE_URL": None}, "sqlite:///runtime.sqlite3")
        == "sqlite:///runtime.sqlite3"
    )


# ---------------------------------------------------------------------------
# C. offline migration も MIGRATION_DATABASE_URL を優先すること
# ---------------------------------------------------------------------------


def test_offline_sql_uses_migration_database_url_dialect(tmp_path):
    """runtime=SQLite / migration=PostgreSQL のとき、PostgreSQL方言で出力される。"""
    env = _clean_env(
        APP_ENV="development",
        DATABASE_URL=f"sqlite:///{tmp_path / 'runtime.sqlite3'}",
        MIGRATION_DATABASE_URL="postgresql://u:p@localhost/db",
    )
    sql = _run_ok(env, "upgrade", "--sql")

    assert "TIMESTAMP WITH TIME ZONE" in sql, sql
    assert "DATETIME" not in sql, "SQLite方言で生成されている(offline routing崩れ)"


def test_offline_sql_migration_url_wins_over_postgres_runtime(tmp_path):
    """逆向き(runtime=PostgreSQL / migration=SQLite)でもmigration側が勝つこと。"""
    env = _clean_env(
        APP_ENV="development",
        DATABASE_URL="postgresql://u:p@localhost/db",
        MIGRATION_DATABASE_URL=f"sqlite:///{tmp_path / 'offline.sqlite3'}",
    )
    sql = _run_ok(env, "upgrade", "--sql")

    assert "DATETIME" in sql, sql
    assert "TIMESTAMP WITH TIME ZONE" not in sql


def test_offline_and_online_agree_on_target(split_urls):
    """同一設定で offline が示す接続先と online の実書き込み先が一致すること。"""
    runtime, migration, env = split_urls
    # offlineはDDLを出すだけでDBを作らない
    _run_ok(env, "upgrade", "--sql")
    assert not migration.exists()
    assert not runtime.exists()

    _run_ok(env, "upgrade")
    assert migration.exists()
    assert not runtime.exists()


# ---------------------------------------------------------------------------
# D. URL normalization / query parameter の保持
# ---------------------------------------------------------------------------


def test_migration_url_keeps_query_parameters_and_driver():
    config = build_config(
        {
            "APP_ENV": "production",
            "SECRET_KEY": "x",
            "DATABASE_URL": (
                "postgresql://u:p@ep-x-pooler.neon.tech/db"
                "?sslmode=require&channel_binding=require"
            ),
            "MIGRATION_DATABASE_URL": (
                "postgres://u:p@ep-x.neon.tech/db"
                "?sslmode=require&channel_binding=require"
            ),
        }
    )
    resolved = resolve_migration_url(config, config["SQLALCHEMY_DATABASE_URI"])

    assert resolved == config["MIGRATION_DATABASE_URL"]
    assert "-pooler" not in resolved

    url = make_url(resolved)
    assert url.drivername == "postgresql+psycopg"
    assert url.query["sslmode"] == "require"
    assert url.query["channel_binding"] == "require"


def test_dedicated_migration_engine_preserves_url(tmp_path):
    """resolveされたURLからengineを作っても、driver/query paramが失われないこと。

    create_engine()は接続を張らないため、実DBは不要。
    """
    config = build_config(
        {
            "APP_ENV": "production",
            "SECRET_KEY": "x",
            "DATABASE_URL": "postgresql://u:p@ep-x-pooler.neon.tech/db?sslmode=require",
            "MIGRATION_DATABASE_URL": "postgresql://u:p@ep-x.neon.tech/db?sslmode=require",
        }
    )
    resolved = resolve_migration_url(config, config["SQLALCHEMY_DATABASE_URI"])
    engine = create_engine(resolved, poolclass=NullPool)
    try:
        assert engine.url.drivername == "postgresql+psycopg"
        assert engine.url.query["sslmode"] == "require"
        assert "-pooler" not in engine.url.host
    finally:
        engine.dispose()


def test_pooled_runtime_uri_is_unchanged_by_the_fix():
    """アプリ本体のengine URLは従来どおりDATABASE_URL(pooled)のままであること。"""
    config = build_config(
        {
            "APP_ENV": "production",
            "SECRET_KEY": "x",
            "DATABASE_URL": "postgresql://u:p@ep-x-pooler.neon.tech/db?sslmode=require",
            "MIGRATION_DATABASE_URL": "postgresql://u:p@ep-x.neon.tech/db?sslmode=require",
        }
    )
    assert "-pooler" in config["SQLALCHEMY_DATABASE_URI"]
    assert config["SQLALCHEMY_ENGINE_OPTIONS"]["pool_size"] == 3


# ---------------------------------------------------------------------------
# env.py の構造的なガード(Flask-Migrateテンプレートの再コピーによる再発防止)
# ---------------------------------------------------------------------------


def test_env_py_online_path_does_not_use_runtime_engine_directly():
    source = ENV_PY.read_text(encoding="utf-8")
    assert "connectable = get_engine()" not in source, (
        "run_migrations_online() が runtime engine を直接使っている"
        "(Flask-Migrate既定テンプレートに戻っている)"
    )
    assert "with migration_connectable() as connectable:" in source


def test_env_py_disposes_only_the_dedicated_migration_engine():
    source = ENV_PY.read_text(encoding="utf-8")
    assert "poolclass=NullPool" in source
    assert "migration_engine.dispose()" in source
    # runtime engine(アプリ本体のpool)をdisposeしていないこと
    assert "runtime_engine.dispose()" not in source
    assert "get_engine().dispose()" not in source
