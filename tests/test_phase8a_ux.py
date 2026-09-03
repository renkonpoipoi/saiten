"""Phase 8A-1: 作成者のHost session / Submit後の導線 / FINISHED後のUX。"""

from __future__ import annotations

import re
from pathlib import Path

from app.models import Project
from app.services.code_service import hash_code
from tests.helpers import (
    build_payload,
    create_project,
    criteria_for,
    login_host,
    login_scorer,
    score_and_submit,
    start_scoring,
    subjects_for,
)

APP_DIR = Path(__file__).resolve().parent.parent / "app"


# ---------------------------------------------------------------------------
# 作成者をそのままHostとして認証する
# ---------------------------------------------------------------------------


def test_project_creation_grants_host_session(client, db):
    created = create_project(client)
    with client.session_transaction() as sess:
        assert sess["host_project_id"] == created["project_id"]


def test_creator_can_use_host_api_without_logging_in(client, db):
    """host-loginを一度も呼ばずにHost APIが200になること。"""
    created = create_project(client)
    resp = client.get(f"/api/projects/{created['project_id']}/progress")
    assert resp.status_code == 200


def test_host_code_is_still_issued_and_stored_as_hash_only(client, db):
    created = create_project(client)
    project = db.session.get(Project, created["project_id"])

    assert created["host_code"].startswith("host_")
    assert project.host_code_hash == hash_code(created["host_code"])
    # 平文がDBのどこにも残らないこと
    assert created["host_code"] != project.host_code_hash
    assert created["host_code"] not in project.host_code_hash


def test_other_browser_still_requires_host_code(client, app, db):
    """別ブラウザ(=別session)ではHost Codeでのログインが必須のまま。"""
    created = create_project(client)
    project_id = created["project_id"]

    other = app.test_client()
    assert other.get(f"/api/projects/{project_id}/progress").status_code == 403

    login_host(other, created["host_code"])
    assert other.get(f"/api/projects/{project_id}/progress").status_code == 200


def test_creating_a_second_project_moves_the_host_session(client, db):
    """sessionが保持できるHost権限は1プロジェクト分だけ(既存host-loginと同じ挙動)。"""
    first = create_project(client, name="1つ目")
    second = create_project(client, name="2つ目")

    assert client.get(f"/api/projects/{second['project_id']}/progress").status_code == 200
    assert client.get(f"/api/projects/{first['project_id']}/progress").status_code == 403


def test_creator_session_does_not_leak_to_other_projects(client, app, db):
    """作成者sessionが他人のプロジェクトへ波及しないこと。"""
    mine = create_project(client, name="自分の")

    stranger = app.test_client()
    theirs = create_project(stranger, name="他人の")

    assert client.get(f"/api/projects/{theirs['project_id']}/progress").status_code == 403
    assert stranger.get(f"/api/projects/{mine['project_id']}/progress").status_code == 403


def test_project_create_is_rate_limited(client_with_ratelimit_only):
    """session発行endpointになったため、作成APIにrate limitがかかること。

    (dbフixtureを取らないのは、Flask-Limiterの有効/無効がinit_app()時に固定され、
     rate limit無効な別appを先に生成すると検証にならないため。既存の
     test_login_rate_limit_blocks_after_thresholdと同じ理由。)
    """
    statuses = []
    for i in range(25):
        resp = client_with_ratelimit_only.post(
            "/api/projects", json=build_payload(name=f"p{i}")
        )
        statuses.append(resp.status_code)
    assert 429 in statuses, statuses
    assert 0 < statuses.count(201) <= 20, statuses


def test_failed_host_login_drops_the_creator_session(client, db):
    """誤ったホストコードでのログイン失敗は、作成時に得たHost権限も破棄する。

    ログイン画面を開いた時点で「別のホストとして入り直す」意図があるため、
    失敗しても前の権限が残り続けることがないようにしている。
    """
    created = create_project(client)
    project_id = created["project_id"]
    assert client.get(f"/api/projects/{project_id}/progress").status_code == 200

    resp = client.post("/api/auth/host-login", json={"code": "host_wrong-code"})
    assert resp.status_code == 403

    with client.session_transaction() as sess:
        assert "host_project_id" not in sess
    assert client.get(f"/api/projects/{project_id}/progress").status_code == 403


def test_failed_scorer_login_drops_the_scorer_session(client, app, db):
    created = create_project(client)
    start_scoring(client, created["project_id"])

    scorer = app.test_client()
    login_scorer(scorer, created["scorers"][0]["code"])
    assert scorer.get("/api/scorer/me/evaluations").status_code == 200

    assert scorer.post(
        "/api/auth/scorer-login", json={"code": "scr_wrong-code"}
    ).status_code == 403
    assert scorer.get("/api/scorer/me/evaluations").status_code == 403


def test_login_rate_limit_is_unchanged():
    """既存loginのrate limitを変更していないこと。"""
    from app.routes.api_auth import LOGIN_RATE_LIMIT

    assert LOGIN_RATE_LIMIT == "10 per minute"


# ---------------------------------------------------------------------------
# /projects/<id>/created
# ---------------------------------------------------------------------------


def test_created_page_is_a_real_route(client, db):
    created = create_project(client)
    resp = client.get(f"/projects/{created['project_id']}/created")
    assert resp.status_code == 200


def test_created_page_never_shows_codes(client, db):
    created = create_project(client)
    html = client.get(f"/projects/{created['project_id']}/created").get_data(as_text=True)

    assert created["host_code"] not in html
    for scorer in created["scorers"]:
        assert scorer["code"] not in html


def test_project_create_js_links_to_dashboard_not_login():
    js = (APP_DIR / "static" / "js" / "project_create.js").read_text(encoding="utf-8")
    assert "goToHostDashboardLink" in js
    assert "/host/${result.project_id}`" in js


def test_project_create_template_has_dashboard_cta(client):
    html = client.get("/projects/new").get_data(as_text=True)
    assert 'id="goToHostDashboardLink"' in html
    # 別端末向けにホストコードログインの案内も残っていること
    assert "/host/login" in html


# ---------------------------------------------------------------------------
# Submit後にDashboardへ戻す
# ---------------------------------------------------------------------------


def test_scoring_js_redirects_to_scorer_dashboard_after_submit():
    js = (APP_DIR / "static" / "js" / "scoring.js").read_text(encoding="utf-8")
    assert "SUBMIT_REDIRECT_DELAY_MS" in js
    assert 'window.location.href = "/scorer"' in js
    assert "提出しました" in js


def test_submitted_evaluation_is_still_viewable_readonly(client, app, db):
    created = create_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    scorer = app.test_client()
    login_scorer(scorer, created["scorers"][0]["code"])
    subject = subjects_for(project_id)[0]

    submit_resp = score_and_submit(scorer, project_id, subject.id, feedback="よかった")
    assert submit_resp.status_code == 200

    evaluation_id = submit_resp.get_json()["evaluation_id"]
    detail = scorer.get(f"/api/evaluations/{evaluation_id}").get_json()
    assert detail["status"] == "submitted"
    assert detail["feedback"] == "よかった"
    assert all(c["score"] == 15 for c in detail["criteria"])


def test_submitted_evaluation_is_irreversible(client, app, db):
    created = create_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    scorer = app.test_client()
    login_scorer(scorer, created["scorers"][0]["code"])
    subject = subjects_for(project_id)[0]
    evaluation_id = score_and_submit(scorer, project_id, subject.id).get_json()[
        "evaluation_id"
    ]

    resp = scorer.post(
        f"/api/evaluations/{evaluation_id}/scores",
        json={"scores": {str(c.id): 1 for c in criteria_for(project_id)}},
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# FINISHED後のUX / replay
# ---------------------------------------------------------------------------


def _run_batch_project_to_finished(client, app):
    created = create_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    for scorer_info in created["scorers"]:
        scorer = app.test_client()
        login_scorer(scorer, scorer_info["code"])
        for subject in subjects_for(project_id):
            score_and_submit(scorer, project_id, subject.id)

    for target in ("LOCKED", "PRESENTING", "FINISHED"):
        resp = client.post(
            f"/api/projects/{project_id}/transition", json={"target_status": target}
        )
        assert resp.status_code == 200, target
    return created


def test_finished_project_result_is_replayable_without_state_change(client, app, db):
    """replayに使うAPIはGETのみで、何度呼んでもFINISHEDのままであること。"""
    created = _run_batch_project_to_finished(client, app)
    project_id = created["project_id"]

    bodies = []
    for _ in range(3):
        resp = client.get(f"/api/projects/{project_id}/result-summary")
        assert resp.status_code == 200
        bodies.append(resp.get_json())
        assert db.session.get(Project, project_id).status == "FINISHED"

    assert bodies[0] == bodies[1] == bodies[2]


def test_finished_project_cannot_be_moved_back(client, app, db):
    created = _run_batch_project_to_finished(client, app)
    project_id = created["project_id"]

    for target in ("PRESENTING", "LOCKED", "SCORING", "DRAFT"):
        resp = client.post(
            f"/api/projects/{project_id}/transition", json={"target_status": target}
        )
        assert resp.status_code == 409, target
        assert db.session.get(Project, project_id).status == "FINISHED"


def test_presentation_page_has_finished_navigation(client, app, db):
    created = _run_batch_project_to_finished(client, app)
    html = client.get(f"/host/{created['project_id']}/present").get_data(as_text=True)

    for element_id in ("finishedPanel", "replayButton", "showRankingButton",
                       "backToDashboardLink"):
        assert f'id="{element_id}"' in html, element_id


def test_replay_path_in_js_performs_no_state_transition():
    """FINISHED向けの導線がtransition APIを叩かないこと(静的検査)。"""
    js = (APP_DIR / "static" / "js" / "result_presentation.js").read_text(encoding="utf-8")

    replay = re.search(
        r'getElementById\("replayButton"\)\.addEventListener\('
        r'"click",\s*async \(\) => \{(.*?)\n  \}\);',
        js,
        re.DOTALL,
    )
    assert replay, "replayButtonのハンドラが見つからない"
    assert "transition" not in replay.group(1)
    assert "apiFetch" not in replay.group(1)

    ranking = re.search(
        r'getElementById\("showRankingButton"\)\.addEventListener\('
        r'"click",\s*\(\) => \{(.*?)\n  \}\);',
        js,
        re.DOTALL,
    )
    assert ranking, "showRankingButtonのハンドラが見つからない"
    assert "transition" not in ranking.group(1)
    assert "apiFetch" not in ranking.group(1)


def test_finish_button_is_only_shown_while_presenting():
    """FINISHEDでは発表終了ボタンが出ない=誤ってtransitionを叩けない。"""
    js = (APP_DIR / "static" / "js" / "result_presentation.js").read_text(encoding="utf-8")
    assert 'summary.project.status !== "PRESENTING"' in js


def test_standby_panel_is_hidden_by_default(client, app, db):
    """FINISHED再訪問時に「結果発表を開始する」が見えないこと。"""
    created = _run_batch_project_to_finished(client, app)
    html = client.get(f"/host/{created['project_id']}/present").get_data(as_text=True)
    assert re.search(r'id="standbyPanel"[^>]*class="[^"]*hidden', html)
