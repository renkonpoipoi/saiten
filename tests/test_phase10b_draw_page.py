"""Phase 10B-6: プロジェクター用の抽選ページ。

このページの責務は「サーバーが決めた1件を、映えるように見せる」ことだけ。
**結果を決めるのはサーバー**であり、client の乱数は切替中の表示しか決めない。
ここではその不変条件を、実挙動と実装ソースの両面から固定する。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.models import Project
from tests.helpers import create_project, start_scoring

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DRAW_JS = (APP_DIR / "static" / "js" / "draw.js").read_text(encoding="utf-8")
DRAW_HTML = (APP_DIR / "templates" / "draw.html").read_text(encoding="utf-8")
REVEAL_CSS = (APP_DIR / "static" / "css" / "reveal.css").read_text(encoding="utf-8")

FOUR = {"subjects": ["A", "B", "C", "D"], "scorers": ["s1", "s2"]}


def _code_only(source: str) -> str:
    """コメントを除いた JS 本体。説明文の語をコードの証拠と取り違えないため。"""
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", without_block, flags=re.M)


def _js_function(name: str) -> str:
    """draw.js から1関数の本文を切り出す(インデント2で閉じる規約に依存)。"""
    start = DRAW_JS.index(f"function {name}(")
    return DRAW_JS[start : DRAW_JS.index("\n  }", start)]


def _random_draw_project(client, **overrides):
    payload = dict(FOUR)
    payload.update(overrides)
    created = create_project(client, **payload)
    resp = client.patch(
        f"/api/projects/{created['project_id']}/subject-order-mode",
        json={"subject_order_mode": "RANDOM_DRAW"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return created


# ---------------------------------------------------------------------------
# ルート
# ---------------------------------------------------------------------------


def test_draw_page_is_served(client, db):
    created = _random_draw_project(client)
    resp = client.get(f"/host/{created['project_id']}/draw")
    assert resp.status_code == 200


def test_draw_page_renders_the_stage_scaffolding(client, db):
    created = _random_draw_project(client)
    html = client.get(f"/host/{created['project_id']}/draw").get_data(as_text=True)
    for element_id in [
        "drawStage",
        "drawCard",
        "drawName",
        "drawHistory",
        "drawButton",
        "drawUnavailablePanel",
        "skipButton",
        "fullscreenButton",
    ]:
        assert f'id="{element_id}"' in html, element_id


def test_draw_page_loads_the_presentation_core_and_draw_script(client, db):
    created = _random_draw_project(client)
    html = client.get(f"/host/{created['project_id']}/draw").get_data(as_text=True)
    assert "js/presentation/core.js" in html
    assert "js/draw.js" in html
    assert "css/reveal.css" in html


def test_draw_page_html_carries_no_project_data(client, db):
    """サーバーレンダリング時点では project_id 以外を一切埋め込まない。"""
    created = _random_draw_project(client)
    html = client.get(f"/host/{created['project_id']}/draw").get_data(as_text=True)
    for name in FOUR["subjects"]:
        assert f">{name}<" not in html
    assert "draw_order" not in html
    assert f"window.PROJECT_ID = {created['project_id']}" in html


def test_opening_the_draw_page_changes_no_state(client, db):
    """GET で抽選を進めない。ページを開くだけでは何も起きない。"""
    created = _random_draw_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    before = db.session.get(Project, project_id)
    status, cursor = before.status, before.draw_cursor

    for _ in range(3):
        assert client.get(f"/host/{project_id}/draw").status_code == 200

    db.session.expire_all()
    after = db.session.get(Project, project_id)
    assert (after.status, after.draw_cursor) == (status, cursor)


def test_draw_page_is_open_but_the_draw_api_is_not(client, app, db):
    """ページ route は他の画面と同じく無 guard。認可の実体は API 側。"""
    created = _random_draw_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    with app.test_client() as anonymous:
        assert anonymous.get(f"/host/{project_id}/draw").status_code == 200
        resp = anonymous.post(
            f"/api/projects/{project_id}/draw-next-subject",
            json={"expected_cursor": 0},
        )
        assert resp.status_code == 403
        assert "A" not in resp.get_data(as_text=True)

    db.session.expire_all()
    assert db.session.get(Project, project_id).draw_cursor == 0


def test_draw_page_falls_back_to_the_dashboard_with_a_plain_get_link(client, db):
    """戻る導線は state を変えない素の GET リンクにする。"""
    created = _random_draw_project(client)
    html = client.get(f"/host/{created['project_id']}/draw").get_data(as_text=True)
    assert 'id="drawDashboardLink"' in html
    assert 'id="unavailableDashboardLink"' in html
    assert "<form" not in html
    assert f'/host/${{projectId}}`' in DRAW_JS or "dashboardHref" in DRAW_JS
    assert 'const dashboardHref = `/host/${projectId}`;' in DRAW_JS


# ---------------------------------------------------------------------------
# 結果を決めるのはサーバー
# ---------------------------------------------------------------------------


def test_draw_js_sends_the_cursor_the_server_reported(client, db):
    """次に送る expected_cursor は常にサーバーが返した draw_cursor。"""
    body = _js_function("drawNext")
    assert "expected_cursor: draw.draw_cursor" in body
    # client 側で件数を数え直さない
    assert "drawn.length + 1" not in DRAW_JS
    assert "cursor + 1" not in DRAW_JS


def test_draw_js_lands_on_the_server_result(client, db):
    body = _js_function("drawNext")
    assert "const resultName = result.subject.name;" in body
    assert "finalState: () => showResult(resultName)" in body
    assert "showResult(resultName)" in _js_function("applyDrawStep")


def test_client_randomness_only_drives_the_spinning_display(client, db):
    """★ Math.random() は切替表示にしか使わない。着地は決めさせない。"""
    spin = _js_function("startSpin")
    assert "Math.random()" in spin
    assert _code_only(DRAW_JS).count("Math.random()") == spin.count("Math.random()")
    # 演出側で結果を選ぶ経路が無いこと
    assert "showResult(candidates" not in DRAW_JS
    assert "resultName" not in spin


def test_spin_candidates_exclude_already_drawn_subjects(client, db):
    """★ 抽選済みを次回のルーレット候補として再表示しない。"""
    body = _js_function("drawNext")
    assert "core.remainingCandidates(subjectNames(progress), drawnNames(progress))" in body
    assert "drawnNames" in DRAW_JS


def test_spin_candidates_are_taken_before_the_draw_request(client, db):
    """今回の結果は候補集合に含まれている必要がある(抽選前の残り = 候補)。"""
    body = _js_function("drawNext")
    assert body.index("const candidates =") < body.index("draw-next-subject")


def test_draw_js_never_references_the_secret_order(client, db):
    for forbidden in ["draw_order", "next_subject", "future_order", "secret_order"]:
        assert forbidden not in DRAW_JS, forbidden


def test_draw_page_reads_progress_from_the_public_endpoint(client, db):
    assert "/progress`" in DRAW_JS
    assert "presentation-state" not in DRAW_JS


# ---------------------------------------------------------------------------
# 1クリック = 1回の抽選
# ---------------------------------------------------------------------------


def test_one_click_draws_exactly_once(client, db):
    """自動連鎖しない。drawNext を呼ぶのはクリックハンドラだけ。"""
    call_sites = re.findall(r"(?<!function )\bdrawNext\(\)", _code_only(DRAW_JS))
    assert len(call_sites) == 1
    assert "drawButton.addEventListener" in DRAW_JS


def test_the_draw_request_is_guarded_against_double_clicks(client, db):
    body = _js_function("drawNext")
    assert body.index("if (busy") < body.index("draw-next-subject")
    assert "setBusy(true)" in body
    assert "drawButton.disabled = value" in _js_function("setBusy")


def test_skip_only_lands_the_current_animation(client, db):
    """★ Skip は演出を最終状態へ進めるだけ。次を引かない。"""
    body = _js_function("skipCurrentSequence")
    assert "apiFetch" not in body
    assert "drawNext" not in body
    assert "sequence.finalState()" in body
    assert "stopSpin()" in body


def test_replayed_draws_are_reported_as_such(client, db):
    body = _js_function("drawNext")
    assert "result.replayed" in body


# ---------------------------------------------------------------------------
# Phase 9 資産への影響
# ---------------------------------------------------------------------------


def test_draw_styles_reuse_the_phase9_tokens(client, db):
    block = REVEAL_CSS[REVEAL_CSS.index(".p-draw {") :]
    assert "var(--p-gold)" in block
    assert "var(--p-panel)" in block
    assert "var(--u)" in block


def test_phase9_core_exports_are_untouched(client, db):
    core = (APP_DIR / "static" / "js" / "presentation" / "core.js").read_text(encoding="utf-8")
    for name in [
        "buildSubjectSteps:",
        "buildSequentialRankSteps:",
        "buildBatchFinalSteps:",
        "railLayout:",
        "timingCssVars:",
    ]:
        assert name in core, name
    # 10B で足したのは純粋関数の追加だけ
    for name in ["buildDrawSteps:", "drawSpinIntervals:", "remainingCandidates:"]:
        assert name in core, name


def test_result_presentation_does_not_import_draw_logic(client, db):
    js = (APP_DIR / "static" / "js" / "result_presentation.js").read_text(encoding="utf-8")
    assert "buildDrawSteps" not in js
    assert "draw-next-subject" not in js
