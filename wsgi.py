"""gunicorn / flask CLI のエントリポイント。

APP_ENV等の必須環境変数が未設定の場合、create_app()がConfigErrorを送出して
起動に失敗する(意図した挙動。実装計画 v2 3節)。
"""

from app import create_app

app = create_app()
