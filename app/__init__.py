"""App Factory。

Phase 1時点ではモデル定義とDB接続の疎通確認が目的のため、
routes/ blueprintの登録はまだ行わない(Phase 2以降)。
"""

from __future__ import annotations

from flask import Flask

from app.config import build_config
from app.extensions import csrf, db, limiter, migrate


def create_app(env_override: dict | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)

    config = build_config(env_override)
    app.config.update(config)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    limiter.init_app(app)

    # Flask-Migrateのautogenerateがテーブルを検出できるよう、
    # モデルをアプリコンテキスト構築時に必ずimportしておく。
    from app import models  # noqa: F401

    return app
