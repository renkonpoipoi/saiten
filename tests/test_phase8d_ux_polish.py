"""Phase 8D: Production Smoke Testで見つかったUXの袋小路・不足の修正。

対象は4点のみ。
  1. Final Ranking画面のナビゲーション(BATCH / SEQUENTIAL共通)
  2. SEQUENTIAL採点待ち画面からHost Dashboardへの導線
  3. Project作成直後の参加者コード一括コピー
  4. DRAFT中のScorer Dashboard表示

Presentationの演出そのもの(reveal engine)はPhase 9で扱うため、ここでは
一切検証・変更しない。
"""

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
JS_DIR = APP_DIR / "static" / "js"

THREE_SUBJECTS = {
    "subjects": ["チームA", "チームB", "チームC"],
    "scorers": ["採点者1", "採点者2"],
}


def _presentation_js() -> str:
    return (JS_DIR / "result_presentation.js").read_text(encoding="utf-8")


def _sequential_project(client, **overrides):
    payload = dict(THREE_SUBJECTS, presentation_mode="SEQUENTIAL")
    payload.update(overrides)
    return create_project(client, **payload)


def _submit_all_for(app, created, project_id, subject_id):
    for scorer_info in created["scorers"]:
        scorer = app.test_client()
        login_scorer(scorer, scorer_info["code"])
        score_and_submit(scorer, project_id, subject_id)


def _run_batch_to(client, app, final_status="FINISHED"):
    """BATCHプロジェクトを指定statusまで進めて作成レスポンスを返す。"""
    created = create_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    for scorer_info in created["scorers"]:
        scorer = app.test_client()
        login_scorer(scorer, scorer_info["code"])
        for subject in subjects_for(project_id):
            score_and_submit(scorer, project_id, subject.id)

    targets = ["LOCKED", "PRESENTING", "FINISHED"]
    for target in targets[: targets.index(final_status) + 1]:
        resp = client.post(
            f"/api/projects/{project_id}/transition", json={"target_status": target}
        )
        assert resp.status_code == 200, target
    return created


def _run_sequential_to_final(client, app, final_status="FINISHED"):
    """SEQUENTIALプロジェクトを全Subject発表済み→指定statusまで進める。"""
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    for subject in subjects_for(project_id):
        _submit_all_for(app, created, project_id, subject.id)
        assert (
            client.post(
                f"/api/projects/{project_id}/subjects/{subject.id}/lock"
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/projects/{project_id}/subjects/{subject.id}/present"
            ).status_code
            == 200
        )

    targets = ["LOCKED", "PRESENTING", "FINISHED"]
    for target in targets[: targets.index(final_status) + 1]:
        resp = client.post(
            f"/api/projects/{project_id}/transition", json={"target_status": target}
        )
        assert resp.status_code == 200, target
    return created


# ---------------------------------------------------------------------------
# 1. Final Ranking のナビゲーション
# ---------------------------------------------------------------------------


def test_batch_final_ranking_has_dashboard_and_analysis_links(client, app, db):
    created = _run_batch_to(client, app)
    project_id = created["project_id"]
    html = client.get(f"/host/{project_id}/present").get_data(as_text=True)

    assert 'id="rankingActions"' in html
    assert 'id="backToDashboardLink"' in html
    assert 'id="finishedAnalysisLink"' in html

    js = _presentation_js()
    assert "backToDashboardLink" in js and f"/host/${{projectId}}`" in js
    assert f"/host/${{projectId}}/analysis`" in js


def test_sequential_final_ranking_shares_the_same_navigation(client, app, db):
    """SEQUENTIALも同じrankingActionsを使う(専用の複製を作らない)。"""
    created = _run_sequential_to_final(client, app)
    html = client.get(f"/host/{created['project_id']}/present").get_data(as_text=True)

    assert html.count('id="rankingActions"') == 1
    assert html.count('id="backToDashboardLink"') == 1
    assert html.count('id="finishedAnalysisLink"') == 1

    js = _presentation_js()
    # SEQUENTIALの最終ランキングもBATCHと同じ showRanking() を経由する
    assert "async function showFinalRanking()" in js
    assert re.search(r"async function showFinalRanking\(\)[^}]*showRanking\(", js, re.S)


def test_ranking_actions_live_inside_the_ranking_panel(client, app, db):
    """導線がランキングと同じパネル内にあり、ランキング表示時に必ず一緒に出ること。"""
    created = _run_batch_to(client, app)
    html = client.get(f"/host/{created['project_id']}/present").get_data(as_text=True)

    panel = re.search(
        r'<section id="rankingPanel".*?</section>', html, re.S
    )
    assert panel, "rankingPanelが見つからない"
    body = panel.group(0)
    for element_id in ("rankingActions", "finishButton", "replayButton",
                       "backToDashboardLink", "finishedAnalysisLink"):
        assert f'id="{element_id}"' in body, element_id


def test_ranking_actions_are_never_left_hidden(client, app, db):
    """showRanking()の結果、action areaがhiddenのままにならないこと。"""
    created = _run_batch_to(client, app)
    html = client.get(f"/host/{created['project_id']}/present").get_data(as_text=True)

    # 初期HTMLでrankingActions自体にhiddenが付いていない
    action_tag = re.search(r'<div id="rankingActions"[^>]*>', html)
    assert action_tag, "rankingActionsが見つからない"
    assert "hidden" not in action_tag.group(0)

    js = _presentation_js()
    # showRanking()はrenderRankingActions()を必ず呼ぶ
    show = re.search(r"function showRanking\(summary\) \{(.*?)\n  \}\n", js, re.S)
    assert show, "showRankingが見つからない"
    assert "renderRankingActions(summary)" in show.group(1)

    # renderRankingActions()はaction areaのhiddenを無条件に外す
    render = re.search(
        r"function renderRankingActions\(summary\) \{(.*?)\n  \}\n", js, re.S
    )
    assert render, "renderRankingActionsが見つからない"
    assert 'rankingActions.classList.remove("hidden")' in render.group(1)


def test_finish_action_is_kept_while_presenting(client, app, db):
    created = _run_batch_to(client, app, final_status="PRESENTING")
    assert db.session.get(Project, created["project_id"]).status == "PRESENTING"

    js = _presentation_js()
    # PRESENTING以外では隠す = PRESENTING中は出る
    assert 'summary.project.status !== "PRESENTING"' in js


def test_replay_action_is_offered_when_finished(client, app, db):
    created = _run_batch_to(client, app)
    assert db.session.get(Project, created["project_id"]).status == "FINISHED"

    html = client.get(f"/host/{created['project_id']}/present").get_data(as_text=True)
    assert 'id="replayButton"' in html

    js = _presentation_js()
    assert 'summary.project.status !== "FINISHED"' in js


def test_replay_does_not_change_project_status(client, app, db):
    """replayに使うAPIはGETのみ。何度取得してもFINISHEDのまま。"""
    created = _run_batch_to(client, app)
    project_id = created["project_id"]

    for _ in range(3):
        assert client.get(f"/api/projects/{project_id}/result-summary").status_code == 200
        assert db.session.get(Project, project_id).status == "FINISHED"


def test_ranking_navigation_targets_are_real_routes(client, app, db):
    created = _run_batch_to(client, app)
    project_id = created["project_id"]

    assert client.get(f"/host/{project_id}").status_code == 200
    assert client.get(f"/host/{project_id}/analysis").status_code == 200


def test_presentation_dom_ids_match_js_after_phase8d(client, app, db):
    """新しく増やしたgetElementByIdの参照先が全てDOMに存在すること。"""
    created = _sequential_project(client)
    html = client.get(f"/host/{created['project_id']}/present").get_data(as_text=True)
    js = _presentation_js()
    for element_id in re.findall(r'getElementById\("([^"]+)"\)', js):
        assert f'id="{element_id}"' in html, element_id


# ---------------------------------------------------------------------------
# 2. SEQUENTIAL 採点待ち画面 → Host Dashboard
# ---------------------------------------------------------------------------


def test_sequential_waiting_panel_has_host_dashboard_link(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    html = client.get(f"/host/{project_id}/present").get_data(as_text=True)

    panel = re.search(
        r'<section id="sequentialWaitingPanel".*?</section>', html, re.S
    )
    assert panel, "sequentialWaitingPanelが見つからない"
    assert 'id="sequentialDashboardLink"' in panel.group(0)


def test_sequential_dashboard_link_opens_in_a_new_tab(client, app, db):
    created = _sequential_project(client)
    html = client.get(f"/host/{created['project_id']}/present").get_data(as_text=True)

    tag = re.search(r'<a[^>]*id="sequentialDashboardLink"[^>]*>', html)
    assert tag, "sequentialDashboardLinkが見つからない"
    assert 'target="_blank"' in tag.group(0)
    assert 'rel="noopener"' in tag.group(0)


def test_sequential_dashboard_link_points_at_host_route(client, app, db):
    js = _presentation_js()
    assert re.search(
        r'getElementById\("sequentialDashboardLink"\)\.href = `/host/\$\{projectId\}`',
        js,
    )

    created = _sequential_project(client)
    assert client.get(f"/host/{created['project_id']}").status_code == 200


def test_sequential_waiting_state_is_reachable_after_confirm_next(client, app, db):
    """Subject Aを確定した後、Subject Bが採点待ちになる(=リンクが要る画面)。"""
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    _submit_all_for(app, created, project_id, subjects[0].id)
    client.post(f"/api/projects/{project_id}/subjects/{subjects[0].id}/lock")
    client.post(f"/api/projects/{project_id}/subjects/{subjects[0].id}/present")

    state = client.get(f"/api/projects/{project_id}/presentation-state").get_json()
    assert state["project"]["status"] == "SCORING"
    assert state["presentable_subject_id"] is None
    assert state["all_subjects_presented"] is False
    assert state["current_subject_id"] == subjects[1].id


def test_batch_presentation_has_no_sequential_panels_shown(client, app, db):
    """BATCHの発表画面を不必要に変えていないこと(SEQUENTIAL用パネルはhiddenのまま)。"""
    created = _run_batch_to(client, app)
    html = client.get(f"/host/{created['project_id']}/present").get_data(as_text=True)

    for element_id in ("sequentialWaitingPanel", "sequentialStandbyPanel",
                       "confirmNextPanel", "finalRankingPanel"):
        tag = re.search(rf'<section id="{element_id}"[^>]*>', html)
        assert tag, element_id
        assert "hidden" in tag.group(0), element_id


# ---------------------------------------------------------------------------
# 3. 参加者コードの一括コピー
# ---------------------------------------------------------------------------


def test_create_page_has_bulk_copy_button(client, db):
    html = client.get("/projects/new").get_data(as_text=True)
    assert 'id="copyAllScorerCodesButton"' in html
    assert "参加者用の採点者コードを一括コピー" in html
    # 除外対象はホスト兼任の有無で変わるため、説明文はJSが差し込む
    assert 'id="bulkCopyNote"' in html


def test_bulk_copy_text_contains_every_participant_scorer():
    """Phase 10A: 参加者へ配布する採点者だけを1行ずつ並べる。

    Phase 8Dでは全Scorerを対象にしていたが、Phase 10Aでホスト兼任の採点者を
    除外するようになった(本人が使うコードであり配布対象ではないため)。
    """
    js = (JS_DIR / "project_create.js").read_text(encoding="utf-8")
    build = re.search(
        r"function buildScorerCodeText\(scorers\) \{(.*?)\n  \}", js, re.S
    )
    assert build, "buildScorerCodeTextが見つからない"
    body = build.group(1)
    # ホスト兼任だけを除き、残りを display_name: code の1行ずつに変換する
    assert "!scorer.is_host_scorer" in body
    assert ".map(" in body
    assert "scorer.display_name" in body
    assert "scorer.code" in body
    assert '.join("\\n")' in body


def test_bulk_copy_never_includes_the_host_code():
    js = (JS_DIR / "project_create.js").read_text(encoding="utf-8")
    build = re.search(
        r"function buildScorerCodeText\(scorers\) \{(.*?)\n  \}", js, re.S
    )
    assert build
    assert "host_code" not in build.group(1)

    # 一括コピーのハンドラもhost codeに触れない
    handler = re.search(
        r'getElementById\("copyAllScorerCodesButton"\)\s*\.addEventListener\('
        r'"click", \(\) => \{(.*?)\n      \}\);',
        js,
        re.S,
    )
    assert handler, "一括コピーのハンドラが見つからない"
    assert "host_code" not in handler.group(1)
    assert "hostCodeText" not in handler.group(1)


def test_bulk_copy_does_not_send_codes_back_to_the_server():
    """既存のcreate responseをclient側で使うだけで、APIを叩き直さないこと。"""
    js = (JS_DIR / "project_create.js").read_text(encoding="utf-8")
    handler = re.search(
        r'getElementById\("copyAllScorerCodesButton"\)\s*\.addEventListener\('
        r'"click", \(\) => \{(.*?)\n      \}\);',
        js,
        re.S,
    )
    assert handler
    body = handler.group(1)
    assert "apiFetch" not in body
    assert "fetch(" not in body

    # showCreated全体でも、作成API以外の通信を増やしていない
    assert js.count("apiFetch(") == 1
    assert '"/api/projects"' in js


def test_individual_copy_buttons_are_kept():
    js = (JS_DIR / "project_create.js").read_text(encoding="utf-8")
    # 各Scorer行のコピーボタン
    assert 'copyButton.textContent = "コピー"' in js
    assert "copyToClipboard(scorer.code)" in js

    html = (APP_DIR / "templates" / "project_create.html").read_text(encoding="utf-8")
    # Host Code個別コピー
    assert 'data-copy-target="hostCodeText"' in html


def test_bulk_copy_survives_clipboard_failure():
    """Clipboardが失敗しても致命的にならず、メッセージに落とすこと。"""
    js = (JS_DIR / "project_create.js").read_text(encoding="utf-8")
    handler = re.search(
        r'getElementById\("copyAllScorerCodesButton"\)\s*\.addEventListener\('
        r'"click", \(\) => \{(.*?)\n      \}\);',
        js,
        re.S,
    )
    assert handler
    body = handler.group(1)
    assert "try {" in body          # 同期例外(execCommandのfallback)
    assert ".catch(" in body        # Promise rejection(clipboard API)
    assert "BULK_COPY_FAILED_MESSAGE" in body
    # 新規の外部ライブラリを持ち込んでいない
    assert "copyToClipboard(" in body


def test_bulk_copy_shows_success_feedback():
    js = (JS_DIR / "project_create.js").read_text(encoding="utf-8")
    assert "参加者コードをコピーしました" in js


def test_created_page_after_reload_still_never_shows_or_fetches_codes(client, db):
    """reload後の /projects/<id>/created はコードを再取得・再表示しない。"""
    created = create_project(client)
    project_id = created["project_id"]
    html = client.get(f"/projects/{project_id}/created").get_data(as_text=True)

    assert created["host_code"] not in html
    for scorer in created["scorers"]:
        assert scorer["code"] not in html
    assert 'id="copyAllScorerCodesButton"' not in html

    template = (APP_DIR / "templates" / "project_created.html").read_text(
        encoding="utf-8"
    )
    # コード再取得のためのJSを一切読み込んでいない
    assert "extra_js" not in template
    assert "apiFetch" not in template
    assert "fetch(" not in template


def test_scorer_codes_are_still_stored_as_hashes_only(client, db):
    """一括コピー追加後も平文コードをサーバーに保存していないこと。"""
    from app.models import Scorer
    from app.services.code_service import hash_code

    created = create_project(client)
    scorers = Scorer.query.filter_by(project_id=created["project_id"]).all()
    stored = {s.access_code_hash for s in scorers}

    for scorer_info in created["scorers"]:
        assert scorer_info["code"] not in stored
        assert hash_code(scorer_info["code"]) in stored


# ---------------------------------------------------------------------------
# 4. DRAFT中のScorer Dashboard
# ---------------------------------------------------------------------------


def test_scorer_can_still_log_in_while_draft(client, app, db):
    """既存挙動(DRAFTでもscorer login可)を壊していないこと。"""
    created = create_project(client)
    assert db.session.get(Project, created["project_id"]).status == "DRAFT"

    scorer = app.test_client()
    login_scorer(scorer, created["scorers"][0]["code"])
    resp = scorer.get("/api/scorer/me/evaluations")
    assert resp.status_code == 200


def test_scorer_dashboard_reports_draft_status(client, app, db):
    created = create_project(client)
    scorer = app.test_client()
    login_scorer(scorer, created["scorers"][0]["code"])

    body = scorer.get("/api/scorer/me/evaluations").get_json()
    assert body["project_status"] == "DRAFT"
    # 既存keyは維持され、意味も変わっていない
    assert body["subjects"] == []
    assert body["submitted_count"] == 0
    assert body["total_count"] == 0


def test_scorer_dashboard_template_has_not_started_state(client, db):
    html = client.get("/scorer").get_data(as_text=True)
    assert 'id="notStartedPanel"' in html
    assert "まだ採点は開始されていません" in html
    assert "ホストが採点を開始すると、ここに被採点者が表示されます。" in html
    assert 'id="scoringPanel"' in html


def test_scorer_dashboard_js_hides_the_empty_list_while_draft():
    js = (JS_DIR / "scorer_dashboard.js").read_text(encoding="utf-8")
    assert 'data.project_status === "DRAFT"' in js
    render = re.search(r"function render\(data\) \{(.*?)\n  \}\n", js, re.S)
    assert render, "renderが見つからない"
    body = render.group(1)
    assert 'notStartedPanel' in body
    assert 'scoringPanel' in body
    # DRAFTでは 0/0 の一覧描画まで進まない
    assert "if (notStarted) return;" in body


def test_scorer_dashboard_adds_no_polling():
    """過剰なpolling/リアルタイム機構を持ち込んでいないこと。"""
    js = (JS_DIR / "scorer_dashboard.js").read_text(encoding="utf-8")
    assert "setInterval" not in js
    assert "setTimeout" not in js
    assert "EventSource" not in js
    assert "WebSocket" not in js
    # 手動の再読み込みだけを用意している
    assert "reloadDashboardButton" in js
    assert "load();" in js


def test_scorer_dashboard_after_scoring_starts_is_unchanged(client, app, db):
    """SCORING開始後は従来どおりSubject一覧を返す(BATCH無回帰)。"""
    created = create_project(client)
    project_id = created["project_id"]
    scorer = app.test_client()
    login_scorer(scorer, created["scorers"][0]["code"])

    start_scoring(client, project_id)
    body = scorer.get("/api/scorer/me/evaluations").get_json()

    assert body["project_status"] == "SCORING"
    assert body["total_count"] == 2
    assert [row["subject_name"] for row in body["subjects"]] == ["チームA", "チームB"]
    assert all(row["state"] == "not_started" for row in body["subjects"])
    assert all(row["subject_status"] == "SCORING" for row in body["subjects"])


def test_sequential_waiting_labels_are_not_regressed(client, app, db):
    """SEQUENTIALの「待機中」表示(subject_status)が従来どおりであること。"""
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    scorer = app.test_client()
    login_scorer(scorer, created["scorers"][0]["code"])
    body = scorer.get("/api/scorer/me/evaluations").get_json()

    assert body["project_status"] == "SCORING"
    assert [r["subject_status"] for r in body["subjects"]] == [
        "SCORING",
        "WAITING",
        "WAITING",
    ]
    assert [r["scorable"] for r in body["subjects"]] == [True, False, False]

    js = (JS_DIR / "scorer_dashboard.js").read_text(encoding="utf-8")
    assert "待機中" in js
    assert "subject_status" in js


def test_scorer_dashboard_project_status_across_lifecycle(client, app, db):
    """LOCKED / PRESENTING / FINISHED でも一覧表示のまま(未開始扱いにしない)。"""
    created = create_project(client)
    project_id = created["project_id"]
    scorer_client = app.test_client()
    login_scorer(scorer_client, created["scorers"][0]["code"])

    start_scoring(client, project_id)
    for scorer_info in created["scorers"]:
        c = app.test_client()
        login_scorer(c, scorer_info["code"])
        for subject in subjects_for(project_id):
            score_and_submit(c, project_id, subject.id)

    for target in ("LOCKED", "PRESENTING", "FINISHED"):
        client.post(
            f"/api/projects/{project_id}/transition", json={"target_status": target}
        )
        body = scorer_client.get("/api/scorer/me/evaluations").get_json()
        assert body["project_status"] == target
        assert body["total_count"] == 2, target
        assert len(body["subjects"]) == 2, target


# ---------------------------------------------------------------------------
# Phase 8Dのスコープ外(Phase 9)に手を出していないこと
# ---------------------------------------------------------------------------


def test_reveal_engine_was_rebuilt_in_phase9():
    """Phase 8Dのスコープ境界ガードをPhase 9の仕様へ差し替えたもの。

    Phase 8Dでは「演出を変更していないこと」を証明するために running total と
    暫定カード配置の存在を検証していた。Phase 9ではその running total 廃止が
    P0仕様なので、逆に「無いこと」を検証する。
    Final RankingにRadar / 記述回答を統合しない方針は引き続き維持する。
    """
    js = _presentation_js()
    assert "runningTotal" not in js, "running total は Phase 9 で完全廃止した"
    assert "judge-score-bubble" not in js, "旧カード配置は撤去した"
    assert js.count("async function revealSubjectSequence(") == 1
    assert "radar" not in js.lower()
    assert "feedback" not in js.lower()


def test_no_new_migrations_were_added():
    versions = Path(__file__).resolve().parent.parent / "migrations" / "versions"
    revisions = sorted(p.name for p in versions.glob("*.py"))
    assert revisions == [
        "9c4e17a2b8d3_add_presentation_modes.py",
        "b37d61517847_initial_schema.py",
    ], revisions
