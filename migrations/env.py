import logging
from contextlib import contextmanager
from logging.config import fileConfig

from flask import current_app
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from alembic import context

from app.config import resolve_migration_url

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
fileConfig(config.config_file_name)
logger = logging.getLogger('alembic.env')


def get_engine():
    """アプリ本体(runtime)が使うFlask-SQLAlchemyのengineを返す。

    これは DATABASE_URL (本番ではNeon pooled) に紐づくengineであり、
    migrationの接続先とは限らない。migration側は get_migration_url() /
    migration_connectable() を使うこと。
    """
    try:
        # this works with Flask-SQLAlchemy<3 and Alchemical
        return current_app.extensions['migrate'].db.get_engine()
    except (TypeError, AttributeError):
        # this works with Flask-SQLAlchemy>=3
        return current_app.extensions['migrate'].db.engine


def render_url(url) -> str:
    """SQLAlchemyのURLオブジェクトを、password込みの文字列へ戻す。"""
    try:
        return url.render_as_string(hide_password=False)
    except AttributeError:
        return str(url)


def get_engine_url():
    """runtime engineのURL(alembic.iniへ書き込むため '%' をescapeする)。"""
    return render_url(get_engine().url).replace('%', '%%')


def get_migration_url() -> str:
    """schema migrationが接続すべきURLを返す。**online/offline共通の唯一の決定点**。

    - MIGRATION_DATABASE_URL が設定されていればそれを使う
      (本番ではNeonのdirect connection)。
    - 未設定ならruntime engineのURL(=DATABASE_URL相当)へfallbackする。

    値は create_app() の build_config() が既に normalize 済み
    (postgres:// / postgresql:// → postgresql+psycopg://、query parameterは
    そのまま保持)なので、ここでURLを再加工しない。
    """
    return resolve_migration_url(
        current_app.config, render_url(get_engine().url)
    )


@contextmanager
def migration_connectable():
    """migrationを実行するconnectableを供給するcontext manager。

    MIGRATION_DATABASE_URL が runtime engine と別の接続先を指している場合
    (本番: runtime=pooled / migration=direct)のみ、そのURL専用のengineを
    一時的に生成する。DDLを1回流すだけなので NullPool を使い、抜けるときに
    必ず dispose() する。

    接続先が同一の場合(MIGRATION_DATABASE_URL未設定、SQLiteでの開発、
    テスト等)は、engineを増やさずruntime engineをそのまま使う。この場合は
    アプリ本体のconnection poolなので **dispose()してはならない**。
    """
    runtime_engine = get_engine()
    migration_url = get_migration_url()

    if render_url(make_url(migration_url)) == render_url(runtime_engine.url):
        yield runtime_engine
        return

    logger.info(
        'Using dedicated migration engine from MIGRATION_DATABASE_URL '
        '(runtime DATABASE_URL is left untouched).'
    )
    migration_engine = create_engine(migration_url, poolclass=NullPool)
    try:
        yield migration_engine
    finally:
        migration_engine.dispose()


# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata

# schema migrationには常にNeonのdirect connection(MIGRATION_DATABASE_URL)を
# 使う。設定されていなければ(ローカルSQLite開発時など)DATABASE_URL相当の
# runtime engine URLにフォールバックする(実装計画 v2 17節)。
config.set_main_option('sqlalchemy.url', get_migration_url().replace('%', '%%'))
target_db = current_app.extensions['migrate'].db

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def get_metadata():
    if hasattr(target_db, 'metadatas'):
        return target_db.metadatas[None]
    return target_db.metadata


def run_migrations_offline():
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=get_metadata(), literal_binds=True
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    # this callback is used to prevent an auto-migration from being generated
    # when there are no changes to the schema
    # reference: http://alembic.zzzcomputing.com/en/latest/cookbook.html
    def process_revision_directives(context, revision, directives):
        if getattr(config.cmd_opts, 'autogenerate', False):
            script = directives[0]
            if script.upgrade_ops.is_empty():
                directives[:] = []
                logger.info('No changes in schema detected.')

    conf_args = current_app.extensions['migrate'].configure_args
    if conf_args.get("process_revision_directives") is None:
        conf_args["process_revision_directives"] = process_revision_directives

    # Flask-Migrateの既定テンプレートはここで get_engine() を使うため、
    # sqlalchemy.url に入れた MIGRATION_DATABASE_URL が online modeでは
    # 無視され、DATABASE_URL(pooled)へ接続してしまっていた。
    # online/offlineで同じ決定ロジックを通すため connectable を差し替える。
    with migration_connectable() as connectable:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=get_metadata(),
                **conf_args
            )

            with context.begin_transaction():
                context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
