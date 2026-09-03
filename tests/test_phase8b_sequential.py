"""Phase 8B-1: SEQUENTIAL modeの状態機械とガード。"""

from __future__ import annotations

import pytest

from app.models import Project, Subject
from app.services import project_service
from tests.helpers import (
    create_project,
    criteria_for,
    evaluation_id_for,
    login_scorer,
    score_and_submit,
    scorer_clients,
    start_scoring,
    subjects_for,
)

THREE_SUBJECTS = {
    "subjects": ["チームA", "チームB", "チームC"],
    "scorers": ["採点者1", "採点者2"],
}


def _sequential_project(client, **overrides):
    payload = dict(THREE_SUBJECTS, presentation_mode="SEQUENTIAL")
    payload.update(overrides)
    return create_project(client, **payload)


def _statuses(project_id: int) -> list[str]:
    return [s.presentation_status for s in subjects_for(project_id)]


def _submit_all_for(app, created, project_id, subject_id, value=15):
    for scorer_info in created["scorers"]:
        scorer = app.test_client()
        login_scorer(scorer, scorer_info["code"])
        score_and_submit(scorer, project_id, subject_id, value=value)


def _lock(client, project_id, subject_id):
    return client.post(f"/api/projects/{project_id}/subjects/{subject_id}/lock")


def _present(client, project_id, subject_id):
    return client.post(f"/api/projects/{project_id}/subjects/{subject_id}/present")


# ---------------------------------------------------------------------------
# presentation_mode の作成
# ---------------------------------------------------------------------------


def test_presentation_mode_defaults_to_batch(client, db):
    created = create_project(client)
    assert created["presentation_mode"] == "BATCH"
    assert db.session.get(Project, created["project_id"]).presentation_mode == "BATCH"


def test_sequential_project_can_be_created(client, db):
    created = _sequential_project(client)
    assert created["presentation_mode"] == "SEQUENTIAL"
    assert db.session.get(Project, created["project_id"]).presentation_mode == "SEQUENTIAL"


def test_unknown_presentation_mode_is_rejected(client, db):
    from tests.helpers import build_payload

    resp = client.post("/api/projects", json=build_payload(presentation_mode="TURBO"))
    assert resp.status_code == 400


def test_presentation_mode_is_case_insensitive(client, db):
    created = create_project(client, presentation_mode="sequential")
    assert created["presentation_mode"] == "SEQUENTIAL"


# ---------------------------------------------------------------------------
# Subject状態の初期化
# ---------------------------------------------------------------------------


def test_subjects_start_as_waiting_before_scoring(client, db):
    created = _sequential_project(client)
    assert _statuses(created["project_id"]) == ["WAITING", "WAITING", "WAITING"]


def test_only_first_subject_is_scoring_after_start(client, db):
    created = _sequential_project(client)
    start_scoring(client, created["project_id"])
    assert _statuses(created["project_id"]) == ["SCORING", "WAITING", "WAITING"]


def test_batch_project_does_not_use_subject_state(client, db):
    """BATCHではsubjects.presentation_statusを書き換えない(導出で表示する)。"""
    created = create_project(client, **THREE_SUBJECTS)
    start_scoring(client, created["project_id"])
    assert _statuses(created["project_id"]) == ["WAITING", "WAITING", "WAITING"]

    progress = client.get(f"/api/projects/{created['project_id']}/progress").get_json()
    assert progress["presentation_mode"] == "BATCH"
    # 表示上はProject statusから導出される
    assert all(s["presentation_status"] == "SCORING" for s in progress["subjects"])


def test_start_scoring_is_atomic(client, db, monkeypatch):
    """Evaluation生成・Subject初期化・status更新が同一transactionであること。

    commit直前で失敗させると、どの変更も残らない。
    """
    created = _sequential_project(client)
    project_id = created["project_id"]

    from app.extensions import db as _db

    original_commit = _db.session.commit
    calls = {"count": 0}

    def exploding_commit():
        calls["count"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(_db.session, "commit", exploding_commit)
    with pytest.raises(RuntimeError):
        project_service.transition_to_scoring(db.session.get(Project, project_id))
    monkeypatch.setattr(_db.session, "commit", original_commit)

    # commitは1回しか呼ばれない = 途中commitが無い
    assert calls["count"] == 1

    _db.session.rollback()
    project = db.session.get(Project, project_id)
    assert project.status == "DRAFT"
    assert _statuses(project_id) == ["WAITING", "WAITING", "WAITING"]


# ---------------------------------------------------------------------------
# 採点ガード
# ---------------------------------------------------------------------------


def test_waiting_subject_cannot_be_scored(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    scorer = app.test_client()
    login_scorer(scorer, created["scorers"][0]["code"])
    evaluation_id = evaluation_id_for(scorer, subjects[1].id)

    resp = scorer.post(
        f"/api/evaluations/{evaluation_id}/scores",
        json={"scores": {str(c.id): 10 for c in criteria_for(project_id)}},
    )
    assert resp.status_code == 409
    assert scorer.post(f"/api/evaluations/{evaluation_id}/submit").status_code == 409


def test_current_subject_can_be_scored(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    scorer = app.test_client()
    login_scorer(scorer, created["scorers"][0]["code"])
    assert score_and_submit(scorer, project_id, subjects[0].id).status_code == 200


def test_locked_subject_cannot_be_scored(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    # 採点者1だけ先に提出しておき、その評価IDを控える
    first = app.test_client()
    login_scorer(first, created["scorers"][0]["code"])
    evaluation_id = evaluation_id_for(first, subjects[0].id)
    score_and_submit(first, project_id, subjects[0].id)

    second = app.test_client()
    login_scorer(second, created["scorers"][1]["code"])
    score_and_submit(second, project_id, subjects[0].id)

    assert _lock(client, project_id, subjects[0].id).status_code == 200

    resp = first.post(
        f"/api/evaluations/{evaluation_id}/scores",
        json={"scores": {str(c.id): 1 for c in criteria_for(project_id)}},
    )
    assert resp.status_code == 409


def test_presented_subject_cannot_be_edited_again(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    first = app.test_client()
    login_scorer(first, created["scorers"][0]["code"])
    evaluation_id = evaluation_id_for(first, subjects[0].id)
    score_and_submit(first, project_id, subjects[0].id)
    second = app.test_client()
    login_scorer(second, created["scorers"][1]["code"])
    score_and_submit(second, project_id, subjects[0].id)

    _lock(client, project_id, subjects[0].id)
    _present(client, project_id, subjects[0].id)

    resp = first.post(
        f"/api/evaluations/{evaluation_id}/scores",
        json={"scores": {str(c.id): 1 for c in criteria_for(project_id)}},
    )
    assert resp.status_code == 409


def test_scorer_ui_shows_waiting_state_read_only():
    """待機中の被採点者は入力不可で表示する(拒否の実体はサーバー側)。"""
    from pathlib import Path

    js_dir = Path(__file__).resolve().parent.parent / "app" / "static" / "js"

    scoring_js = (js_dir / "scoring.js").read_text(encoding="utf-8")
    assert "subjectStatus" in scoring_js
    assert "readOnly" in scoring_js
    assert "まだ採点の順番が来ていません" in scoring_js
    # disabledはUXのためだけであることをコメントで明示している
    assert "サーバー側" in scoring_js

    dashboard_js = (js_dir / "scorer_dashboard.js").read_text(encoding="utf-8")
    assert "subject_status" in dashboard_js
    assert "待機中" in dashboard_js


def test_scorer_dashboard_marks_waiting_subjects(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    scorer = app.test_client()
    login_scorer(scorer, created["scorers"][0]["code"])
    rows = scorer.get("/api/scorer/me/evaluations").get_json()["subjects"]

    assert [r["subject_status"] for r in rows] == ["SCORING", "WAITING", "WAITING"]
    assert [r["scorable"] for r in rows] == [True, False, False]


# ---------------------------------------------------------------------------
# subject lock
# ---------------------------------------------------------------------------


def test_lock_rejected_until_all_participating_scorers_submit(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    assert _lock(client, project_id, subjects[0].id).status_code == 409

    only_one = app.test_client()
    login_scorer(only_one, created["scorers"][0]["code"])
    score_and_submit(only_one, project_id, subjects[0].id)

    resp = _lock(client, project_id, subjects[0].id)
    assert resp.status_code == 409
    assert "Forced close is not available" in resp.get_json()["error"]


def test_lock_succeeds_when_everyone_submitted(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)
    _submit_all_for(app, created, project_id, subjects[0].id)

    resp = _lock(client, project_id, subjects[0].id)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["presentation_status"] == "LOCKED"
    assert body["locked_at"] is not None
    assert _statuses(project_id) == ["LOCKED", "WAITING", "WAITING"]


def test_lock_is_rejected_twice(client, app, db):
    """二重クリック・二重requestで状態が壊れないこと。"""
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)
    _submit_all_for(app, created, project_id, subjects[0].id)

    assert _lock(client, project_id, subjects[0].id).status_code == 200
    assert _lock(client, project_id, subjects[0].id).status_code == 409
    assert _statuses(project_id) == ["LOCKED", "WAITING", "WAITING"]


def test_lock_rejected_for_waiting_subject(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)
    assert _lock(client, project_id, subjects[1].id).status_code == 409


def test_subject_lock_is_rejected_in_batch_mode(client, app, db):
    created = create_project(client, **THREE_SUBJECTS)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)
    _submit_all_for(app, created, project_id, subjects[0].id)

    resp = _lock(client, project_id, subjects[0].id)
    assert resp.status_code == 409
    assert "SEQUENTIAL" in resp.get_json()["error"]


# ---------------------------------------------------------------------------
# subject present と次Subjectへの進行
# ---------------------------------------------------------------------------


def test_present_advances_to_the_next_subject(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)
    _submit_all_for(app, created, project_id, subjects[0].id)
    _lock(client, project_id, subjects[0].id)

    resp = _present(client, project_id, subjects[0].id)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["subject"]["presentation_status"] == "PRESENTED"
    assert body["subject"]["presented_at"] is not None
    assert body["next_subject"]["id"] == subjects[1].id
    assert body["next_subject"]["presentation_status"] == "SCORING"
    assert _statuses(project_id) == ["PRESENTED", "SCORING", "WAITING"]


def test_present_requires_locked_subject(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    assert _present(client, project_id, subjects[0].id).status_code == 409
    _submit_all_for(app, created, project_id, subjects[0].id)
    assert _present(client, project_id, subjects[0].id).status_code == 409


def test_present_cannot_be_repeated(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)
    _submit_all_for(app, created, project_id, subjects[0].id)
    _lock(client, project_id, subjects[0].id)

    assert _present(client, project_id, subjects[0].id).status_code == 200
    assert _present(client, project_id, subjects[0].id).status_code == 409
    assert _statuses(project_id) == ["PRESENTED", "SCORING", "WAITING"]


def test_last_subject_present_leaves_project_scoring(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    for subject in subjects_for(project_id):
        _submit_all_for(app, created, project_id, subject.id)
        _lock(client, project_id, subject.id)
        body = _present(client, project_id, subject.id).get_json()
        # 各ステップでProject statusはSCORINGのまま
        assert db.session.get(Project, project_id).status == "SCORING"

    assert body["next_subject"] is None
    assert _statuses(project_id) == ["PRESENTED"] * 3
    assert db.session.get(Project, project_id).status == "SCORING"


# ---------------------------------------------------------------------------
# scorer set の固定
# ---------------------------------------------------------------------------


def test_participating_scorer_set_never_changes(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    expected = {s["id"] for s in created["scorers"]}
    observed = [project_service.participating_scorer_ids(project_id)]

    for subject in subjects_for(project_id):
        _submit_all_for(app, created, project_id, subject.id)
        observed.append(project_service.participating_scorer_ids(project_id))
        _lock(client, project_id, subject.id)
        observed.append(project_service.participating_scorer_ids(project_id))
        _present(client, project_id, subject.id)
        observed.append(project_service.participating_scorer_ids(project_id))

    assert all(ids == expected for ids in observed), observed


def test_scorers_cannot_be_added_after_scoring_starts(client, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    resp = client.post(
        f"/api/projects/{project_id}/scorers", json={"display_name": "飛び入り"}
    )
    assert resp.status_code == 409


def test_every_subject_is_scored_by_the_same_number_of_judges(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    for subject in subjects_for(project_id):
        _submit_all_for(app, created, project_id, subject.id)
        _lock(client, project_id, subject.id)
        _present(client, project_id, subject.id)

    for target in ("LOCKED", "PRESENTING"):
        client.post(f"/api/projects/{project_id}/transition", json={"target_status": target})

    summary = client.get(f"/api/projects/{project_id}/result-summary").get_json()
    counts = {s["scorer_count"] for s in summary["subjects"]}
    assert counts == {len(created["scorers"])}


# ---------------------------------------------------------------------------
# Project statusの不可逆性 / forced close禁止
# ---------------------------------------------------------------------------


def test_project_lock_rejected_before_all_subjects_presented(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    _submit_all_for(app, created, project_id, subjects[0].id)
    _lock(client, project_id, subjects[0].id)
    _present(client, project_id, subjects[0].id)

    resp = client.post(
        f"/api/projects/{project_id}/transition", json={"target_status": "LOCKED"}
    )
    assert resp.status_code == 409
    assert "all subjects must be presented" in resp.get_json()["error"].lower()
    assert db.session.get(Project, project_id).status == "SCORING"


def test_sequential_has_no_forced_close(client, app, db):
    """未提出者がいる状態では、Subjectも Project も締め切れない。"""
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    partial = app.test_client()
    login_scorer(partial, created["scorers"][0]["code"])
    score_and_submit(partial, project_id, subjects[0].id)

    assert _lock(client, project_id, subjects[0].id).status_code == 409
    assert (
        client.post(
            f"/api/projects/{project_id}/transition", json={"target_status": "LOCKED"}
        ).status_code
        == 409
    )
    assert db.session.get(Project, project_id).status == "SCORING"


def test_project_status_is_never_rolled_back(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    for subject in subjects_for(project_id):
        _submit_all_for(app, created, project_id, subject.id)
        _lock(client, project_id, subject.id)
        _present(client, project_id, subject.id)

    for target in ("LOCKED", "PRESENTING", "FINISHED"):
        assert (
            client.post(
                f"/api/projects/{project_id}/transition", json={"target_status": target}
            ).status_code
            == 200
        )

    for target in ("PRESENTING", "LOCKED", "SCORING", "DRAFT"):
        assert (
            client.post(
                f"/api/projects/{project_id}/transition", json={"target_status": target}
            ).status_code
            == 409
        )
    assert db.session.get(Project, project_id).status == "FINISHED"


def test_subject_progression_rejected_once_project_locked(client, app, db):
    created = _sequential_project(client, subjects=["チームA"], scorers=["採点者1"])
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subject = subjects_for(project_id)[0]

    _submit_all_for(app, created, project_id, subject.id)
    _lock(client, project_id, subject.id)
    _present(client, project_id, subject.id)
    client.post(f"/api/projects/{project_id}/transition", json={"target_status": "LOCKED"})

    assert _lock(client, project_id, subject.id).status_code == 409
    assert _present(client, project_id, subject.id).status_code == 409


# ---------------------------------------------------------------------------
# authorization
# ---------------------------------------------------------------------------


def test_subject_progression_requires_host_session(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subject = subjects_for(project_id)[0]

    anonymous = app.test_client()
    assert _lock(anonymous, project_id, subject.id).status_code == 403
    assert _present(anonymous, project_id, subject.id).status_code == 403


def test_subject_progression_rejects_other_project_host(client, app, db):
    mine = _sequential_project(client, name="自分の")
    start_scoring(client, mine["project_id"])

    other = app.test_client()
    theirs = _sequential_project(other, name="他人の")
    start_scoring(other, theirs["project_id"])
    their_subject = subjects_for(theirs["project_id"])[0]

    # 自分のHost sessionで他人のプロジェクトを操作しようとする
    assert _lock(client, theirs["project_id"], their_subject.id).status_code == 403
    assert _present(client, theirs["project_id"], their_subject.id).status_code == 403


def test_subject_must_belong_to_the_project_in_the_url(client, app, db):
    """URLのproject_idと別プロジェクトのsubject_idの組み合わせを拒否する。"""
    mine = _sequential_project(client, name="自分の")
    start_scoring(client, mine["project_id"])

    other = app.test_client()
    theirs = _sequential_project(other, name="他人の")
    start_scoring(other, theirs["project_id"])
    their_subject = subjects_for(theirs["project_id"])[0]

    # Host権限は自分のプロジェクトに対して有効だが、subjectは他人のもの
    resp = _lock(client, mine["project_id"], their_subject.id)
    assert resp.status_code == 403
    assert (
        db.session.get(Subject, their_subject.id).presentation_status == "SCORING"
    )


# ---------------------------------------------------------------------------
# Host Dashboard 向けの進行情報
# ---------------------------------------------------------------------------


def test_progress_reports_sequential_progression(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    progress = client.get(f"/api/projects/{project_id}/progress").get_json()
    assert progress["presentation_mode"] == "SEQUENTIAL"
    assert progress["participating_scorer_count"] == 2
    assert progress["current_subject_id"] == subjects[0].id
    assert progress["presentable_subject_id"] is None
    assert progress["all_subjects_presented"] is False

    first = progress["subjects"][0]
    assert first["presentation_status"] == "SCORING"
    assert first["submitted_count"] == 0
    assert first["pending_count"] == 2
    assert first["can_lock"] is False

    _submit_all_for(app, created, project_id, subjects[0].id)
    progress = client.get(f"/api/projects/{project_id}/progress").get_json()
    first = progress["subjects"][0]
    assert first["submitted_count"] == 2
    assert first["pending_count"] == 0
    assert first["can_lock"] is True

    _lock(client, project_id, subjects[0].id)
    progress = client.get(f"/api/projects/{project_id}/progress").get_json()
    assert progress["presentable_subject_id"] == subjects[0].id
    assert progress["subjects"][0]["can_lock"] is False


def test_progress_keeps_existing_batch_fields(client, app, db):
    """既存のpollingが依存するフィールドを壊さないこと。"""
    created = create_project(client, **THREE_SUBJECTS)
    start_scoring(client, created["project_id"])
    progress = client.get(f"/api/projects/{created['project_id']}/progress").get_json()

    for key in (
        "project_status",
        "subjects",
        "scorers",
        "submitted_count",
        "total_count",
        "eligible_scorer_count",
        "incomplete_scorer_count",
    ):
        assert key in progress, key
    for scorer in progress["scorers"]:
        assert {"scorer_id", "display_name", "statuses", "eligible"} <= set(scorer)
