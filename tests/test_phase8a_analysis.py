"""Phase 8A-2: result_serviceの再設計 と Host結果分析(feedback閲覧)。"""

from __future__ import annotations

import re
from pathlib import Path

from app.models import Evaluation, Project
from tests.helpers import (
    create_project,
    criteria_for,
    login_host,
    login_scorer,
    score_and_submit,
    start_scoring,
    subjects_for,
)

APP_DIR = Path(__file__).resolve().parent.parent / "app"


def _finished_project(client, app, *, scores=(18, 12)):
    """2 Scorer x 2 Subjectを全員提出させてFINISHEDまで進める。"""
    created = create_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    for scorer_info, value in zip(created["scorers"], scores):
        scorer = app.test_client()
        login_scorer(scorer, scorer_info["code"])
        for subject in subjects_for(project_id):
            score_and_submit(
                scorer,
                project_id,
                subject.id,
                value=value,
                feedback=f"{scorer_info['display_name']}から{subject.name}へ",
            )

    for target in ("LOCKED", "PRESENTING", "FINISHED"):
        assert (
            client.post(
                f"/api/projects/{project_id}/transition", json={"target_status": target}
            ).status_code
            == 200
        )
    return created


def _forced_closed_project(client, app):
    """Scorer2がチームBを未提出のまま強制締切する(BATCHのforced close)。"""
    created = create_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    complete = app.test_client()
    login_scorer(complete, created["scorers"][0]["code"])
    for subject in subjects:
        score_and_submit(complete, project_id, subject.id, value=20,
                         feedback=f"完走者から{subject.name}へ")

    partial = app.test_client()
    login_scorer(partial, created["scorers"][1]["code"])
    score_and_submit(partial, project_id, subjects[0].id, value=4,
                     feedback="未完了者からチームAへ")

    assert (
        client.post(
            f"/api/projects/{project_id}/transition", json={"target_status": "LOCKED"}
        ).status_code
        == 200
    )
    return created, subjects


# ---------------------------------------------------------------------------
# result_service 再設計後の互換性
# ---------------------------------------------------------------------------


def test_result_summary_keeps_existing_keys(client, app, db):
    created = _finished_project(client, app)
    summary = client.get(
        f"/api/projects/{created['project_id']}/result-summary"
    ).get_json()

    assert set(summary) >= {
        "project",
        "criteria",
        "theoretical_max_total",
        "eligible_scorer_count",
        "subjects",
    }
    assert set(summary["project"]) >= {"id", "name", "status"}
    for subject in summary["subjects"]:
        assert set(subject) >= {
            "id",
            "name",
            "sort_order",
            "scorer_count",
            "total_score",
            "mean_score",
            "criterion_averages",
            "judge_totals",
            "rank",
        }


def test_result_summary_exposes_presentation_mode_and_status(client, app, db):
    created = _finished_project(client, app)
    summary = client.get(
        f"/api/projects/{created['project_id']}/result-summary"
    ).get_json()

    assert summary["project"]["presentation_mode"] == "BATCH"
    assert summary["official_scorer_count"] == summary["eligible_scorer_count"] == 2
    # BATCHのSubject状態はProject.statusから導出される
    assert all(s["presentation_status"] == "PRESENTED" for s in summary["subjects"])


def test_judge_totals_are_ordered_deterministically(client, app, db):
    created = _finished_project(client, app)
    summary = client.get(
        f"/api/projects/{created['project_id']}/result-summary"
    ).get_json()

    for subject in summary["subjects"]:
        scorer_ids = [j["scorer_id"] for j in subject["judge_totals"]]
        assert scorer_ids == sorted(scorer_ids), subject["name"]


def test_result_service_orders_evaluations_explicitly():
    """DB返却順に依存しないよう、明示的なORDER BYがあること。"""
    source = (APP_DIR / "services" / "result_service.py").read_text(encoding="utf-8")
    assert "order_by(Evaluation.scorer_id)" in source


# ---------------------------------------------------------------------------
# official scorer 判定
# ---------------------------------------------------------------------------


def test_participating_scorer_ids_is_derived_from_evaluations(client, app, db):
    from app.services.project_service import participating_scorer_ids

    created = create_project(client)
    project_id = created["project_id"]
    assert participating_scorer_ids(project_id) == set()

    start_scoring(client, project_id)
    expected = {s["id"] for s in created["scorers"]}
    assert participating_scorer_ids(project_id) == expected


def test_batch_official_scorers_are_eligible_scorers(client, app, db):
    from app.services.project_service import eligible_scorer_ids, official_scorer_ids

    created, _ = _forced_closed_project(client, app)
    project = db.session.get(Project, created["project_id"])

    assert official_scorer_ids(project) == eligible_scorer_ids(project.id)
    assert official_scorer_ids(project) == {created["scorers"][0]["id"]}


# ---------------------------------------------------------------------------
# Analysis: authorization / status gate
# ---------------------------------------------------------------------------


def test_analysis_requires_host_session(client, app, db):
    created = _finished_project(client, app)
    anonymous = app.test_client()
    resp = anonymous.get(f"/api/projects/{created['project_id']}/analysis")
    assert resp.status_code == 403


def test_analysis_rejects_other_project_host(client, app, db):
    mine = _finished_project(client, app)

    other_client = app.test_client()
    theirs = _finished_project(other_client, app)

    login_host(client, mine["host_code"])
    resp = client.get(f"/api/projects/{theirs['project_id']}/analysis")
    assert resp.status_code == 403


def test_analysis_rejected_before_locked(client, db):
    created = create_project(client)
    project_id = created["project_id"]

    assert client.get(f"/api/projects/{project_id}/analysis").status_code == 409
    start_scoring(client, project_id)
    assert client.get(f"/api/projects/{project_id}/analysis").status_code == 409


def test_analysis_available_from_locked_onwards(client, app, db):
    created, _ = _forced_closed_project(client, app)
    resp = client.get(f"/api/projects/{created['project_id']}/analysis")
    assert resp.status_code == 200


def test_analysis_returns_404_for_unknown_project(client, db):
    created = create_project(client)
    with client.session_transaction() as sess:
        sess["host_project_id"] = 99999
    assert client.get("/api/projects/99999/analysis").status_code == 404
    assert created  # 作成済みプロジェクトとは無関係であることを明示


# ---------------------------------------------------------------------------
# Analysis: feedback と official/excluded の識別
# ---------------------------------------------------------------------------


def test_analysis_exposes_submitted_feedback(client, app, db):
    created = _finished_project(client, app)
    data = client.get(f"/api/projects/{created['project_id']}/analysis").get_json()

    by_name = {s["name"]: s for s in data["subjects"]}
    feedbacks = {e["feedback"] for e in by_name["チームA"]["evaluations"]}
    assert "採点者1からチームAへ" in feedbacks
    assert "採点者2からチームAへ" in feedbacks


def test_analysis_marks_excluded_scorer_feedback_without_counting_scores(client, app, db):
    """forced closeで除外されたScorerのfeedbackは残るが、点数は加算されない。"""
    created, subjects = _forced_closed_project(client, app)
    data = client.get(f"/api/projects/{created['project_id']}/analysis").get_json()

    assert data["official_scorer_count"] == 1
    assert data["excluded_scorer_count"] == 1

    team_a = next(s for s in data["subjects"] if s["name"] == subjects[0].name)
    by_flag = {e["official_included"]: e for e in team_a["evaluations"]}
    assert set(by_flag) == {True, False}
    assert by_flag[False]["feedback"] == "未完了者からチームAへ"
    assert by_flag[False]["total"] == 20  # 4点 x 5軸。保持はされている

    # 公式スコアは完走者(20点 x 5軸 = 100点)のみ
    assert team_a["total_score"] == 100
    assert team_a["scorer_count"] == 1
    assert [j["scorer_id"] for j in team_a["judge_totals"]] == [
        created["scorers"][0]["id"]
    ]


def test_analysis_excludes_unsubmitted_drafts(client, app, db):
    """draftのまま確定していない評価は結果として扱わない。"""
    created = create_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    complete = app.test_client()
    login_scorer(complete, created["scorers"][0]["code"])
    for subject in subjects:
        score_and_submit(complete, project_id, subject.id, value=10)

    # Scorer2はチームAをdraft保存のみ(submitしない)
    drafting = app.test_client()
    login_scorer(drafting, created["scorers"][1]["code"])
    dashboard = drafting.get("/api/scorer/me/evaluations").get_json()
    draft_eval_id = next(
        r["evaluation_id"] for r in dashboard["subjects"] if r["subject_id"] == subjects[0].id
    )
    drafting.post(
        f"/api/evaluations/{draft_eval_id}/scores",
        json={
            "scores": {str(c.id): 3 for c in criteria_for(project_id)},
            "feedback": "これは未提出の下書き",
        },
    )

    client.post(f"/api/projects/{project_id}/transition", json={"target_status": "LOCKED"})
    data = client.get(f"/api/projects/{project_id}/analysis").get_json()

    all_feedback = [
        e["feedback"] for s in data["subjects"] for e in s["evaluations"]
    ]
    assert "これは未提出の下書き" not in all_feedback
    assert db.session.get(Evaluation, draft_eval_id).status == "draft"


def test_analysis_criterion_averages_carry_name_and_max_score(client, app, db):
    """Radar Chartがこの配列だけで描けること。"""
    created = _finished_project(client, app)
    data = client.get(f"/api/projects/{created['project_id']}/analysis").get_json()

    criterion_names = [c["name"] for c in data["criteria"]]
    for subject in data["subjects"]:
        assert len(subject["criterion_averages"]) == 5
        for average in subject["criterion_averages"]:
            assert set(average) == {"criterion_id", "average", "name", "max_score"}
            assert average["name"] in criterion_names
            assert average["max_score"] == 20
            assert 0 <= average["average"] <= average["max_score"]


def test_analysis_ranking_matches_result_summary(client, app, db):
    created = _finished_project(client, app)
    project_id = created["project_id"]

    summary = client.get(f"/api/projects/{project_id}/result-summary").get_json()
    analysis = client.get(f"/api/projects/{project_id}/analysis").get_json()

    def shape(payload):
        return [
            (s["name"], s["rank"], s["total_score"], s["mean_score"])
            for s in payload["subjects"]
        ]

    assert shape(summary) == shape(analysis)


def test_analysis_never_exposes_codes_or_hashes(client, app, db):
    created = _finished_project(client, app)
    raw = client.get(
        f"/api/projects/{created['project_id']}/analysis"
    ).get_data(as_text=True)

    assert created["host_code"] not in raw
    for scorer in created["scorers"]:
        assert scorer["code"] not in raw
    for token in ("host_code_hash", "access_code_hash", "code_hash"):
        assert token not in raw


# ---------------------------------------------------------------------------
# 画面の導線
# ---------------------------------------------------------------------------


def test_analysis_page_is_served(client, app, db):
    created = _finished_project(client, app)
    resp = client.get(f"/host/{created['project_id']}/analysis")
    assert resp.status_code == 200


def test_analysis_page_references_only_existing_assets(client, app, db):
    created = _finished_project(client, app)
    html = client.get(f"/host/{created['project_id']}/analysis").get_data(as_text=True)
    for href in re.findall(r'(?:href|src)="(/static/[^"]+)"', html):
        assert client.get(href).status_code == 200, href


def test_analysis_dom_ids_match_js_expectations(client, app, db):
    created = _finished_project(client, app)
    html = client.get(f"/host/{created['project_id']}/analysis").get_data(as_text=True)
    js = (APP_DIR / "static" / "js" / "host_analysis.js").read_text(encoding="utf-8")

    for element_id in re.findall(r'getElementById\("([^"]+)"\)', js):
        assert f'id="{element_id}"' in html, element_id


def test_host_dashboard_links_to_analysis(client, app, db):
    created = _finished_project(client, app)
    html = client.get(f"/host/{created['project_id']}").get_data(as_text=True)
    assert 'id="analysisLink"' in html

    js = (APP_DIR / "static" / "js" / "host_dashboard.js").read_text(encoding="utf-8")
    assert "/analysis`" in js


def test_finished_panel_links_to_analysis(client, app, db):
    created = _finished_project(client, app)
    html = client.get(f"/host/{created['project_id']}/present").get_data(as_text=True)
    assert 'id="finishedAnalysisLink"' in html
