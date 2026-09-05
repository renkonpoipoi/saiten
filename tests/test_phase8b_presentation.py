"""Phase 8B-2: SEQUENTIAL の結果発表(Subject単位のReveal)と最終ランキング。"""

from __future__ import annotations

import re
from pathlib import Path

from app.models import Project
from tests.helpers import (
    create_project,
    login_scorer,
    score_and_submit,
    start_scoring,
    subjects_for,
)

APP_DIR = Path(__file__).resolve().parent.parent / "app"

THREE_SUBJECTS = {
    "subjects": ["チームA", "チームB", "チームC"],
    "scorers": ["採点者1", "採点者2"],
}


def _sequential_project(client, **overrides):
    payload = dict(THREE_SUBJECTS, presentation_mode="SEQUENTIAL")
    payload.update(overrides)
    return create_project(client, **payload)


def _submit_all_for(app, created, project_id, subject_id, values=None):
    for index, scorer_info in enumerate(created["scorers"]):
        scorer = app.test_client()
        login_scorer(scorer, scorer_info["code"])
        value = values[index] if values else 15
        score_and_submit(scorer, project_id, subject_id, value=value)


def _advance_subject(client, app, created, project_id, subject_id, values=None):
    _submit_all_for(app, created, project_id, subject_id, values)
    assert (
        client.post(f"/api/projects/{project_id}/subjects/{subject_id}/lock").status_code
        == 200
    )
    return client.post(f"/api/projects/{project_id}/subjects/{subject_id}/present")


# ---------------------------------------------------------------------------
# presentation-state
# ---------------------------------------------------------------------------


def test_presentation_state_tracks_progression(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    state = client.get(f"/api/projects/{project_id}/presentation-state").get_json()
    assert state["project"]["presentation_mode"] == "SEQUENTIAL"
    assert state["project"]["status"] == "SCORING"
    assert state["current_subject_id"] == subjects[0].id
    assert state["presentable_subject_id"] is None
    assert state["all_subjects_presented"] is False
    assert state["participating_scorer_count"] == 2

    _submit_all_for(app, created, project_id, subjects[0].id)
    client.post(f"/api/projects/{project_id}/subjects/{subjects[0].id}/lock")

    state = client.get(f"/api/projects/{project_id}/presentation-state").get_json()
    assert state["presentable_subject_id"] == subjects[0].id


def test_presentation_state_never_exposes_scores(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    _submit_all_for(app, created, project_id, subjects_for(project_id)[0].id)

    raw = client.get(
        f"/api/projects/{project_id}/presentation-state"
    ).get_data(as_text=True)
    for token in ("total_score", "judge_totals", "criterion_averages", "mean_score"):
        assert token not in raw, token


def test_presentation_state_requires_host(client, app, db):
    created = _sequential_project(client)
    anonymous = app.test_client()
    resp = anonymous.get(
        f"/api/projects/{created['project_id']}/presentation-state"
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# subject result (Judge単位のReveal data)
# ---------------------------------------------------------------------------


def test_subject_result_is_unavailable_before_lock(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    # 採点中でも待機中でも取得できない
    for subject in (subjects[0], subjects[1]):
        resp = client.get(
            f"/api/projects/{project_id}/subjects/{subject.id}/result"
        )
        assert resp.status_code == 409, subject.name

    _submit_all_for(app, created, project_id, subjects[0].id)
    assert (
        client.get(
            f"/api/projects/{project_id}/subjects/{subjects[0].id}/result"
        ).status_code
        == 409
    )


def test_subject_result_gives_judge_totals_after_lock(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    _submit_all_for(app, created, project_id, subjects[0].id, values=[20, 10])
    client.post(f"/api/projects/{project_id}/subjects/{subjects[0].id}/lock")

    payload = client.get(
        f"/api/projects/{project_id}/subjects/{subjects[0].id}/result"
    ).get_json()

    assert payload["theoretical_max_total"] == 100
    subject = payload["subject"]
    assert subject["name"] == "チームA"
    assert subject["presentation_status"] == "LOCKED"
    assert subject["scorer_count"] == 2
    # 20点x5軸 + 10点x5軸
    assert subject["total_score"] == 150
    totals = [j["total"] for j in subject["judge_totals"]]
    assert totals == [100, 50]
    assert [j["display_name"] for j in subject["judge_totals"]] == ["採点者1", "採点者2"]
    # Subject TOTAL は judge_totals の合計と一致する
    assert sum(totals) == subject["total_score"]


def test_subject_result_shape_matches_summary_subjects(client, app, db):
    """発表演出をBATCHと共用できるよう、subjectの形が同じであること。"""
    created = _sequential_project(client, subjects=["チームA"], scorers=["採点者1"])
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subject = subjects_for(project_id)[0]

    _advance_subject(client, app, created, project_id, subject.id)
    subject_payload = client.get(
        f"/api/projects/{project_id}/subjects/{subject.id}/result"
    ).get_json()["subject"]

    for target in ("LOCKED", "PRESENTING"):
        client.post(f"/api/projects/{project_id}/transition", json={"target_status": target})
    summary_subject = client.get(
        f"/api/projects/{project_id}/result-summary"
    ).get_json()["subjects"][0]

    assert set(subject_payload) <= set(summary_subject)
    for key in ("id", "name", "total_score", "judge_totals", "criterion_averages"):
        assert subject_payload[key] == summary_subject[key], key


def test_subject_result_still_available_after_present(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)
    _advance_subject(client, app, created, project_id, subjects[0].id)

    resp = client.get(f"/api/projects/{project_id}/subjects/{subjects[0].id}/result")
    assert resp.status_code == 200
    assert resp.get_json()["subject"]["presentation_status"] == "PRESENTED"


def test_subject_result_requires_host_and_matching_project(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)
    _advance_subject(client, app, created, project_id, subjects[0].id)

    anonymous = app.test_client()
    assert (
        anonymous.get(
            f"/api/projects/{project_id}/subjects/{subjects[0].id}/result"
        ).status_code
        == 403
    )

    other = app.test_client()
    theirs = _sequential_project(other, name="他人の")
    start_scoring(other, theirs["project_id"])
    their_subject = subjects_for(theirs["project_id"])[0]

    # 自分のHost sessionで、自分のproject_id + 他人のsubject_id を渡す
    resp = client.get(
        f"/api/projects/{project_id}/subjects/{their_subject.id}/result"
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 最終ランキング
# ---------------------------------------------------------------------------


def test_final_ranking_after_all_subjects_presented(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    # A: 20/20, B: 20/20, C: 10/10 -> A,Bが同点1位、Cが3位
    _advance_subject(client, app, created, project_id, subjects[0].id, values=[20, 20])
    _advance_subject(client, app, created, project_id, subjects[1].id, values=[20, 20])
    _advance_subject(client, app, created, project_id, subjects[2].id, values=[10, 10])

    state = client.get(f"/api/projects/{project_id}/presentation-state").get_json()
    assert state["all_subjects_presented"] is True
    assert db.session.get(Project, project_id).status == "SCORING"

    for target in ("LOCKED", "PRESENTING"):
        assert (
            client.post(
                f"/api/projects/{project_id}/transition", json={"target_status": target}
            ).status_code
            == 200
        )

    summary = client.get(f"/api/projects/{project_id}/result-summary").get_json()
    by_name = {s["name"]: s for s in summary["subjects"]}
    assert by_name["チームA"]["total_score"] == 200
    assert by_name["チームB"]["total_score"] == 200
    assert by_name["チームC"]["total_score"] == 100
    assert by_name["チームA"]["rank"] == 1
    assert by_name["チームB"]["rank"] == 1
    assert by_name["チームC"]["rank"] == 3
    assert summary["official_scorer_count"] == 2


def test_presentation_state_after_finish(client, app, db):
    created = _sequential_project(client, subjects=["チームA"], scorers=["採点者1"])
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subject = subjects_for(project_id)[0]
    _advance_subject(client, app, created, project_id, subject.id)

    for target in ("LOCKED", "PRESENTING", "FINISHED"):
        client.post(f"/api/projects/{project_id}/transition", json={"target_status": target})

    state = client.get(f"/api/projects/{project_id}/presentation-state").get_json()
    assert state["project"]["status"] == "FINISHED"
    assert state["all_subjects_presented"] is True

    # replayに使うAPIはGETのみで、状態は変わらない
    assert client.get(f"/api/projects/{project_id}/result-summary").status_code == 200
    assert db.session.get(Project, project_id).status == "FINISHED"


# ---------------------------------------------------------------------------
# 画面 / JS
# ---------------------------------------------------------------------------


def test_presentation_page_has_sequential_panels(client, app, db):
    created = _sequential_project(client)
    html = client.get(f"/host/{created['project_id']}/present").get_data(as_text=True)
    for element_id in (
        "sequentialWaitingPanel",
        "sequentialWaitingText",
        "sequentialStandbyPanel",
        "sequentialSubjectName",
        "revealSubjectButton",
        "confirmNextPanel",
        "confirmNextButton",
        "finalRankingPanel",
        "finalRankingButton",
    ):
        assert f'id="{element_id}"' in html, element_id


def test_presentation_dom_ids_still_match_js(client, app, db):
    created = _sequential_project(client)
    html = client.get(f"/host/{created['project_id']}/present").get_data(as_text=True)
    js = (APP_DIR / "static" / "js" / "result_presentation.js").read_text(encoding="utf-8")
    for element_id in re.findall(r'getElementById\("([^"]+)"\)', js):
        assert f'id="{element_id}"' in html, element_id


def test_presentation_uses_one_url_for_both_modes():
    """モードごとに別URLを作らず、/host/<id>/present に統合していること。"""
    from app import create_app

    app = create_app({"APP_ENV": "testing", "SECRET_KEY": "x"})
    # 画面ルート(APIを除く)に発表画面が1つしかないこと
    present_pages = [
        rule.rule
        for rule in app.url_map.iter_rules()
        if "/present" in rule.rule and not rule.rule.startswith("/api/")
    ]
    assert present_pages == ["/host/<int:project_id>/present"]


def test_sequential_reveal_reuses_the_batch_reveal_function():
    """1 Subjectの得点発表engineはBATCH / SEQUENTIALで完全に同一のものを使う。"""
    js = (APP_DIR / "static" / "js" / "result_presentation.js").read_text(encoding="utf-8")
    # 得点発表engineの定義は1つだけ
    assert js.count("async function revealSubjectSequence(") == 1
    # BATCH側とSEQUENTIAL側の両方がそれを呼ぶ
    assert "await revealSubjectSequence(subject, showBatchAdvance)" in js
    assert "await revealSubjectSequence(payload.subject," in js


def test_sequential_presentation_polling_has_safeguards():
    js = (APP_DIR / "static" / "js" / "result_presentation.js").read_text(encoding="utf-8")
    assert "SEQUENTIAL_POLL_INTERVAL_MS = 15000" in js
    assert "document.hidden" in js
    assert "pollInFlight" in js
    assert "stopPolling" in js
    assert "visibilitychange" in js


def test_sequential_never_moves_project_status_backwards():
    """発表画面がProject statusを前向きにしか動かさないこと。"""
    js = (APP_DIR / "static" / "js" / "result_presentation.js").read_text(encoding="utf-8")
    targets = re.findall(r'target_status: "([A-Z]+)"', js)
    assert set(targets) <= {"LOCKED", "PRESENTING", "FINISHED"}
    assert "SCORING" not in targets
    assert "DRAFT" not in targets


# ---------------------------------------------------------------------------
# Host Dashboard の進行パネル
# ---------------------------------------------------------------------------


def test_host_dashboard_has_sequential_panel(client, app, db):
    created = _sequential_project(client)
    html = client.get(f"/host/{created['project_id']}").get_data(as_text=True)
    for element_id in (
        "sequentialSection",
        "sequentialProgressRows",
        "sequentialCurrentStatus",
        "lockSubjectButton",
        "sequentialPresentLink",
    ):
        assert f'id="{element_id}"' in html, element_id


def test_host_dashboard_dom_ids_match_js(client, app, db):
    created = _sequential_project(client)
    html = client.get(f"/host/{created['project_id']}").get_data(as_text=True)
    js = (APP_DIR / "static" / "js" / "host_dashboard.js").read_text(encoding="utf-8")
    for element_id in re.findall(r'getElementById\("([^"]+)"\)', js):
        assert f'id="{element_id}"' in html, element_id


def test_host_dashboard_hides_batch_close_ui_in_sequential():
    js = (APP_DIR / "static" / "js" / "host_dashboard.js").read_text(encoding="utf-8")
    assert 'data.presentation_mode === "SEQUENTIAL"' in js
    assert "sequentialSection" in js
    # BATCHのforced close文言はSEQUENTIALでは出さない
    assert "集計から除外されます" in js
