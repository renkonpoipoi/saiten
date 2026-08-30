"""Flask拡張のインスタンスをここでのみ生成する。

models/やroutes/からの循環importを避けるため、init_app()呼び出しは
app/__init__.py のcreate_app()に集約する。
"""

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address)
