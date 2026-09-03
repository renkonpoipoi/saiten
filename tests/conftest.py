"""pytest共通fixture。

SQLiteインメモリDBを使い、Postgresでは不要だがSQLiteではデフォルト無効な
外部キー制約をイベントリスナーで強制的に有効化する(実装計画 v2 20節)。
"""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app import create_app
from app.extensions import db as _db

TEST_ENV = {
    "APP_ENV": "testing",
    "SECRET_KEY": "test-secret",
}


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):  # noqa: ARG001
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture()
def app():
    application = create_app(dict(TEST_ENV))
    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def db(app):  # noqa: ARG001 (appの存在によりapp_context/テーブル作成を保証する)
    return _db


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def app_with_security():
    """CSRF保護とrate limitを有効化した状態のアプリ(通常はテスト簡略化の
    ためtesting configで無効化されているが、これらの機構自体の検証に使う)。

    Flask-Limiterの有効/無効はLimiter.init_app()実行時(=create_app()内)に
    固定されるため、生成後のapp.config.update()では反映されない。
    env_override経由でcreate_app()に渡す必要がある。
    """
    env = dict(TEST_ENV, WTF_CSRF_ENABLED=True, RATELIMIT_ENABLED=True)
    application = create_app(env)
    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def client_with_security(app_with_security):
    return app_with_security.test_client()


@pytest.fixture()
def app_with_ratelimit_only():
    """rate limitだけを検証したいテスト用(CSRFは無効のまま)。

    CSRF保護も同時に有効だと、CSRFトークン欠如による400が
    rate limit超過(429)より先に返ってしまいテストの意図が
    曖昧になるため、機構ごとに切り分けて検証する。
    """
    env = dict(TEST_ENV, RATELIMIT_ENABLED=True)
    application = create_app(env)
    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def client_with_ratelimit_only(app_with_ratelimit_only):
    return app_with_ratelimit_only.test_client()
