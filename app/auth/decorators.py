"""API/画面ルートに付与する権限チェックデコレータの枠組み。

権限チェックは常にサーバー側で完結させ、フロントの表示制御(ボタンdisabled等)
はUXのためだけに存在させる(実装計画 v1 10節)。

Host権限は session["host_project_ids"] 起因、Scorer権限は session["scorer_id"]
起因でそれぞれ独立に判定し、Scorer.is_host_scorer(表示用フラグ)は権限判定に
一切使わない(実装計画 v2 11節)。

Phase 1時点ではJoin/Host Loginがまだ存在しないため、デコレータの形だけを
用意する(未接続)。実際のセッション検証ロジックはPhase 2で実装する。
"""

from __future__ import annotations

from functools import wraps
from typing import Callable


def require_host(view_func: Callable) -> Callable:
    """対象project_idについてHostセッションを持つことを要求する(Phase 2で実装)。"""

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        raise NotImplementedError("Phase 2 で実装予定")

    return wrapper


def require_scorer(view_func: Callable) -> Callable:
    """Scorerセッションを持つことを要求する(Phase 2で実装)。"""

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        raise NotImplementedError("Phase 2 で実装予定")

    return wrapper
