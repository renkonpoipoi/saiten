"""プロジェクトのライフサイクル(DRAFT/SCORING/LOCKED/PRESENTING/FINISHED)と
DRAFT編集(Project/Subject/Criterion/Scorer)を扱うサービス層。

route側はHTTPの入出力にのみ責務を持ち、業務ロジックはここに集約する。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func

from app.errors import ConflictError, ForbiddenError, ValidationError
from app.extensions import db
from app.models import (
    PRESENTATION_MODES,
    Criterion,
    Evaluation,
    Project,
    Scorer,
    Subject,
)
from app.services.code_service import generate_host_code, generate_scorer_code, hash_code

REQUIRED_CRITERION_COUNT = 5
DEFAULT_MAX_SCORE = 20


class ProjectStateError(ConflictError):
    """状態遷移やDRAFT限定編集のガードに違反した操作を表す例外。"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clean_names(raw_names: list[str]) -> list[str]:
    return [n.strip() for n in raw_names if n and n.strip()]


def _resolve_host_scorer_index(raw_index, scorer_count: int) -> int:
    """「ホストとして採点する人」を指すindexを検証して返す。

    Project作成は1リクエストで完結し、この時点ではScorer.idがまだ無いため、
    display_nameではなく**空文字除去後のscorer配列に対するindex**で指定させる
    (採点者名は重複しうるため名前では一意に指せない)。

    boolはintのサブクラスなので、Trueが1として通ってしまわないよう明示的に弾く。
    """
    if raw_index is None:
        raise ValidationError(
            "host_scorer_index is required when allow_host_scoring is enabled."
        )
    if isinstance(raw_index, bool) or not isinstance(raw_index, int):
        raise ValidationError("host_scorer_index must be an integer.")
    if not 0 <= raw_index < scorer_count:
        raise ValidationError(
            f"host_scorer_index must be between 0 and {scorer_count - 1}."
        )
    return raw_index


def _normalize_sort_order(rows: list) -> None:
    """並び順を 0..N-1 の連番へ詰め直す(rows は既に希望順に並んでいること)。

    **2段階で代入する。** subjects には UNIQUE(project_id, sort_order) があり、
    素直に代入すると途中で必ず既存値と衝突して IntegrityError になる。
    一旦衝突しない負値へ退避して flush() し、そのあと最終値を入れる。
    scorers には現状この制約が無いが、扱いを揃えておく。
    """
    if not rows:
        return
    for index, row in enumerate(rows):
        row.sort_order = -(index + 1)
    db.session.flush()
    for index, row in enumerate(rows):
        row.sort_order = index


def _active_scorers_in_order(project_id: int) -> list[Scorer]:
    return (
        Scorer.query.filter_by(project_id=project_id, is_active=True)
        .order_by(Scorer.sort_order, Scorer.id)
        .all()
    )


def _subjects_in_order(project_id: int) -> list[Subject]:
    return (
        Subject.query.filter_by(project_id=project_id)
        .order_by(Subject.sort_order, Subject.id)
        .all()
    )


def _require_draft(project: Project) -> None:
    if project.status != "DRAFT":
        raise ProjectStateError(
            "This operation is only allowed while the project is in DRAFT status."
        )


def _require_owned(project: Project, obj, *, label: str) -> None:
    if obj.project_id != project.id:
        raise ForbiddenError(f"{label} does not belong to this project.")


# ---------------------------------------------------------------------------
# 作成
# ---------------------------------------------------------------------------


def create_project(
    *,
    name: str,
    subject_names: list[str],
    scorer_names: list[str],
    criterion_names: list[str],
    allow_host_scoring: bool,
    presentation_mode: str = "BATCH",
    host_scorer_index: object = None,
) -> dict:
    """Project/Subject/Criterion/Scorerを1トランザクションで一括作成する。

    戻り値には平文のhost_code・scorer codeを一度だけ含める(DBにはhashのみ
    保存する)。

    **Host roleはScorerの属性であって別人格ではない。** allow_host_scoringが
    Trueのとき、入力済みScorerのうちhost_scorer_indexで指された1人に
    is_host_scorerを立てるだけで、Scorerの数は増やさない。
    (Phase 10A以前は「ホスト」という名前のScorerを1人追加していた。)
    """
    name = (name or "").strip()
    if not name:
        raise ValidationError("Project name is required.")

    subject_names = _clean_names(subject_names or [])
    if not subject_names:
        raise ValidationError("At least one subject is required.")

    scorer_names = _clean_names(scorer_names or [])
    if not scorer_names:
        raise ValidationError("At least one scorer is required.")

    criterion_names = _clean_names(criterion_names or [])
    if len(criterion_names) != REQUIRED_CRITERION_COUNT:
        raise ValidationError(
            f"Exactly {REQUIRED_CRITERION_COUNT} criteria are required in this MVP."
        )

    presentation_mode = (presentation_mode or "BATCH").strip().upper()
    if presentation_mode not in PRESENTATION_MODES:
        raise ValidationError(
            f"presentation_mode must be one of {PRESENTATION_MODES}."
        )

    allow_host_scoring = bool(allow_host_scoring)
    # 検証はDBへ書き始める前に済ませる(不正なindexでProjectが作られないように)。
    host_index = (
        _resolve_host_scorer_index(host_scorer_index, len(scorer_names))
        if allow_host_scoring
        else None
    )

    host_code = generate_host_code()
    project = Project(
        name=name,
        status="DRAFT",
        host_code_hash=hash_code(host_code),
        allow_host_scoring=allow_host_scoring,
        presentation_mode=presentation_mode,
    )
    db.session.add(project)
    db.session.flush()  # project.id を採番させる

    for order, subject_name in enumerate(subject_names):
        db.session.add(Subject(project_id=project.id, name=subject_name, sort_order=order))

    for order, criterion_name in enumerate(criterion_names):
        db.session.add(
            Criterion(
                project_id=project.id,
                name=criterion_name,
                max_score=DEFAULT_MAX_SCORE,
                sort_order=order,
            )
        )

    scorer_payload = []
    for index, scorer_name in enumerate(scorer_names):
        # 入力されたScorerのうち1人にHost roleを割り当てるだけ。人数は増やさない。
        is_host_scorer = index == host_index
        code = generate_scorer_code()
        scorer = Scorer(
            project_id=project.id,
            display_name=scorer_name,
            access_code_hash=hash_code(code),
            is_host_scorer=is_host_scorer,
            # 入力された採点者の順をそのまま初期の並び順にする
            sort_order=index,
        )
        db.session.add(scorer)
        db.session.flush()
        scorer_payload.append(
            {
                "id": scorer.id,
                "display_name": scorer.display_name,
                "code": code,
                "is_host_scorer": scorer.is_host_scorer,
            }
        )

    db.session.commit()

    return {
        "project": project,
        "host_code": host_code,
        "scorers": scorer_payload,
    }


# ---------------------------------------------------------------------------
# DRAFT限定編集
# ---------------------------------------------------------------------------


def update_project_name(project: Project, name: str) -> Project:
    _require_draft(project)
    name = (name or "").strip()
    if not name:
        raise ValidationError("Project name is required.")
    project.name = name
    db.session.commit()
    return project


def add_subject(project: Project, name: str) -> Subject:
    _require_draft(project)
    name = (name or "").strip()
    if not name:
        raise ValidationError("Subject name is required.")
    max_order = (
        db.session.query(func.max(Subject.sort_order)).filter_by(project_id=project.id).scalar()
    )
    subject = Subject(
        project_id=project.id, name=name, sort_order=(max_order + 1) if max_order is not None else 0
    )
    db.session.add(subject)
    db.session.flush()
    _normalize_sort_order(_subjects_in_order(project.id))
    db.session.commit()
    return subject


def update_subject(project: Project, subject: Subject, name: str) -> Subject:
    _require_draft(project)
    _require_owned(project, subject, label="Subject")
    name = (name or "").strip()
    if not name:
        raise ValidationError("Subject name is required.")
    subject.name = name
    db.session.commit()
    return subject


def delete_subject(project: Project, subject: Subject) -> None:
    _require_draft(project)
    _require_owned(project, subject, label="Subject")
    db.session.delete(subject)
    db.session.flush()
    # 残った被採点者を 0..N-1 へ詰め直す(gapを残さない)
    _normalize_sort_order(_subjects_in_order(project.id))
    db.session.commit()


def update_criterion(project: Project, criterion: Criterion, name: str) -> Criterion:
    """MVPでは軸の個数(5件)は固定のため、名称編集のみ許可する。"""
    _require_draft(project)
    _require_owned(project, criterion, label="Criterion")
    name = (name or "").strip()
    if not name:
        raise ValidationError("Criterion name is required.")
    criterion.name = name
    db.session.commit()
    return criterion


def add_scorer(project: Project, display_name: str) -> tuple[Scorer, str]:
    _require_draft(project)
    display_name = (display_name or "").strip()
    if not display_name:
        raise ValidationError("Scorer name is required.")
    code = generate_scorer_code()
    max_order = (
        db.session.query(func.max(Scorer.sort_order))
        .filter_by(project_id=project.id, is_active=True)
        .scalar()
    )
    scorer = Scorer(
        project_id=project.id,
        display_name=display_name,
        access_code_hash=hash_code(code),
        # 新しい採点者は末尾へ。そのあと 0..N-1 へ詰め直す。
        sort_order=(max_order + 1) if max_order is not None else 0,
    )
    db.session.add(scorer)
    db.session.flush()
    _normalize_sort_order(_active_scorers_in_order(project.id))
    db.session.commit()
    return scorer, code


def update_scorer_name(project: Project, scorer: Scorer, display_name: str) -> Scorer:
    _require_draft(project)
    _require_owned(project, scorer, label="Scorer")
    display_name = (display_name or "").strip()
    if not display_name:
        raise ValidationError("Scorer name is required.")
    scorer.display_name = display_name
    db.session.commit()
    return scorer


def delete_scorer(project: Project, scorer: Scorer) -> None:
    _require_draft(project)
    _require_owned(project, scorer, label="Scorer")
    # ホスト兼任だったScorerを消したら、その事実をProject側にも反映する。
    # 「ホスト兼任がいないのに allow_host_scoring=true」という中途半端な状態を
    # 残さないため、同一トランザクションで確定させる。
    if scorer.is_host_scorer:
        project.allow_host_scoring = False
    db.session.delete(scorer)
    db.session.flush()
    # 残った採点者を 0..N-1 へ詰め直す(gapを残さない)
    _normalize_sort_order(_active_scorers_in_order(project.id))
    db.session.commit()


def set_host_scorer(project: Project, scorer: Scorer | None) -> Scorer | None:
    """DRAFT中にHost兼任のScorerを付け替える(Noneで解除)。

    Host roleはScorerの属性なので、付け替えはフラグの移動だけで完結する。
    Scorerの追加・削除は行わない。

    **順序が重要。** 「1 Projectにつき is_host_scorer=true は最大1人」という
    部分UNIQUE INDEXと共存させるため、必ず

        旧Hostを False -> flush() -> 新Hostを True -> commit

    の順で書く。SQLAlchemyのunit of workは同一flush内のUPDATEを主キー順で
    並べるため、明示的にflushを挟まないと「新Hostを立てるUPDATE」が
    「旧Hostを降ろすUPDATE」より先に飛び、一時的に2人trueになってUNIQUE違反
    しうる(plainなunique indexはdeferrableではないので即時に評価される)。
    """
    _require_draft(project)

    current = (
        Scorer.query.filter_by(project_id=project.id, is_host_scorer=True).all()
    )

    if scorer is None:
        for existing in current:
            existing.is_host_scorer = False
        project.allow_host_scoring = False
        db.session.commit()
        return None

    _require_owned(project, scorer, label="Scorer")
    if not scorer.is_active:
        raise ValidationError("An inactive scorer cannot take the host role.")

    # 1) 旧Hostを降ろす
    demoted = [existing for existing in current if existing.id != scorer.id]
    for existing in demoted:
        existing.is_host_scorer = False
    if demoted:
        # 2) 降格のUPDATEだけを先にDBへ送る(ここが無いとUNIQUE違反しうる)
        db.session.flush()

    # 3) 新Hostを立てる
    scorer.is_host_scorer = True
    project.allow_host_scoring = True
    db.session.commit()
    return scorer


# ---------------------------------------------------------------------------
# 並び替え(DRAFT限定)
# ---------------------------------------------------------------------------


def _validate_reorder_ids(raw_ids, rows, *, label: str) -> list[int]:
    """並び替えリクエストのIDリストを検証する。

    「現存する集合と過不足なく一致すること」を要求する。部分的な並び替えを
    許すと、送られてこなかった行の順位が不定になるため。
    """
    if not isinstance(raw_ids, list):
        raise ValidationError(f"{label}_ids must be a list.")
    for raw_id in raw_ids:
        if isinstance(raw_id, bool) or not isinstance(raw_id, int):
            raise ValidationError(f"{label}_ids must contain integers.")

    requested = list(raw_ids)
    if len(set(requested)) != len(requested):
        raise ValidationError(f"{label}_ids must not contain duplicates.")

    existing = {row.id for row in rows}
    if set(requested) != existing:
        raise ValidationError(
            f"{label}_ids must list every {label} of this project exactly once."
        )
    return requested


def reorder_subjects(project: Project, subject_ids) -> list[Subject]:
    """被採点者の並び順を一括で保存する(DRAFT限定)。"""
    _require_draft(project)
    rows = _subjects_in_order(project.id)
    ordered_ids = _validate_reorder_ids(subject_ids, rows, label="subject")

    by_id = {row.id: row for row in rows}
    _normalize_sort_order([by_id[subject_id] for subject_id in ordered_ids])
    db.session.commit()
    return _subjects_in_order(project.id)


def reorder_scorers(project: Project, scorer_ids) -> list[Scorer]:
    """採点者の並び順(座席順)を一括で保存する(DRAFT限定)。

    対象は active な採点者のみ。is_host_scorer は順序に一切影響しない。
    """
    _require_draft(project)
    rows = _active_scorers_in_order(project.id)
    ordered_ids = _validate_reorder_ids(scorer_ids, rows, label="scorer")

    by_id = {row.id: row for row in rows}
    _normalize_sort_order([by_id[scorer_id] for scorer_id in ordered_ids])
    db.session.commit()
    return _active_scorers_in_order(project.id)


# ---------------------------------------------------------------------------
# コード再発行(DRAFT限定ではなく常時可能)
# ---------------------------------------------------------------------------


def regenerate_scorer_code(project: Project, scorer: Scorer) -> str:
    _require_owned(project, scorer, label="Scorer")
    code = generate_scorer_code()
    scorer.access_code_hash = hash_code(code)
    db.session.commit()
    return code


def regenerate_host_code(project: Project) -> str:
    code = generate_host_code()
    project.host_code_hash = hash_code(code)
    db.session.commit()
    return code


# ---------------------------------------------------------------------------
# eligible scorer判定(Phase 4で本格利用するが、進捗表示にも使うためここに置く)
# ---------------------------------------------------------------------------


def eligible_scorer_ids(project_id: int) -> set[int]:
    """そのプロジェクトの全Subjectに対してstatus='submitted'のEvaluationを
    持つactiveなScorerのIDを返す(=公式集計の対象になる採点者)。

    N+1を避けるため、Scorer単位でループせず集計クエリ1本で判定する。
    """
    total_subjects = (
        db.session.query(func.count(Subject.id)).filter(Subject.project_id == project_id).scalar()
    )
    if not total_subjects:
        return set()

    rows = (
        db.session.query(Evaluation.scorer_id, func.count(Evaluation.id))
        .join(Scorer, Scorer.id == Evaluation.scorer_id)
        .filter(
            Evaluation.project_id == project_id,
            Evaluation.status == "submitted",
            Scorer.is_active.is_(True),
        )
        .group_by(Evaluation.scorer_id)
        .all()
    )
    return {scorer_id for scorer_id, submitted_count in rows if submitted_count == total_subjects}


def participating_scorer_ids(project_id: int) -> set[int]:
    """SCORING開始時点で固定された参加Scorerの集合を返す。

    DRAFT->SCORING遷移がactive Scorer x SubjectのEvaluationを一括生成し、それ以降
    Scorerの追加・削除は_require_draftガードにより不可能なため(DRAFTへ戻る遷移が
    存在しない)、**Evaluation行そのものが参加Scorerのsnapshotになっている**。
    このため専用のsnapshotテーブルは持たない。

    この不変条件を壊さないため、SCORING開始後にScorerを増減させる操作や
    is_activeを書き換える操作を追加してはならない。
    """
    rows = (
        db.session.query(Evaluation.scorer_id)
        .join(Scorer, Scorer.id == Evaluation.scorer_id)
        .filter(Evaluation.project_id == project_id, Scorer.is_active.is_(True))
        .distinct()
        .all()
    )
    return {scorer_id for (scorer_id,) in rows}


def official_scorer_ids(project: Project) -> set[int]:
    """公式集計の対象になるScorer集合。モード差はこの1関数に閉じ込める。

    - BATCH:      全Subjectを提出し終えたScorerのみ(=eligible scorer)。
                  forced closeで未完了者を全Subjectから一律除外することで、
                  Subject間の審査員数を揃えている。
    - SEQUENTIAL: 参加Scorer全員。Subjectのlock条件が「全参加Scorerの提出」で
                  あるため、発表可能なSubjectでは常に全員分が揃っている。
                  (eligible判定は「全Subject提出済み」を要求するので、
                   後続Subjectが未採点なSEQUENTIALの途中では常に空集合になり使えない)
    """
    if project.presentation_mode == "SEQUENTIAL":
        return participating_scorer_ids(project.id)
    return eligible_scorer_ids(project.id)


# BATCHではSubject単位の進行状態を持たないため、Project.statusから導出する。
_BATCH_SUBJECT_STATUS = {
    "DRAFT": "WAITING",
    "SCORING": "SCORING",
    "LOCKED": "LOCKED",
    "PRESENTING": "PRESENTED",
    "FINISHED": "PRESENTED",
}


def subject_presentation_status(project: Project, subject: Subject) -> str:
    """Subjectの進行状態を、モードの違いを隠して1つの語彙で返す。

    subjects.presentation_status列はSEQUENTIALでのみ権威を持つ。BATCHでは
    Project.statusから導出するため、Phase 8以前に作られた既存Projectの
    'WAITING'という値が誤って表示されることはない(=migrationでの
    データbackfillが不要)。
    """
    if project.presentation_mode == "SEQUENTIAL":
        return subject.presentation_status
    return _BATCH_SUBJECT_STATUS[project.status]


# ---------------------------------------------------------------------------
# 状態遷移
# ---------------------------------------------------------------------------


def transition_to_scoring(project: Project) -> Project:
    if project.status != "DRAFT":
        raise ProjectStateError("Only DRAFT projects can start scoring.")

    active_scorers = Scorer.query.filter_by(project_id=project.id, is_active=True).all()
    subjects = (
        Subject.query.filter_by(project_id=project.id).order_by(Subject.sort_order).all()
    )
    if not active_scorers:
        raise ProjectStateError("At least one active scorer is required to start scoring.")
    if not subjects:
        raise ProjectStateError("At least one subject is required to start scoring.")

    existing_pairs = {
        (e.scorer_id, e.subject_id)
        for e in Evaluation.query.filter_by(project_id=project.id).all()
    }
    for scorer in active_scorers:
        for subject in subjects:
            if (scorer.id, subject.id) in existing_pairs:
                continue
            db.session.add(
                Evaluation(project_id=project.id, scorer_id=scorer.id, subject_id=subject.id)
            )

    if project.presentation_mode == "SEQUENTIAL":
        # 最初のSubjectだけを採点可能にし、残りは待機させる。
        for index, subject in enumerate(subjects):
            subject.presentation_status = "SCORING" if index == 0 else "WAITING"

    project.status = "SCORING"
    # Evaluationの一括生成・Subject状態の初期化・Project statusの更新を
    # 1トランザクションで確定させる(途中commitは行わない)。
    db.session.commit()
    return project


def transition_to_locked(project: Project) -> Project:
    if project.status != "SCORING":
        raise ProjectStateError("Only SCORING projects can be locked.")

    if project.presentation_mode == "SEQUENTIAL":
        # SEQUENTIALではSubject単位で採点・締切・発表を終えており、Project全体の
        # LOCKEDは最終ランキングへ進むための最後の一歩でしかない。
        # forced closeの概念は持ち込まない(§lock_subjectを参照)。
        pending = [
            s
            for s in Subject.query.filter_by(project_id=project.id).all()
            if s.presentation_status != "PRESENTED"
        ]
        if pending:
            raise ProjectStateError(
                "Cannot lock: all subjects must be presented first "
                f"({len(pending)} remaining)."
            )
    else:
        eligible_ids = eligible_scorer_ids(project.id)
        if not eligible_ids:
            raise ProjectStateError(
                "Cannot lock: no eligible scorer has completed all subjects yet."
            )

    project.status = "LOCKED"
    project.locked_at = _utcnow()
    db.session.commit()
    return project


def transition_to_presenting(project: Project) -> Project:
    if project.status != "LOCKED":
        raise ProjectStateError("Only LOCKED projects can start presenting.")
    project.status = "PRESENTING"
    project.presenting_at = _utcnow()
    db.session.commit()
    return project


def transition_to_finished(project: Project) -> Project:
    if project.status != "PRESENTING":
        raise ProjectStateError("Only PRESENTING projects can finish.")
    project.status = "FINISHED"
    project.finished_at = _utcnow()
    db.session.commit()
    return project


# ---------------------------------------------------------------------------
# Subject単位の進行(SEQUENTIAL専用)
# ---------------------------------------------------------------------------


def _require_sequential(project: Project) -> None:
    if project.presentation_mode != "SEQUENTIAL":
        raise ProjectStateError(
            "Subject-level progression is only available in SEQUENTIAL mode."
        )
    if project.status != "SCORING":
        raise ProjectStateError(
            "Subject-level progression is only available while the project is scoring."
        )


def submitted_scorer_ids_for_subject(project_id: int, subject_id: int) -> set[int]:
    rows = (
        db.session.query(Evaluation.scorer_id)
        .filter(
            Evaluation.project_id == project_id,
            Evaluation.subject_id == subject_id,
            Evaluation.status == "submitted",
        )
        .all()
    )
    return {scorer_id for (scorer_id,) in rows}


def lock_subject(project: Project, subject: Subject) -> Subject:
    """SEQUENTIAL: 1 Subjectの採点を締め切る(SCORING -> LOCKED)。

    **forced closeは提供しない。** 参加Scorer全員の提出を必須にすることで、
    どのSubjectも同じ人数で採点されている状態を保つ。人数が揃っていないと
    Subject間で合計点を比較できず、最終ランキングが意味を失うため。
    (BATCHは「未完了者を全Subjectから一律に除外する」という別の方法で
     同じ不変条件を守っている)
    """
    _require_owned(project, subject, label="Subject")
    _require_sequential(project)

    # 二重クリックや同時requestでも壊れないよう、現在の状態を必ず読み直して検証する
    if subject.presentation_status != "SCORING":
        raise ProjectStateError(
            f"Subject is not open for scoring (current status: {subject.presentation_status})."
        )

    participating = participating_scorer_ids(project.id)
    submitted = submitted_scorer_ids_for_subject(project.id, subject.id)
    missing = participating - submitted
    if missing:
        raise ProjectStateError(
            "Cannot lock this subject: every participating scorer must submit first "
            f"({len(missing)} of {len(participating)} still pending). "
            "Forced close is not available in SEQUENTIAL mode."
        )

    subject.presentation_status = "LOCKED"
    subject.locked_at = _utcnow()
    db.session.commit()
    return subject


def present_subject(project: Project, subject: Subject) -> tuple[Subject, Subject | None]:
    """SEQUENTIAL: 発表済みとして確定し、次のSubjectを採点可能にする。

    当該SubjectのPRESENTED化と次SubjectのSCORING化は同一トランザクションで行う。
    最後のSubjectの場合は次が無く、Project.statusはSCORINGのまま据え置く
    (最終ランキングへはHostの明示操作でSCORING->LOCKED->PRESENTINGと進める)。
    """
    _require_owned(project, subject, label="Subject")
    _require_sequential(project)

    if subject.presentation_status != "LOCKED":
        raise ProjectStateError(
            f"Only a locked subject can be presented (current status: "
            f"{subject.presentation_status})."
        )

    subject.presentation_status = "PRESENTED"
    subject.presented_at = _utcnow()

    next_subject = (
        Subject.query.filter_by(project_id=project.id, presentation_status="WAITING")
        .order_by(Subject.sort_order)
        .first()
    )
    if next_subject is not None:
        next_subject.presentation_status = "SCORING"

    db.session.commit()
    return subject, next_subject


_TRANSITIONS = {
    ("DRAFT", "SCORING"): transition_to_scoring,
    ("SCORING", "LOCKED"): transition_to_locked,
    ("LOCKED", "PRESENTING"): transition_to_presenting,
    ("PRESENTING", "FINISHED"): transition_to_finished,
}


def transition(project: Project, target_status: str) -> Project:
    handler = _TRANSITIONS.get((project.status, target_status))
    if handler is None:
        raise ProjectStateError(
            f"Cannot transition project from {project.status} to {target_status}."
        )
    return handler(project)


# ---------------------------------------------------------------------------
# 進捗(Host Dashboard/Settings向け)
# ---------------------------------------------------------------------------


def subject_progress_rows(project: Project) -> list[dict]:
    """Subjectごとの進行状態と提出状況。Host DashboardとPresentationで共用する。"""
    subjects = (
        Subject.query.filter_by(project_id=project.id).order_by(Subject.sort_order).all()
    )
    participating_ids = participating_scorer_ids(project.id)

    submitted_by_subject: dict[int, int] = defaultdict(int)
    rows = (
        db.session.query(Evaluation.scorer_id, Evaluation.subject_id)
        .filter(
            Evaluation.project_id == project.id,
            Evaluation.status == "submitted",
        )
        .all()
    )
    for scorer_id, subject_id in rows:
        if scorer_id in participating_ids:
            submitted_by_subject[subject_id] += 1

    result = []
    for subject in subjects:
        status = subject_presentation_status(project, subject)
        submitted = submitted_by_subject.get(subject.id, 0)
        result.append(
            {
                "id": subject.id,
                "name": subject.name,
                "sort_order": subject.sort_order,
                "presentation_status": status,
                "submitted_count": submitted,
                "scorer_count": len(participating_ids),
                "pending_count": len(participating_ids) - submitted,
                # SEQUENTIALでこのSubjectを今すぐ締め切れるか(全員提出済みか)
                "can_lock": (
                    project.presentation_mode == "SEQUENTIAL"
                    and status == "SCORING"
                    and len(participating_ids) > 0
                    and submitted == len(participating_ids)
                ),
            }
        )
    return result


def _progression_summary(project: Project, subject_rows: list[dict]) -> dict:
    return {
        # SEQUENTIALで現在採点中のSubject(いなければNone)
        "current_subject_id": next(
            (r["id"] for r in subject_rows if r["presentation_status"] == "SCORING"), None
        ),
        # SEQUENTIALで発表待ち(締切済み)のSubject
        "presentable_subject_id": next(
            (r["id"] for r in subject_rows if r["presentation_status"] == "LOCKED"), None
        ),
        "all_subjects_presented": bool(subject_rows)
        and all(r["presentation_status"] == "PRESENTED" for r in subject_rows),
    }


def build_presentation_state(project: Project) -> dict:
    """結果発表画面が「いま何をすべきか」を判断するための状態。"""
    subject_rows = subject_progress_rows(project)
    return {
        "project": {
            "id": project.id,
            "name": project.name,
            "status": project.status,
            "presentation_mode": project.presentation_mode,
        },
        "subjects": subject_rows,
        "participating_scorer_count": len(participating_scorer_ids(project.id)),
        **_progression_summary(project, subject_rows),
    }


def get_progress(project: Project) -> dict:
    subjects = Subject.query.filter_by(project_id=project.id).order_by(Subject.sort_order).all()
    scorers = (
        Scorer.query.filter_by(project_id=project.id, is_active=True)
        .order_by(Scorer.sort_order, Scorer.id)
        .all()
    )

    eligible_ids = eligible_scorer_ids(project.id) if subjects else set()

    evaluations = Evaluation.query.filter_by(project_id=project.id).all()
    eval_lookup = {(e.scorer_id, e.subject_id): e.status for e in evaluations}

    scorer_rows = []
    for scorer in scorers:
        statuses = [eval_lookup.get((scorer.id, s.id), "not_started") for s in subjects]
        scorer_rows.append(
            {
                "scorer_id": scorer.id,
                "display_name": scorer.display_name,
                "is_host_scorer": scorer.is_host_scorer,
                # subjectsと同じ並び順のstatus一覧(Host Dashboardの
                # Scorer x Subjectマトリクス描画用)
                "statuses": statuses,
                "submitted_count": sum(1 for st in statuses if st == "submitted"),
                "subject_count": len(subjects),
                "eligible": scorer.id in eligible_ids,
            }
        )

    submitted_count = sum(1 for e in evaluations if e.status == "submitted")

    subject_rows = subject_progress_rows(project)

    return {
        "project_status": project.status,
        "presentation_mode": project.presentation_mode,
        "subjects": subject_rows,
        "scorers": scorer_rows,
        "submitted_count": submitted_count,
        "total_count": len(evaluations),
        "eligible_scorer_count": len(eligible_ids),
        "incomplete_scorer_count": len(scorers) - len(eligible_ids),
        "participating_scorer_count": len(participating_scorer_ids(project.id)),
        **_progression_summary(project, subject_rows),
    }
