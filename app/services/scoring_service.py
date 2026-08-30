"""採点(Evaluation/EvaluationScore)の保存・確定を扱うサービス層。

Phase 1時点では骨格のみ。実装はPhase 3(Scoring)で行う。
"""

from __future__ import annotations


class AlreadySubmittedError(RuntimeError):
    """確定済み(status='submitted')のEvaluationへの書き込みを拒否する際に送出する。"""


def save_scores(*args, **kwargs):
    """draft状態のEvaluation/EvaluationScoreを保存(UPSERT)する。Phase 3で実装する。"""
    raise NotImplementedError("Phase 3 で実装予定")


def submit_evaluation(*args, **kwargs):
    """Evaluationをsubmitted状態に確定する(不可逆)。Phase 3で実装する。"""
    raise NotImplementedError("Phase 3 で実装予定")
