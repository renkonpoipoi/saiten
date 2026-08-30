"""集計(Subject別合計・平均・competition rankingでのtie処理)を扱うサービス層。

集計結果は独立テーブルに永続化せず、Evaluation/EvaluationScoreから都度算出する
(実装計画 v2 8節: 無料DB枠内でのデータ重複回避)。

Phase 1時点では骨格のみ。実装はPhase 4(Close & aggregation)で行う。
"""

from __future__ import annotations


def build_result_summary(*args, **kwargs):
    """eligible scorerのEvaluationのみを対象にSubjectごとの合計・平均・
    competition ranking(同点は1位,1位,3位)を算出する。Phase 4で実装する。
    """
    raise NotImplementedError("Phase 4 で実装予定")
