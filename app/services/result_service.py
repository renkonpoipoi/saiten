"""集計(Subject別合計・平均・competition rankingでのtie処理)を扱うサービス層。

公式集計対象はeligible scorer(=そのプロジェクトの全SubjectをsubmittedしたActive
Scorer)のみ。部分採点しか完了していないScorerのデータは、他Subjectへの
submitted評価も含めて集計から全て除外する。

集計結果は独立テーブルに永続化せず、Evaluation/EvaluationScoreから都度算出する
(実装計画 v2 8節: 無料DB枠内でのデータ重複回避)。
"""

from __future__ import annotations

from collections import defaultdict

from app.models import Criterion, Evaluation, EvaluationScore, Project, Scorer, Subject
from app.services.project_service import eligible_scorer_ids


def _competition_rank(sorted_results: list[dict]) -> None:
    """合計点降順にソート済みのリストへ、in-placeでrankを付与する。

    同点は同順位、次の順位は同点者数分スキップする
    (例: 1位, 1位, 3位)。
    """
    rank = 0
    previous_total = None
    for index, result in enumerate(sorted_results):
        if previous_total is None or result["total_score"] != previous_total:
            rank = index + 1
        result["rank"] = rank
        previous_total = result["total_score"]


def build_result_summary(project: Project) -> dict:
    subjects = Subject.query.filter_by(project_id=project.id).order_by(Subject.sort_order).all()
    criteria = Criterion.query.filter_by(project_id=project.id).order_by(Criterion.sort_order).all()
    theoretical_max_total = sum(c.max_score for c in criteria)

    eligible_ids = eligible_scorer_ids(project.id)
    scorer_names = {
        s.id: s.display_name
        for s in Scorer.query.filter(Scorer.id.in_(eligible_ids)).all()
    } if eligible_ids else {}

    eval_ids_by_subject: dict[int, list[int]] = defaultdict(list)
    scorer_id_by_eval: dict[int, int] = {}
    scores_by_eval: dict[int, dict[int, int]] = defaultdict(dict)

    if eligible_ids:
        evaluations = Evaluation.query.filter(
            Evaluation.project_id == project.id,
            Evaluation.scorer_id.in_(eligible_ids),
            Evaluation.status == "submitted",
        ).all()
        eval_ids = [e.id for e in evaluations]
        for e in evaluations:
            eval_ids_by_subject[e.subject_id].append(e.id)
            scorer_id_by_eval[e.id] = e.scorer_id

        if eval_ids:
            for s in EvaluationScore.query.filter(
                EvaluationScore.evaluation_id.in_(eval_ids)
            ).all():
                scores_by_eval[s.evaluation_id][s.criterion_id] = s.score

    subject_results = []
    for subject in subjects:
        eval_ids_for_subject = eval_ids_by_subject.get(subject.id, [])
        scorer_count = len(eval_ids_for_subject)

        total_score = 0
        criterion_sums: dict[int, int] = defaultdict(int)
        judge_totals = []
        for eval_id in eval_ids_for_subject:
            eval_scores = scores_by_eval.get(eval_id, {})
            eval_total = sum(eval_scores.values())
            total_score += eval_total
            for criterion_id, score in eval_scores.items():
                criterion_sums[criterion_id] += score
            scorer_id = scorer_id_by_eval[eval_id]
            judge_totals.append(
                {
                    "scorer_id": scorer_id,
                    "display_name": scorer_names.get(scorer_id, ""),
                    "total": eval_total,
                }
            )

        mean_score = round(total_score / scorer_count, 2) if scorer_count else 0.0
        criterion_averages = [
            {
                "criterion_id": c.id,
                "average": round(criterion_sums[c.id] / scorer_count, 2) if scorer_count else 0.0,
            }
            for c in criteria
        ]

        subject_results.append(
            {
                "id": subject.id,
                "name": subject.name,
                "sort_order": subject.sort_order,
                "scorer_count": scorer_count,
                "total_score": total_score,
                "mean_score": mean_score,
                "criterion_averages": criterion_averages,
                "judge_totals": judge_totals,
            }
        )

    ranked = sorted(subject_results, key=lambda r: (-r["total_score"], r["sort_order"]))
    _competition_rank(ranked)

    return {
        "project": {"id": project.id, "name": project.name, "status": project.status},
        "criteria": [
            {"id": c.id, "name": c.name, "max_score": c.max_score} for c in criteria
        ],
        "theoretical_max_total": theoretical_max_total,
        "eligible_scorer_count": len(eligible_ids),
        "subjects": ranked,
    }
