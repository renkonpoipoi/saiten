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
