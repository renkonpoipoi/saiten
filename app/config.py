"""アプリの環境別設定を組み立てるモジュール。

方針(実装計画 v2 より):
- APP_ENV自体を必須環境変数とする。未設定/不明な値は起動失敗させる
  (development へのデフォルトフォールバックは行わない)。
- production は DATABASE_URL を必須とする。未設定なら起動失敗させる
  (SQLiteへの暗黙フォールバックは行わない)。
- Neonなどが返す postgres://, postgresql:// 形式のURLは
  postgresql+psycopg:// へ正規化する。クエリパラメータ(sslmode等)は
  文字列置換の対象にせず、そのまま素通しする。
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

VALID_APP_ENVS = ("development", "testing", "production")


class ConfigError(RuntimeError):
    """起動時の設定不備を表す例外。意図的に起動を失敗させるために使う。"""


def normalize_pg_url(url: str) -> str:
    """postgres:// / postgresql:// を postgresql+psycopg:// に正規化する。

    スキーム部分の文字列置換のみを行い、"?sslmode=require" 等の
    クエリパラメータを含む残りの部分には一切手を加えない。
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and not url.startswith("postgresql+psycopg://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _require(source: dict, name: str, *, context: str = "") -> str:
    value = source.get(name)
    if not value:
        suffix = f" ({context})" if context else ""
        raise ConfigError(f"{name} is required but not set{suffix}.")
    return value


def build_config(env: dict | None = None) -> dict:
    """Flask の app.config.update() にそのまま渡せる設定dictを返す。

    env を渡すとテストで os.environ の代わりに使える
    (テストごとにモジュール再importをせずに済ませるため)。
    """
    source = env if env is not None else os.environ

    app_env = source.get("APP_ENV")
    if not app_env:
        raise ConfigError(
            "APP_ENV is required. Set one of: development, testing, production."
        )
    if app_env not in VALID_APP_ENVS:
        raise ConfigError(
            f"Unknown APP_ENV={app_env!r}. Must be one of {VALID_APP_ENVS}."
        )

    secret_key = _require(source, "SECRET_KEY", context="required in all environments")

    config: dict = {
        "APP_ENV": app_env,
        "SECRET_KEY": secret_key,
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "RATELIMIT_STORAGE_URI": "memory://",
    }

    if app_env == "production":
        database_url = _require(source, "DATABASE_URL", context="required when APP_ENV=production")
        migration_url = source.get("MIGRATION_DATABASE_URL") or database_url
        config.update(
            {
                "SQLALCHEMY_DATABASE_URI": normalize_pg_url(database_url),
                "MIGRATION_DATABASE_URL": normalize_pg_url(migration_url),
                "SESSION_COOKIE_SECURE": True,
                "SQLALCHEMY_ENGINE_OPTIONS": {
                    "pool_pre_ping": True,
                    "pool_recycle": 300,
                    "pool_size": 3,
                    "max_overflow": 2,
                },
            }
        )
    elif app_env == "testing":
        db_uri = source.get("SQLALCHEMY_DATABASE_URI") or "sqlite:///:memory:"
        config.update(
            {
                "SQLALCHEMY_DATABASE_URI": db_uri,
                "SESSION_COOKIE_SECURE": False,
                # デフォルトはテスト簡略化のため無効。CSRF/rate limit自体を
                # 検証したいテストは env_override で明示的に True を渡す。
                # (Flask-LimiterのenabledはLimiter.init_app()実行時に固定
                # されるため、app.config.update()による後からの変更は
                # 反映されない。CSRFProtect側はリクエスト都度configを見る
                # ため後から変更しても効くが、統一のためどちらもここで
                # source経由の上書きを許可する)
                "WTF_CSRF_ENABLED": bool(source.get("WTF_CSRF_ENABLED", False)),
                "RATELIMIT_ENABLED": bool(source.get("RATELIMIT_ENABLED", False)),
            }
        )
    else:  # development
        default_sqlite = f"sqlite:///{DATA_DIR / 'dev.sqlite3'}"
        database_url = source.get("DATABASE_URL") or default_sqlite
        normalized = (
            database_url if database_url.startswith("sqlite") else normalize_pg_url(database_url)
        )
        migration_url = source.get("MIGRATION_DATABASE_URL") or database_url
        config.update(
            {
                "SQLALCHEMY_DATABASE_URI": normalized,
                "MIGRATION_DATABASE_URL": (
                    migration_url if migration_url.startswith("sqlite") else normalize_pg_url(migration_url)
                ),
                "SESSION_COOKIE_SECURE": False,
            }
        )

    return config
