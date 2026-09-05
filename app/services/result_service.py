"""集計(Subject別合計・平均・competition rankingでのtie処理)を扱うサービス層。

公式集計の対象になるScorerはモードによって決まり、その判定は
project_service.official_scorer_ids() に集約している(BATCHはeligible scorer、
SEQUENTIALは参加Scorer全員)。集計そのもののロジックは両モードで完全に共通。

集計結果は独立テーブルに永続化せず、Evaluation/EvaluationScoreから都度算出する
(実装計画 v2 8節: 無料DB枠内でのデータ重複回避)。
"""

from __future__ import annotations

from collections import defaultdict

from app.errors import ConflictError, ForbiddenError
from app.models import Criterion, Evaluation, EvaluationScore, Project, Scorer, Subject
from app.services.project_service import (
    official_scorer_ids,
    participating_scorer_ids,
    subject_presentation_status,
)


# 発表データを外へ出してよいSubjectの進行状態。
# LOCKED = いま得点発表中(まだPRESENTEDではない)、PRESENTED = 発表確定済み。
# WAITING / SCORING のSubjectはこの集合に含めない = 点数を一切外へ出さない。
REVEALABLE_SUBJECT_STATUSES = ("LOCKED", "PRESENTED")


class _ScoreIndex:
    """1プロジェクト分のsubmitted評価を、集計しやすい形にまとめて保持する。

    Subject/Scorerごとに問い合わせると容易にN+1になるため、必要な行を
    数本のクエリでまとめて読み込む。
    """

    __slots__ = ("eval_ids_by_subject", "scorer_id_by_eval", "scores_by_eval",
                 "scorer_names", "evaluation_by_id")

    def __init__(self):
        self.eval_ids_by_subject: dict[int, list[int]] = defaultdict(list)
        self.scorer_id_by_eval: dict[int, int] = {}
        self.scores_by_eval: dict[int, dict[int, int]] = defaultdict(dict)
        self.scorer_names: dict[int, str] = {}
        self.evaluation_by_id: dict[int, Evaluation] = {}


def _load_score_index(project: Project) -> _ScoreIndex:
    """プロジェクトのsubmitted評価を全件読み込む(公式/非公式の区別はしない)。

    公式集計対象かどうかの絞り込みは呼び出し側がofficial_scorer_idsで行う。
    こうしておくと、集計(公式のみ)とfeedback一覧(提出済み全件)を同じ
    読み込み結果から作れる。
    """
    index = _ScoreIndex()

    index.scorer_names = {
        s.id: s.display_name for s in Scorer.query.filter_by(project_id=project.id).all()
    }

    evaluations = (
        Evaluation.query.filter(
            Evaluation.project_id == project.id,
            Evaluation.status == "submitted",
        )
        # judge開示順を決定的にするため明示的に並べる。Hostが決めた採点者の
        # 並び順(座席順)をそのまま得点開示順にしたいので Scorer.sort_order を使う。
        # 既存Projectは全員 sort_order=0 なので id へフォールバックし、
        # Phase 10B以前と同じ「作成順」になる。
        .join(Scorer, Scorer.id == Evaluation.scorer_id)
        .order_by(Scorer.sort_order, Scorer.id)
        .all()
    )
    if not evaluations:
        return index

    for evaluation in evaluations:
        index.eval_ids_by_subject[evaluation.subject_id].append(evaluation.id)
        index.scorer_id_by_eval[evaluation.id] = evaluation.scorer_id
        index.evaluation_by_id[evaluation.id] = evaluation

    eval_ids = list(index.evaluation_by_id)
    for score in EvaluationScore.query.filter(
        EvaluationScore.evaluation_id.in_(eval_ids)
    ).all():
        index.scores_by_eval[score.evaluation_id][score.criterion_id] = score.score

    return index


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


def _event_order(project: Project, subject: Subject) -> int:
    """本番の進行順(0始まり)。

    RANDOM_DRAWでは抽選順(draw_order)、MANUALでは表示順(sort_order)。
    RANDOM_DRAWで全件抽選する前にLOCKEDへ進めないよう transition_to_locked が
    守っているため、この値が返る場面では draw_order は必ず公開済みになっている。
    (抽選前に漏れないよう、これを含めるのは発表可能な範囲のendpointだけ)
    """
    if project.subject_order_mode == "RANDOM_DRAW" and subject.draw_order is not None:
        return subject.draw_order
    return subject.sort_order


def _build_subject_row(
    project: Project,
    subject: Subject,
    criteria: list[Criterion],
    index: _ScoreIndex,
    official_ids: set[int],
    *,
    with_event_order: bool = False,
) -> dict:
    """1 Subjectの公式集計結果を組み立てる。公式対象外のScorerは加算しない。

    with_event_order は **発表順を出してよいendpointだけ** がTrueにする
    (result-summary / interim-ranking / subjects/<id>/result)。
    """
    official_eval_ids = [
        eval_id
        for eval_id in index.eval_ids_by_subject.get(subject.id, [])
        if index.scorer_id_by_eval[eval_id] in official_ids
    ]
    scorer_count = len(official_eval_ids)

    total_score = 0
    criterion_sums: dict[int, int] = defaultdict(int)
    judge_totals = []
    for eval_id in official_eval_ids:
        eval_scores = index.scores_by_eval.get(eval_id, {})
        eval_total = sum(eval_scores.values())
        total_score += eval_total
        for criterion_id, score in eval_scores.items():
            criterion_sums[criterion_id] += score
        scorer_id = index.scorer_id_by_eval[eval_id]
        judge_totals.append(
            {
                "scorer_id": scorer_id,
                "display_name": index.scorer_names.get(scorer_id, ""),
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

    row = {
        "id": subject.id,
        "name": subject.name,
        "sort_order": subject.sort_order,
        "presentation_status": subject_presentation_status(project, subject),
        "scorer_count": scorer_count,
        "total_score": total_score,
        "mean_score": mean_score,
        "criterion_averages": criterion_averages,
        "judge_totals": judge_totals,
    }
    if with_event_order:
        row["event_order"] = _event_order(project, subject)
    return row


def _project_criteria(project: Project) -> list[Criterion]:
    return (
        Criterion.query.filter_by(project_id=project.id).order_by(Criterion.sort_order).all()
    )


def _project_subjects(project: Project) -> list[Subject]:
    return Subject.query.filter_by(project_id=project.id).order_by(Subject.sort_order).all()


def _project_header(project: Project) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "status": project.status,
        "presentation_mode": project.presentation_mode,
    }


def build_result_summary(project: Project) -> dict:
    """全Subjectの集計 + 最終ランキング。BATCHの発表と、両モードの最終ランキングで使う。"""
    subjects = _project_subjects(project)
    criteria = _project_criteria(project)
    official_ids = official_scorer_ids(project)
    index = _load_score_index(project)

    subject_results = [
        _build_subject_row(
            project, subject, criteria, index, official_ids, with_event_order=True
        )
        for subject in subjects
    ]

    ranked = sorted(subject_results, key=lambda r: (-r["total_score"], r["sort_order"]))
    _competition_rank(ranked)

    return {
        "project": _project_header(project),
        "criteria": [
            {"id": c.id, "name": c.name, "max_score": c.max_score} for c in criteria
        ],
        "theoretical_max_total": sum(c.max_score for c in criteria),
        # eligible_scorer_countは既存クライアント互換のために残している。
        # 意味は official_scorer_count と同じ(公式集計対象の人数)。
        "eligible_scorer_count": len(official_ids),
        "official_scorer_count": len(official_ids),
        "subjects": ranked,
    }


def build_subject_result(project: Project, subject: Subject) -> dict:
    """SEQUENTIALで1 Subjectだけを発表するためのデータ。

    締切済み(LOCKED)か発表済み(PRESENTED)のSubjectしか返さない。採点中の
    Subjectの点数がHostに見えてしまうと、発表前に結果が漏れるため。
    judge_totalsの形はbuild_result_summaryと同一なので、発表演出は
    BATCHとまったく同じ関数を再利用できる。
    """
    if subject.project_id != project.id:
        raise ForbiddenError("Subject does not belong to this project.")

    status = subject_presentation_status(project, subject)
    if status not in REVEALABLE_SUBJECT_STATUSES:
        raise ConflictError(
            f"This subject's result is not available yet (current status: {status})."
        )

    criteria = _project_criteria(project)
    official_ids = official_scorer_ids(project)
    index = _load_score_index(project)

    return {
        "project": _project_header(project),
        "criteria": [
            {"id": c.id, "name": c.name, "max_score": c.max_score} for c in criteria
        ],
        "theoretical_max_total": sum(c.max_score for c in criteria),
        "official_scorer_count": len(official_ids),
        "subject": _build_subject_row(
            project, subject, criteria, index, official_ids, with_event_order=True
        ),
    }


def build_interim_ranking(project: Project) -> dict:
    """SEQUENTIALの暫定ランキング(最後のSubjectではそのまま最終順位になる)。

    含めるSubjectは presentation_status が LOCKED か PRESENTED のものだけ。
    SEQUENTIALでは当該Subjectを
      LOCKED -> Judge Reveal -> TOTAL -> 暫定ランキングへ挿入
      -> Hostが「確定して次へ」 -> PRESENTED
    の順で扱うため、ランキングへ挿入する瞬間の今回SubjectはLOCKEDである。
    したがって「未発表を除く」ではなく「WAITING / SCORING を除く」と定義する。

    順位は既存の _competition_rank をそのまま使う。順位の正解はサーバーの
    このロジックだけが持ち、クライアント側では再計算しない。

    Project.statusがSCORINGのままでも取得できる点がresult-summaryと異なるが、
    返す範囲はsubject単位のエンドポイント(build_subject_result)と同じゲートに
    揃えてあるため、開示範囲は広がらない。
    """
    subjects = _project_subjects(project)
    criteria = _project_criteria(project)
    official_ids = official_scorer_ids(project)
    index = _load_score_index(project)

    revealable = [
        subject
        for subject in subjects
        if subject_presentation_status(project, subject) in REVEALABLE_SUBJECT_STATUSES
    ]
    subject_results = [
        _build_subject_row(
            project, subject, criteria, index, official_ids, with_event_order=True
        )
        for subject in revealable
    ]

    ranked = sorted(subject_results, key=lambda r: (-r["total_score"], r["sort_order"]))
    _competition_rank(ranked)

    return {
        "project": _project_header(project),
        "theoretical_max_total": sum(c.max_score for c in criteria),
        "official_scorer_count": len(official_ids),
        "subjects": ranked,
    }


def _serialize_submitted_evaluations(
    subject: Subject,
    criteria: list[Criterion],
    index: _ScoreIndex,
    official_ids: set[int],
) -> list[dict]:
    """1 Subjectに対する提出済み評価を、公式/非公式の区別付きで列挙する。

    BATCHのforced closeで公式集計から除外されたScorerも、提出済みであれば
    ここに含める(official_included=False)。除外は「公式スコアに加算しない」
    という意味であり、書いてもらったfeedbackを捨てる理由はないため。
    未提出(draft)の評価は結果ではないので含めない。
    """
    rows = []
    for eval_id in index.eval_ids_by_subject.get(subject.id, []):
        evaluation = index.evaluation_by_id[eval_id]
        scores = index.scores_by_eval.get(eval_id, {})
        scorer_id = index.scorer_id_by_eval[eval_id]
        rows.append(
            {
                "evaluation_id": eval_id,
                "scorer_id": scorer_id,
                "scorer_name": index.scorer_names.get(scorer_id, ""),
                "official_included": scorer_id in official_ids,
                "scores": [
                    {"criterion_id": c.id, "score": scores.get(c.id)} for c in criteria
                ],
                "total": sum(scores.values()),
                "feedback": evaluation.feedback or "",
                "submitted_at": (
                    evaluation.submitted_at.isoformat() if evaluation.submitted_at else None
                ),
            }
        )
    return rows


def build_analysis(project: Project) -> dict:
    """Host向け結果分析。公式集計に加えて、提出済みfeedbackを全件返す。

    公式スコア(total_score/mean_score/criterion_averages/judge_totals)は従来通り
    official scorerのみで算出し、feedbackだけを提出済み全件から拾う。
    """
    subjects = _project_subjects(project)
    criteria = _project_criteria(project)
    official_ids = official_scorer_ids(project)
    index = _load_score_index(project)

    subject_results = []
    for subject in subjects:
        row = _build_subject_row(project, subject, criteria, index, official_ids)
        # criterion名と満点を同梱する。Radar Chartはこの配列だけで描ける。
        criterion_lookup = {c.id: c for c in criteria}
        for average in row["criterion_averages"]:
            criterion = criterion_lookup[average["criterion_id"]]
            average["name"] = criterion.name
            average["max_score"] = criterion.max_score
        row["evaluations"] = _serialize_submitted_evaluations(
            subject, criteria, index, official_ids
        )
        subject_results.append(row)

    ranked = sorted(subject_results, key=lambda r: (-r["total_score"], r["sort_order"]))
    _competition_rank(ranked)

    participating_ids = participating_scorer_ids(project.id)

    return {
        "project": _project_header(project),
        "criteria": [
            {
                "id": c.id,
                "name": c.name,
                "max_score": c.max_score,
                "sort_order": c.sort_order,
            }
            for c in criteria
        ],
        "theoretical_max_total": sum(c.max_score for c in criteria),
        "official_scorer_count": len(official_ids),
        "excluded_scorer_count": len(participating_ids - official_ids),
        "subjects": ranked,
    }
