"""Phase 9: Presentation の DOM 契約と、設計上の絶対条件の静的検査。

ここで守るのは主に4つ。
  1. running total を復活させない(Judge全員のあとにだけ TOTAL が出る)
  2. 演出 runner の内部からサーバーへ POST しない
     (animation の完了だけで lifecycle を進めないため)
  3. 1クリック = 1つの意味のある演出sequence(BATCHは被採点者単位で停止する)
  4. Presentation に Radar / 記述回答を持ち込まない(Analysis へ分離したまま)

演出の順序・尺・分岐そのものは純粋関数として tests/js/core.test.mjs で
検証している(実時間を待たない)。ここはその外側の契約だけを見る。
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.helpers import create_project, start_scoring

APP_DIR = Path(__file__).resolve().parent.parent / "app"
JS_DIR = APP_DIR / "static" / "js"
PRESENTATION_JS_DIR = JS_DIR / "presentation"


def _presentation_js() -> str:
    return (JS_DIR / "result_presentation.js").read_text(encoding="utf-8")


def _reveal_css() -> str:
    return (APP_DIR / "static" / "css" / "reveal.css").read_text(encoding="utf-8")


def _function_body(js: str, signature: str) -> str:
    """波括弧の対応を数えて関数本体を切り出す(正規表現より壊れにくい)。"""
    start = js.index(signature)
    open_brace = js.index("{", start)
    depth = 0
    for index in range(open_brace, len(js)):
        if js[index] == "{":
            depth += 1
        elif js[index] == "}":
            depth -= 1
            if depth == 0:
                return js[open_brace + 1:index]
    raise AssertionError(f"unbalanced braces after {signature!r}")


def _present_html(client) -> str:
    created = create_project(client)
    return client.get(f"/host/{created['project_id']}/present").get_data(as_text=True)


# ---------------------------------------------------------------------------
# DOM 契約
# ---------------------------------------------------------------------------


def test_presentation_page_has_the_phase9_stage(client, db):
    html = _present_html(client)
    for element_id in (
        "presentationRoot",
        "revealStage",
        "revealEyebrow",
        "revealSubjectName",
        "revealJudgeRow",
        "activeJudgeCard",
        "activeJudgeName",
        "activeJudgeScore",
        "revealTotal",
        "revealTotalValue",
        "stageControls",
        "fullscreenButton",
        "skipButton",
    ):
        assert f'id="{element_id}"' in html, element_id


def test_active_judge_card_is_a_separate_layer_from_the_rail(client, db):
    """下部railのslotをDOM上で動かさず、中央は別要素で表現していること。"""
    html = _present_html(client)
    rail = re.search(r'<footer class="p-rail" id="revealJudgeRow"[^>]*>(.*?)</footer>', html, re.S)
    assert rail, "judge railが見つからない"
    # railの中身はJSが組み立てるので、テンプレート側にactive cardが混ざっていないこと
    assert "activeJudgeCard" not in rail.group(1)
    assert 'id="activeJudgeCard"' in html


def test_batch_progress_panels_exist(client, db):
    html = _present_html(client)
    for element_id in (
        "batchSubjectPanel",
        "batchSubjectName",
        "batchRevealButton",
        "batchAdvancePanel",
        "batchAdvanceButton",
        "batchAdvanceNote",
    ):
        assert f'id="{element_id}"' in html, element_id


def test_all_presentation_dom_ids_referenced_by_js_exist(client, db):
    """result_presentation.js / presentation/*.js が参照するidが全て存在すること。"""
    html = _present_html(client)
    sources = [_presentation_js()] + [
        path.read_text(encoding="utf-8") for path in sorted(PRESENTATION_JS_DIR.glob("*.js"))
    ]
    for source in sources:
        for element_id in re.findall(r'getElementById\("([^"]+)"\)', source):
            assert f'id="{element_id}"' in html, element_id


def test_presentation_scripts_are_loaded_in_dependency_order(client, db):
    html = _present_html(client)
    order = [
        "assets/sfx/manifest.js",
        "js/presentation/core.js",
        "js/presentation/audio.js",
        "js/presentation/stage.js",
        "js/result_presentation.js",
    ]
    positions = [html.index(path) for path in order]
    assert positions == sorted(positions), "依存順にscriptを読み込んでいない"


def test_presentation_page_is_full_bleed_without_touching_other_pages(client, db):
    """発表画面だけ app-shell の 960px 制約を外していること。"""
    html = _present_html(client)
    assert 'class="presentation-body"' in html

    css = _reveal_css()
    assert ".presentation-body .app-shell" in css
    assert "max-width: none" in css

    # 共通CSSは無変更(他ページのresponsiveを壊さない)
    common = (APP_DIR / "static" / "css" / "common.css").read_text(encoding="utf-8")
    assert "max-width: 960px" in common
    assert "presentation" not in common

    # 他ページには body class が付かない
    other = client.get("/host/login").get_data(as_text=True)
    assert 'class="presentation-body"' not in other


# ---------------------------------------------------------------------------
# P0: running total の完全廃止
# ---------------------------------------------------------------------------


def test_running_total_is_gone(client, db):
    js = _presentation_js()
    assert "runningTotal" not in js
    assert "judge-score-bubble" not in js
    assert "judge-score-bubble" not in _reveal_css()


def test_total_is_revealed_only_after_every_judge(client, db):
    """TOTAL の描画は ALL_SETTLED より後の phase からしか呼ばれないこと。"""
    js = _presentation_js()
    body = _function_body(js, "function applySubjectStep(")
    assert body.index('case "ALL_SETTLED"') < body.index('case "TOTAL_ENTER"')
    # TOTAL を出すのは TOTAL_ENTER の1箇所だけ
    assert body.count("stage.revealTotal(") == 1

    stage_js = (PRESENTATION_JS_DIR / "stage.js").read_text(encoding="utf-8")
    settle = _function_body(stage_js, "function settleJudge(")
    assert "revealTotal" not in settle, "Judge開示のたびに合計を出してはいけない"
    assert "totalValue" not in settle


# ---------------------------------------------------------------------------
# animation と server lifecycle の分離
# ---------------------------------------------------------------------------


def test_the_animation_runner_never_talks_to_the_server(client, db):
    """演出 runner の内部から POST しない(完了だけで status を進めない)。"""
    js = _presentation_js()
    for signature in (
        "async function runSteps(",
        "async function playSequence(",
        "function applySubjectStep(",
        "function skipCurrentSequence(",
    ):
        body = _function_body(js, signature)
        for token in ("apiFetch", "fetch(", "target_status", "transition"):
            assert token not in body, f"{signature}: {token}"


def test_presentation_engine_modules_never_talk_to_the_server(client, db):
    for path in sorted(PRESENTATION_JS_DIR.glob("*.js")):
        source = path.read_text(encoding="utf-8")
        for token in ("apiFetch", "XMLHttpRequest", "target_status", "/api/"):
            assert token not in source, f"{path.name}: {token}"


# 状態遷移を書いてよい場所。ここ以外から lifecycle を進めてはいけない。
LIFECYCLE_CALLERS = (
    'getElementById("startRevealButton").addEventListener',
    'getElementById("finishButton").addEventListener',
    "async function finalizeSequentialResult(",
)


def test_lifecycle_posts_only_happen_in_explicit_host_actions(client, db):
    """状態遷移はHostの明示操作から始まる経路の中だけで起きること。

    演出 runner からは絶対に起こさない(別テストで直接検証している)。
    finalizeSequentialResult は「最終結果を確定する」ボタンからのみ呼ばれる。
    """
    js = _presentation_js()
    allowed = [_function_body(js, signature) for signature in LIFECYCLE_CALLERS]

    for match in re.finditer(r'target_status: (?:target|"[A-Z]+")', js):
        snippet = js[max(0, match.start() - 200):match.end()]
        assert any(
            snippet[-60:] in body or js[match.start():match.end()] in body
            for body in allowed
        ), f"想定外の場所から状態遷移している: {snippet[-80:]!r}"

    # 確定処理を呼ぶのは確定ボタンだけ
    callers = re.findall(r"finalizeSequentialResult\(", js)
    assert len(callers) == 2, "定義1件 + 呼び出し1件のはず"
    button = _function_body(js, 'getElementById("finalRankingButton").addEventListener')
    assert "finalizeSequentialResult(" in button


def test_skip_only_advances_the_animation(client, db):
    js = _presentation_js()
    body = _function_body(js, "function skipCurrentSequence(")
    for token in ("apiFetch", "target_status", "transition", "present"):
        assert token not in body, token
    # sequenceの正しい最終状態へ進めてから停止点のUIを出す
    assert "finalState()" in body
    assert "onComplete()" in body


def test_fullscreen_failure_never_breaks_the_presentation(client, db):
    js = _presentation_js()
    start = js.index('getElementById("fullscreenButton")')
    body = js[start:start + 900]
    assert "requestFullscreen" in body
    assert "catch" in body


# ---------------------------------------------------------------------------
# 1クリック = 1つの意味のある演出sequence
# ---------------------------------------------------------------------------


def test_batch_stops_after_every_subject(client, db):
    """BATCHが全被採点者を通しで自動再生しないこと。"""
    js = _presentation_js()
    assert "runRevealSequence" not in js, "全Subject連続再生は廃止した"

    body = _function_body(js, 'getElementById("batchRevealButton").addEventListener')
    # 1クリックで扱うのは1組だけ
    assert "batchOrder[batchIndex]" in body
    assert "for (" not in body and "forEach" not in body
    assert "showBatchAdvance" in body


def test_batch_advance_is_a_pure_ui_step(client, db):
    """「次の被採点者へ」はサーバーに触れない。"""
    js = _presentation_js()
    body = _function_body(js, 'getElementById("batchAdvanceButton").addEventListener')
    for token in ("apiFetch", "target_status", "transition"):
        assert token not in body, token


def test_batch_advance_button_switches_label_on_the_last_subject(client, db):
    js = _presentation_js()
    body = _function_body(js, "function showBatchAdvance(")
    assert "次の被採点者へ" in body
    assert "最終ランキングを発表する" in body
    assert "isLastBatchSubject()" in body


def test_judges_are_never_clicked_one_by_one(client, db):
    """Judge1人ごとのクリックを要求するUIが無いこと。"""
    html = _present_html(client)
    js = _presentation_js()
    assert "judgeNextButton" not in html
    assert "nextJudgeButton" not in html
    # Judgeの進行はstep配列に閉じている
    assert "core.buildSubjectSteps(" in js


def test_both_modes_share_one_reveal_engine(client, db):
    js = _presentation_js()
    assert js.count("async function revealSubjectSequence(") == 1
    assert "await revealSubjectSequence(subject, showBatchAdvance)" in js
    assert "await revealSubjectSequence(payload.subject," in js


# ---------------------------------------------------------------------------
# 演出中の再入・二重進行
# ---------------------------------------------------------------------------


def test_reentrancy_is_guarded_by_a_run_token(client, db):
    js = _presentation_js()
    body = _function_body(js, "async function runSteps(")
    assert "runToken += 1" in body
    assert "token !== runToken" in body


def test_polling_is_not_restarted_while_a_sequence_runs(client, db):
    """SEQUENTIALのpolling安全弁が残っていること(Phase 8Bからの引き継ぎ)。"""
    js = _presentation_js()
    assert "SEQUENTIAL_POLL_INTERVAL_MS = 15000" in js
    assert "document.hidden" in js
    assert "pollInFlight" in js
    assert "stopPolling" in js


# ---------------------------------------------------------------------------
# 視覚設計 token / Judge数対応
# ---------------------------------------------------------------------------


def test_presentation_design_tokens_are_defined(client, db):
    css = _reveal_css()
    for token in (
        "--p-bg-0",
        "--p-panel",
        "--p-gold",
        "--p-gold-hi",
        "--p-red",
        "--p-ivory",
        "--p-sub",
        "--p-gold-grad",
        "--u:",
    ):
        assert token in css, token
    # Goldはベタ塗りにしない
    assert "#FFD700" not in css.upper().replace("#FFD700", "#FFD700")
    assert "ffd700" not in css.lower()


def test_presentation_avoids_fragile_css_features(client, db):
    """対象外ブラウザでも静かに壊れない書き方に留めていること(コメントは除外)。"""
    css = re.sub(r"/\*.*?\*/", "", _reveal_css(), flags=re.S)
    assert ":has(" not in css
    assert "container-type" not in css
    assert "cqw" not in css


def test_reduced_motion_fallback_exists(client, db):
    css = _reveal_css()
    assert "prefers-reduced-motion" in css


def test_unrevealed_scores_use_a_dash_not_a_question_mark(client, db):
    stage_js = (PRESENTATION_JS_DIR / "stage.js").read_text(encoding="utf-8")
    assert 'UNREVEALED = "—"' in stage_js
    assert '"?"' not in stage_js


def test_rail_layout_is_driven_by_css_custom_properties(client, db):
    stage_js = (PRESENTATION_JS_DIR / "stage.js").read_text(encoding="utf-8")
    assert 'setProperty("--cols"' in stage_js
    assert 'setProperty("--slot-scale"' in stage_js
    css = _reveal_css()
    assert "repeat(var(--cols)" in css


def test_timing_is_pushed_from_js_into_css_variables(client, db):
    js = _presentation_js()
    body = _function_body(js, "function applyTiming(")
    assert "core.timingCssVars(" in body
    assert "setProperty" in body
    css = _reveal_css()
    assert "var(--t-judge-enter)" in css


# ---------------------------------------------------------------------------
# Analysis との分離 (Phase 8A の方針を維持)
# ---------------------------------------------------------------------------


def test_presentation_has_no_analysis_widgets(client, db):
    html = _present_html(client)
    assert "radar_chart.js" not in html
    for source in [_presentation_js()] + [
        p.read_text(encoding="utf-8") for p in sorted(PRESENTATION_JS_DIR.glob("*.js"))
    ]:
        assert "RadarChart" not in source
        assert "radar" not in source.lower()
        assert "feedback" not in source.lower()


# ---------------------------------------------------------------------------
# 既存の導線 (Phase 8D) を壊していないこと
# ---------------------------------------------------------------------------


def test_phase8d_navigation_survives_the_redesign(client, db):
    html = _present_html(client)
    panel = re.search(r'<section id="rankingPanel".*?</section>', html, re.S)
    assert panel
    for element_id in (
        "rankingActions",
        "finishButton",
        "replayButton",
        "backToDashboardLink",
        "finishedAnalysisLink",
    ):
        assert f'id="{element_id}"' in panel.group(0), element_id


def test_sequential_panels_are_untouched(client, app, db):
    created = create_project(client, presentation_mode="SEQUENTIAL")
    start_scoring(client, created["project_id"])
    html = client.get(f"/host/{created['project_id']}/present").get_data(as_text=True)
    for element_id in (
        "sequentialWaitingPanel",
        "sequentialStandbyPanel",
        "sequentialDashboardLink",
        "confirmNextPanel",
        "confirmNextButton",
        "finalRankingPanel",
        "finalRankingButton",
    ):
        assert f'id="{element_id}"' in html, element_id


# ---------------------------------------------------------------------------
# Phase 9C: server state の確定 != ranking visual の再描画
# ---------------------------------------------------------------------------


def test_final_confirmation_never_rebuilds_the_ranking_dom(client, db):
    """SEQ最終確定で完成済みのFINAL RANKINGを作り直さないこと。

    確定成功後に触ってよいのは currentSummary / project status / rankingActions
    だけで、ranking DOM そのものには一切触れない。
    """
    js = _presentation_js()
    body = _function_body(js, "async function finalizeSequentialResult(")

    for token in (
        "showRanking(",
        "rankingList.innerHTML",
        "renderRankingList(",
        "setRankingTitle(",
        "buildRankingRow(",
    ):
        assert token not in body, f"確定処理がranking DOMを作り直している: {token}"

    # 導線だけを更新する
    assert "renderRankingActions(" in body
    assert 'rankingPanel.dataset.preserved = "true"' in body


def test_final_confirmation_is_idempotent_and_forward_only(client, db):
    """現在statusを読み直し、足りない遷移だけを前向きに実行すること。"""
    js = _presentation_js()
    body = _function_body(js, "async function finalizeSequentialResult(")

    # まず現在statusを読み直してから遷移を組み立てる
    assert "await loadState()" in body
    assert body.index("await loadState()") < body.index("/present")

    # subject present -> Project LOCKED -> Project PRESENTING の順
    present_at = body.index("/present")
    locked_at = body.index('target_status: "LOCKED"')
    presenting_at = body.index('target_status: "PRESENTING"')
    assert present_at < locked_at < presenting_at

    # 条件付き実行(既に済んでいる遷移は再実行しない = 押し直しで再開できる)
    assert 'state.project.status === "SCORING"' in body
    assert 'state.project.status !== "PRESENTING"' in body
    assert 'presentation_status === "LOCKED"' in body

    # 後ろ向きの遷移を書いていない
    targets = set(re.findall(r'target_status: "([A-Z]+)"', body))
    assert targets == {"LOCKED", "PRESENTING"}, targets


def test_final_confirmation_keeps_the_visual_on_failure(client, db):
    js = _presentation_js()
    body = _function_body(js, "async function finalizeSequentialResult(")
    catch_index = body.index("} catch (err)")
    catch_block = body[catch_index:catch_index + 300]
    assert "showMessage" in catch_block
    # 失敗時にランキング表示を消す処理を入れていない
    for token in ("classList.add(\"hidden\")", "innerHTML"):
        assert token not in catch_block, token


def test_sequential_reveal_continues_into_the_ranking_without_a_click(client, db):
    """TOTALのあと、Hostのクリックを挟まずにランキング挿入まで自動で進むこと。"""
    js = _presentation_js()
    body = _function_body(js, 'getElementById("revealSubjectButton").addEventListener')
    assert "await revealSubjectSequence(" in body
    assert "await runSequentialRanking(" in body
    assert body.index("revealSubjectSequence(") < body.index("runSequentialRanking(")


def test_sequential_last_subject_stops_before_touching_the_server(client, db):
    """最終Subjectは FINAL RANKING 化まで進んで停止し、そこでは遷移しないこと。"""
    js = _presentation_js()
    body = _function_body(js, "async function runSequentialRanking(")
    for token in ("target_status", "/present", "transition"):
        assert token not in body, token
    # 停止点として確定ボタンを出すだけ
    assert "finalRankingPanel.classList.remove(\"hidden\")" in body

    steps = _function_body(js, "function applyRankStep(")
    assert 'case "FINAL_TITLE"' in steps
    assert 'case "WINNER_GLOW"' in steps


def test_final_ranking_button_is_the_only_sequential_lifecycle_trigger(client, db):
    js = _presentation_js()
    body = _function_body(js, 'getElementById("finalRankingButton").addEventListener')
    assert "finalizeSequentialResult(" in body
    # ボタンのハンドラ自身は遷移を書かない(冪等な確定処理へ委譲する)
    assert "target_status" not in body


def test_interim_ranking_is_fetched_read_only(client, db):
    js = _presentation_js()
    body = _function_body(js, "async function loadInterimRanking(")
    assert "interim-ranking" in body
    assert "method" not in body, "GET以外で叩いてはいけない"


def test_ranking_uses_flip_for_existing_rows(client, db):
    js = _presentation_js()
    body = _function_body(js, "function applyRankStep(")
    assert "stage.flipRows(" in body

    stage_js = (PRESENTATION_JS_DIR / "stage.js").read_text(encoding="utf-8")
    flip = _function_body(stage_js, "function flipRows(")
    assert "getBoundingClientRect" in flip
    assert "requestAnimationFrame" in flip
    # 単一列なので縦方向だけで足りる
    assert "translateY(" in flip
    assert "translateX(" not in flip


def test_ranking_highlights_only_the_incoming_subject(client, db):
    js = _presentation_js()
    body = _function_body(js, "function renderRankingList(")
    assert "highlightId" in body
    assert 'row.dataset.highlight = "true"' in body

    css = _reveal_css()
    assert '.p-rank-row[data-highlight="true"]' in css


def test_ranking_title_switches_to_final(client, db):
    js = _presentation_js()
    assert '"暫定ランキング"' in js
    assert '"FINAL RANKING"' in js
    body = _function_body(js, "function markRankingFinal(")
    assert 'rankingPanel.dataset.final = "true"' in body
    assert "FINAL_RANKING_TITLE" in body


def test_no_new_migrations_were_added_in_phase9():
    versions = Path(__file__).resolve().parent.parent / "migrations" / "versions"
    revisions = sorted(p.name for p in versions.glob("*.py"))
    assert revisions == [
        "9c4e17a2b8d3_add_presentation_modes.py",
        "b37d61517847_initial_schema.py",
        # Phase 10A-5 で追加した部分UNIQUE INDEX(expand-only)。
        # 検証は tests/test_phase10a_migration.py で行う。
        "c1f7a04b9e26_enforce_one_host_scorer_per_project.py",
        "d5b81c37f0ae_add_ordering_and_draw_columns.py",
    ], revisions


# ---------------------------------------------------------------------------
# Phase 9D: BATCH Final Ranking
# ---------------------------------------------------------------------------


def test_final_ranking_containers_exist(client, db):
    html = _present_html(client)
    grid = re.search(r'<div id="rankingList"[^>]*>(.*?)</div>\s*\n', html, re.S)
    assert grid, "rankingListが見つからない"
    assert 'id="rankingTop"' in html
    assert 'id="rankingRest"' in html


def test_batch_shows_no_ranking_until_the_final_reveal(client, db):
    """各TOTALは見えても、順位は最後まで伏せること。"""
    js = _presentation_js()

    # 得点発表sequenceはランキングに触れない
    body = _function_body(js, "function applySubjectStep(")
    for token in ("ranking", "Ranking"):
        assert token not in body, token

    # 被採点者間の進行もランキングを描かない
    advance = _function_body(js, "function showBatchAdvance(")
    assert "ranking" not in advance.lower()

    # 順位が出るのは Final Reveal を明示的に始めたときだけ
    button = _function_body(js, 'getElementById("batchAdvanceButton").addEventListener')
    assert "runBatchFinalRanking(" in button
    assert "isLastBatchSubject()" in button


def test_final_reveal_is_fully_automatic_once_started(client, db):
    """「最終ランキングを発表する」の1クリックから完成まで自動で走ること。"""
    js = _presentation_js()
    body = _function_body(js, "async function runBatchFinalRanking(")
    assert "core.buildBatchFinalSteps(" in body
    assert "playSequence({" in body
    # 途中でHostの操作を挟まない
    assert "addEventListener" not in body
    # サーバーにも触れない
    for token in ("apiFetch", "target_status", "transition"):
        assert token not in body, token


def test_final_reveal_is_driven_by_rank_groups_not_subjects(client, db):
    """Revealの単位が Subject ではなく rank group であること(同率対応)。"""
    js = _presentation_js()
    prepare = _function_body(js, "function prepareFinalRankingStage(")
    assert "core.toRankGroups(" in prepare
    assert "core.finalLayout(" in prepare

    body = _function_body(js, "function applyFinalStep(")
    assert "groupByRank(groups, step.rank)" in body
    for phase in ("QUICK_GROUP", "TOP_GROUP_ENTER", "WINNER_REVEAL"):
        assert f'case "{phase}"' in body, phase


def test_tied_groups_are_revealed_together_in_one_card(client, db):
    stage_js = (PRESENTATION_JS_DIR / "stage.js").read_text(encoding="utf-8")
    body = _function_body(stage_js, "function buildRankGroupCard(")
    # 1グループ = 1カード。同率メンバーは同じカードの中に入る。
    assert "group.subjects.forEach(" in body
    assert "core.groupLabel(group)" in body
    assert 'card.dataset.tied' in body

    css = _reveal_css()
    assert '.p-rank-card[data-tied="true"] .p-rank-card__members' in css


def test_lower_ranks_stack_upwards_and_top_ranks_land_above_them(client, db):
    js = _presentation_js()
    body = _function_body(js, "function appendGroupCard(")
    # 下位は 10位 -> 9位 ... の順に出るので、毎回先頭へ差し込むと下から積み上がる
    assert "container.insertBefore(card, container.firstChild)" in body


def test_final_layouts_cover_every_subject_count(client, db):
    css = _reveal_css()
    for layout in ("hero", "column", "split"):
        assert f'[data-layout="{layout}"]' in css, layout
    # split は左右2カラム
    assert "grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr)" in css


def test_winner_glow_only_after_the_final_state(client, db):
    """1位の強調は最終確定後だけ(暫定表示中は光らせない)。"""
    css = _reveal_css()
    assert '.p-ranking[data-winner="true"] .p-rank-card[data-rank="1"]' in css

    # 1位へglowを当てるruleは、必ず data-winner="true" のスコープ内にあること
    for selector, block in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if "--p-glow" not in block:
            continue
        if 'data-rank="1"' not in selector:
            continue
        assert 'data-winner="true"' in selector, selector.strip()

    js = _presentation_js()
    prepare = _function_body(js, "function prepareFinalRankingStage(")
    assert 'rankingPanel.dataset.winner = "false"' in prepare


def test_skip_produces_the_complete_final_ranking(client, db):
    js = _presentation_js()
    body = _function_body(js, "function showFinalRankingFinalState(")
    assert "core.toRankGroups(" in body
    assert 'rankingPanel.dataset.winner = "true"' in body
    # 上位と下位を正しいコンテナへ振り分ける
    assert "group.rank <= 3" in body


def test_replay_and_revisit_reuse_the_same_final_layout(client, db):
    """FINISHED再訪問 / replay でも同じレイアウトで完成形を出すこと。"""
    js = _presentation_js()
    body = _function_body(js, "function showRanking(summary) {")
    assert "core.finalLayout(" in body
    assert "showFinalRankingFinalState(summary)" in body
    assert "renderRankingActions(summary)" in body
