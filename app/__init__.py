"""App Factory。"""

from __future__ import annotations

from flask import Flask, jsonify
from flask_wtf.csrf import CSRFError

from app.config import build_config
from app.errors import AppError
from app.extensions import csrf, db, limiter, migrate


def create_app(env_override: dict | None = None) -> Flask:
    app = Flask(__name__)

    config = build_config(env_override)
    app.config.update(config)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    limiter.init_app(app)

    # Flask-Migrateのautogenerateがテーブルを検出できるよう、
    # モデルをアプリコンテキスト構築時に必ずimportしておく。
    from app import models  # noqa: F401

    _register_error_handlers(app)
    _register_security_headers(app)
    _register_blueprints(app)

    return app


def _register_security_headers(app: Flask) -> None:
    """低コストで副作用の少ないHTTP security headerを付与する。

    script-src/style-srcを含む本格的なCSPは、現構成が
    (a) テンプレート内のinline <script>によるID受け渡し
    (b) 多数のinline style属性
    に依存しているため、nonce基盤の追加なしには導入できない。
    今回はframe-ancestors(フレーム埋め込み制御のみでJS/CSSに影響しない)に
    留め、完全なCSPはPhase 7以降の課題とする。
    """

    @app.after_request
    def _apply_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
        return response


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(AppError)
    def _handle_app_error(err: AppError):
        return jsonify(err.to_dict()), err.status_code

    @app.errorhandler(CSRFError)
    def _handle_csrf_error(err: CSRFError):
        return jsonify({"error": "CSRF token missing or invalid."}), 400

    @app.errorhandler(429)
    def _handle_rate_limit(err):  # noqa: ARG001
        return jsonify({"error": "Too many requests. Please try again later."}), 429


def _register_blueprints(app: Flask) -> None:
    from app.routes.pages import pages_bp
    from app.routes.api_auth import api_auth_bp
    from app.routes.api_projects import api_projects_bp
    from app.routes.api_scoring import api_scoring_bp
    from app.routes.api_host import api_host_bp
    from app.routes.api_result import api_result_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(api_auth_bp)
    app.register_blueprint(api_projects_bp)
    app.register_blueprint(api_scoring_bp)
    app.register_blueprint(api_host_bp)
    app.register_blueprint(api_result_bp)
