"""Phase 1完了条件の核: APP_ENV/SECRET_KEY/DATABASE_URLの必須化と
postgres URLの正規化を検証する。
"""

from __future__ import annotations

import pytest

from app.config import ConfigError, build_config, normalize_pg_url


def test_missing_app_env_raises():
    with pytest.raises(ConfigError, match="APP_ENV is required"):
        build_config({"SECRET_KEY": "x"})


def test_unknown_app_env_raises():
    with pytest.raises(ConfigError, match="Unknown APP_ENV"):
        build_config({"APP_ENV": "staging", "SECRET_KEY": "x"})


def test_missing_secret_key_raises():
    with pytest.raises(ConfigError, match="SECRET_KEY is required"):
        build_config({"APP_ENV": "development"})


def test_production_without_database_url_raises():
    """本番でDATABASE_URL未設定なら起動失敗する(暗黙のSQLiteフォールバック禁止)。"""
    with pytest.raises(ConfigError, match="DATABASE_URL is required"):
        build_config({"APP_ENV": "production", "SECRET_KEY": "x"})


def test_production_never_falls_back_to_sqlite_even_implicitly():
    with pytest.raises(ConfigError):
        config = build_config({"APP_ENV": "production", "SECRET_KEY": "x"})
        assert "sqlite" not in config.get("SQLALCHEMY_DATABASE_URI", "")


def test_production_with_database_url_normalizes_and_sets_engine_options():
    config = build_config(
        {
            "APP_ENV": "production",
            "SECRET_KEY": "x",
            "DATABASE_URL": "postgres://user:pw@ep-abc-pooler.region.aws.neon.tech/db?sslmode=require",
            "MIGRATION_DATABASE_URL": "postgres://user:pw@ep-abc.region.aws.neon.tech/db?sslmode=require",
        }
    )
    assert config["SQLALCHEMY_DATABASE_URI"] == (
        "postgresql+psycopg://user:pw@ep-abc-pooler.region.aws.neon.tech/db?sslmode=require"
    )
    assert config["MIGRATION_DATABASE_URL"] == (
        "postgresql+psycopg://user:pw@ep-abc.region.aws.neon.tech/db?sslmode=require"
    )
    assert config["SESSION_COOKIE_SECURE"] is True
    assert config["SQLALCHEMY_ENGINE_OPTIONS"]["pool_pre_ping"] is True


def test_development_defaults_to_local_sqlite():
    config = build_config({"APP_ENV": "development", "SECRET_KEY": "x"})
    assert config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:///")
    assert config["SQLALCHEMY_DATABASE_URI"].endswith("dev.sqlite3")
    assert config["SESSION_COOKIE_SECURE"] is False


def test_development_honors_explicit_database_url():
    config = build_config(
        {
            "APP_ENV": "development",
            "SECRET_KEY": "x",
            "DATABASE_URL": "postgresql://user:pw@ep-dev.neon.tech/db?sslmode=require",
        }
    )
    assert config["SQLALCHEMY_DATABASE_URI"] == (
        "postgresql+psycopg://user:pw@ep-dev.neon.tech/db?sslmode=require"
    )


def test_testing_disables_csrf_and_ratelimit():
    config = build_config({"APP_ENV": "testing", "SECRET_KEY": "x"})
    assert config["SQLALCHEMY_DATABASE_URI"] == "sqlite:///:memory:"
    assert config["WTF_CSRF_ENABLED"] is False
    assert config["RATELIMIT_ENABLED"] is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgres://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        (
            "postgres://u:p@h/db?sslmode=require&channel_binding=require",
            "postgresql+psycopg://u:p@h/db?sslmode=require&channel_binding=require",
        ),
    ],
)
def test_normalize_pg_url(raw, expected):
    assert normalize_pg_url(raw) == expected
