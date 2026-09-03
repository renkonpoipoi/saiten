"""アプリ全体で共通のエラーレスポンス形式。

サービス層はここで定義した例外(またはそのサブクラス)を送出し、
create_app()に登録したエラーハンドラがJSON `{"error": "..."}` +
適切なHTTP status codeへ変換する(実装計画の「error response形式を
可能な範囲で統一する」方針)。
"""

from __future__ import annotations


class AppError(Exception):
    status_code = 400

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code

    def to_dict(self) -> dict:
        return {"error": self.message}


class ValidationError(AppError):
    """入力値不正。"""

    status_code = 400


class ForbiddenError(AppError):
    """権限不足・cross-projectアクセス等。"""

    status_code = 403


class NotFoundError(AppError):
    status_code = 404


class ConflictError(AppError):
    """状態遷移違反・確定済みデータへの書き込み等。"""

    status_code = 409
