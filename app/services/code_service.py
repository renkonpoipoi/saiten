"""Host/参加コードの生成・ハッシュ化・検証を行う純粋関数群。

DBアクセスは行わない(呼び出し側がhash値をモデルに保存/比較する)。
参加コードは secrets.token_urlsafe 由来の高エントロピーなランダムトークン
であり人間が選ぶパスワードではないため、bcrypt等の低速ハッシュは使わず
SHA-256 + UNIQUE INDEXでのO(1)ルックアップを採用する(実装計画 v1 10節)。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

HOST_CODE_PREFIX = "host_"
SCORER_CODE_PREFIX = "scr_"

_TOKEN_BYTES = 18  # secrets.token_urlsafeへ渡すバイト数(概ね24文字のURL-safe文字列になる)


def generate_host_code() -> str:
    """平文のホストコードを生成する。呼び出し側は一度だけ表示し、DBにはhashのみ保存する。"""
    return f"{HOST_CODE_PREFIX}{secrets.token_urlsafe(_TOKEN_BYTES)}"


def generate_scorer_code() -> str:
    """平文の参加コードを生成する。呼び出し側は一度だけ表示し、DBにはhashのみ保存する。"""
    return f"{SCORER_CODE_PREFIX}{secrets.token_urlsafe(_TOKEN_BYTES)}"


def hash_code(plain_code: str) -> str:
    """コード文字列全体(prefix込み)のSHA-256 hex digestを返す。"""
    return hashlib.sha256(plain_code.encode("utf-8")).hexdigest()


def verify_code(plain_code: str, code_hash: str) -> bool:
    """平文コードがハッシュ値と一致するかを定時間比較で検証する。"""
    return hmac.compare_digest(hash_code(plain_code), code_hash)
