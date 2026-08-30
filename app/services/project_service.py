"""プロジェクトのライフサイクル(DRAFT/SCORING/LOCKED/PRESENTING/FINISHED)と
DRAFT編集(Project/Subject/Criterion/Scorer)を扱うサービス層。

Phase 1時点では骨格のみ。実装は Phase 2(プロジェクト作成・DRAFT編集・
DRAFT->SCORING遷移)以降で行う。
"""

from __future__ import annotations


class ProjectStateError(RuntimeError):
    """状態遷移やDRAFT限定編集のガードに違反した操作を表す例外。"""


def create_project(*args, **kwargs):
    """Project/Subject/Criterion/Scorerを一括作成し、host/参加コードを発行する。

    Phase 2で実装する。
    """
    raise NotImplementedError("Phase 2 で実装予定")


def transition(*args, **kwargs):
    """Project.statusを次の状態へ遷移させる。

    DRAFT->SCORING時のEvaluation一括生成、SCORING->LOCKED時のeligible scorer
    判定を含む。Phase 2/4 で実装する。
    """
    raise NotImplementedError("Phase 2/4 で実装予定")
