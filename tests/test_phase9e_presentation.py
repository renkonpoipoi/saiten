"""Phase 9E: Presentation final polish の契約。

ここで守るのは主に:
  1. Incoming Score Card はランキングの外から始まり、staging中は順位を出さない
  2. 順位はサーバーが唯一の正。JS側で再計算・再ソートしない
  3. Judge score tier は中央Revealだけでなく settle 後の rail にも残る
  4. TOTAL は得点に関係なく常に同じ演出(tier を適用しない)
  5. 音は装飾。無くても / 失敗しても Presentation は止まらない

演出の順序・尺は tests/js/core.test.mjs、DOMの最終状態は
tests/js/presentation_dom.test.mjs が実際に走らせて検証している。
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.helpers import create_project

APP_DIR = Path(__file__).resolve().parent.parent / "app"
JS_DIR = APP_DIR / "static" / "js"
PRESENTATION_JS_DIR = JS_DIR / "presentation"

PRESENTATION_JS = (JS_DIR / "result_presentation.js").read_text(encoding="utf-8")
STAGE_JS = (PRESENTATION_JS_DIR / "stage.js").read_text(encoding="utf-8")
CORE_JS = (PRESENTATION_JS_DIR / "core.js").read_text(encoding="utf-8")
REVEAL_CSS = (APP_DIR / "static" / "css" / "reveal.css").read_text(encoding="utf-8")
PRESENT_HTML = (APP_DIR / "templates" / "result_presentation.html").read_text(encoding="utf-8")


def _code_only(source: str) -> str:
    """コメントを除いた本体。説明文の語をコードの証拠と取り違えないため。"""
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", without_block, flags=re.M)


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


# ---------------------------------------------------------------------------
# Incoming Score Card
# ---------------------------------------------------------------------------


def test_incoming_card_lives_outside_the_ranking_list(client, db):
    created = create_project(client)
    html = client.get(f"/host/{created['project_id']}/present").get_data(as_text=True)
    assert 'id="incomingCard"' in html
    assert 'id="incomingStage"' in html

    ranking_list = html[html.index('id="rankingList"'):html.index('id="incomingStage"')]
    assert "incomingCard" not in ranking_list, "Incoming Card がランキングの中にある"


def test_incoming_card_has_no_rank_element(client, db):
    """★ staging中に順位を出せない構造にする(順位を入れる要素を持たない)。"""
    start = PRESENT_HTML.index('id="incomingCard"')
    card = PRESENT_HTML[start:PRESENT_HTML.index("</div>\n  </div>", start)]
    assert "incomingName" in card
    assert "incomingScore" in card
    assert "rank" not in card


def test_staging_shows_only_the_name_and_total(client, db):
    body = _function_body(STAGE_JS, "function showIncomingCard(")
    assert "incomingName" in body
    assert "incomingScore" in body
    for token in ("rank", "groupLabel", "位"):
        assert token not in body, token


def test_ranking_list_excludes_the_incoming_subject_while_staging(client, db):
    """★ staging中はランキングに今回の組を入れない(=順位も出ない)。"""
    body = _function_body(PRESENTATION_JS, "async function runSequentialRanking(")
    assert "alreadyRanked(interimSubjects, incomingId)" in body
    assert "stage.hideIncomingCard(stageRefs)" in body

    helper = _function_body(PRESENTATION_JS, "function alreadyRanked(")
    assert "s.id !== incomingId" in helper


def test_incoming_moves_from_staging_into_the_reserved_slot(client, db):
    body = _function_body(PRESENTATION_JS, "function applyRankStep(")
    insert = body.index('case "RANK_INSERT"')
    move = body.index('case "RANK_MOVE"')
    settled = body.index('case "RANK_SETTLED"')
    assert insert < move < settled

    assert "stage.showIncomingCard(" in body[insert:move]
    # 既存行のFLIPは維持したまま、今回の組は placeholder で場所を確保する
    assert "stage.flipRows(" in body[move:settled]
    assert "stage.moveIncomingToSlot(" in body[move:settled]
    assert "stage.placeholderRow(" in body[move:settled]


def test_rank_is_shown_only_on_arrival(client, db):
    """★ 順位が出るのは最終slotへ到着した瞬間だけ。"""
    body = _function_body(PRESENTATION_JS, "function applyRankStep(")
    settled = body.index('case "RANK_SETTLED"')
    assert "stage.normalizeIncomingRow(" in body[settled:]
    # 到着前に順位付きの行を描かない
    assert body.count("stage.normalizeIncomingRow(") == 1

    normalize = _function_body(STAGE_JS, "function normalizeIncomingRow(")
    assert "buildRankingRow(subject, groups)" in normalize
    assert "hideIncomingCard(refs)" in normalize


def test_placeholder_reserves_layout_without_a_rank(client, db):
    body = _function_body(STAGE_JS, "function buildPlaceholderRow(")
    assert 'row.dataset.placeholder = "true"' in body
    assert "rankLabel" not in body
    assert "dataset.rank" not in body
    assert '.p-rank-row[data-placeholder="true"]' in REVEAL_CSS


def test_incoming_card_reuses_the_existing_flip_machinery(client, db):
    """FLIPは捨てずに再利用する(既存行の移動は今までどおり)。"""
    move = _function_body(STAGE_JS, "function moveIncomingToSlot(")
    assert "getBoundingClientRect" in move
    flip = _function_body(STAGE_JS, "function flipRows(")
    assert "getBoundingClientRect" in flip


def test_javascript_never_computes_a_rank(client, db):
    """★ 順位・同率の判定はサーバーだけが行う。"""
    for source in (PRESENTATION_JS, STAGE_JS, CORE_JS):
        assert "total_score >" not in source
        assert "total_score <" not in source
        assert "b.total_score - a.total_score" not in source
        assert "rank + 1" not in source
        assert "rank++" not in source

    # 並べ替えはサーバーが付けた rank の昇順のみ
    sorted_by_rank = _function_body(STAGE_JS, "function sortedByRank(")
    assert "a.rank - b.rank" in sorted_by_rank
    assert "total_score" not in sorted_by_rank


def test_skip_lands_on_the_arrived_state(client, db):
    body = _function_body(PRESENTATION_JS, "function showSequentialRankingFinalState(")
    assert "renderRankingList(stage.sortedByRank(subjects), incomingId)" in body
    assert "stage.hideIncomingCard(stageRefs)" in body
    assert "buildPlaceholderRow" not in body


def test_staging_hold_is_around_750ms(client, db):
    body = _function_body(CORE_JS, "var TIMING = ")
    hold = re.search(r"rankHold:\s*(\d+)", body)
    assert hold is not None
    assert 600 <= int(hold.group(1)) <= 900, hold.group(1)


def test_incoming_card_uses_the_total_visual_language(client, db):
    block = REVEAL_CSS[REVEAL_CSS.index(".p-incoming {"):REVEAL_CSS.index(".p-incoming__name")]
    assert "var(--p-gold)" in block
    assert "125, 17, 27" in block, "Deep Red の backing がない"


# ---------------------------------------------------------------------------
# Judge score tier
# ---------------------------------------------------------------------------


def test_score_tier_is_a_pure_helper_in_core(client, db):
    body = _function_body(CORE_JS, "function scoreTier(")
    assert "document" not in body
    assert "dataset" not in body
    # 1採点者 = 100点満点固定。得点率にも criteria max_score にも依存しない
    for token in ("max_score", "percent", "/ 100", "judgeCount", "scorer_count"):
        assert token not in body, token


def test_score_tier_thresholds_are_declared_once(client, db):
    thresholds = re.findall(r'\{ tier: "(\w+)", min: (\d+) \}', CORE_JS)
    assert thresholds == [("premium", "90"), ("gold", "80"), ("silver", "70")]


def test_central_reveal_and_rail_share_one_tier_source(client, db):
    reveal = _function_body(STAGE_JS, "function revealJudgeScore(")
    fill = _function_body(STAGE_JS, "function fillSlot(")
    assert "core.scoreTier(judge.total)" in reveal
    assert "core.scoreTier(judge.total)" in fill
    # 中央 / rail のどちらも同じ tier 語彙(data-tier)を使う
    assert "dataset.tier" in reveal
    assert "dataset.tier" in fill


def test_tier_is_not_applied_before_the_score_is_visible(client, db):
    enter = _function_body(STAGE_JS, "function enterJudge(")
    assert "scoreTier" not in enter
    assert "delete card.dataset.tier" in enter


def test_every_tier_has_central_styling(client, db):
    for tier in ("standard", "silver", "gold", "premium"):
        selector = f'.p-active[data-state="revealed"][data-tier="{tier}"] .p-active__score'
        assert selector in REVEAL_CSS, tier


def test_every_scored_tier_stays_on_the_rail(client, db):
    for tier in ("silver", "gold", "premium"):
        selector = f'.p-slot[data-state="revealed"][data-tier="{tier}"] .p-slot__score'
        assert selector in REVEAL_CSS, tier


def test_the_rail_is_not_permanently_glowing(client, db):
    """rail に残すのは数字の色と細い accent だけ。常時 Glow はさせない。"""
    start = REVEAL_CSS.index('.p-slot[data-state="revealed"][data-tier="silver"]')
    block = REVEAL_CSS[start:REVEAL_CSS.index(".p-slot__name {", start)]
    assert "var(--p-glow" not in block
    assert "text-shadow" not in block


def test_the_card_itself_is_never_painted_silver_or_gold(client, db):
    """score number に意味を持たせる。カード全体を塗りつぶさない。"""
    for tier in ("silver", "gold", "premium"):
        start = REVEAL_CSS.index(f'.p-active[data-tier="{tier}"] {{')
        block = REVEAL_CSS[start:REVEAL_CSS.index("}", start)]
        assert "background" not in block, tier
        assert "border-color" in block, tier


def test_no_negative_treatment_for_low_scores(client, db):
    """STANDARD に警告色・失敗音・shake の類を割り当てない。"""
    standard = REVEAL_CSS[
        REVEAL_CSS.index('.p-active[data-state="revealed"][data-tier="standard"]'):
        REVEAL_CSS.index('.p-active[data-state="revealed"][data-tier="silver"]')
    ]
    assert "var(--p-red" not in standard
    assert "animation" not in standard
    for token in ("shake", "buzzer", "boo", "failure", "wrong"):
        assert token not in _code_only(REVEAL_CSS).lower(), token
        assert token not in _code_only(CORE_JS).lower(), token
        assert token not in _code_only(PRESENTATION_JS).lower(), token


def test_silver_token_exists_and_is_not_blue(client, db):
    silver = re.search(r"--p-silver:\s*#([0-9a-fA-F]{6})", REVEAL_CSS)
    assert silver is not None
    red, green, blue = (int(silver.group(1)[i:i + 2], 16) for i in (0, 2, 4))
    # 青に振らない(青>赤 だと寒色の銀になる)
    assert blue <= red, silver.group(1)
    assert abs(red - green) < 24 and abs(green - blue) < 24


# ---------------------------------------------------------------------------
# TOTAL は固定
# ---------------------------------------------------------------------------


def test_total_never_uses_a_score_tier(client, db):
    """★ TOTAL は得点に関係なく常に同じ演出。"""
    body = _function_body(STAGE_JS, "function revealTotal(")
    for token in ("scoreTier", "tier", "percent", "judgeCue"):
        assert token not in body, token

    # CSS 側にも TOTAL の tier 差は存在しない
    assert ".p-total[data-tier" not in REVEAL_CSS
    assert '.p-total__value[data-tier' not in REVEAL_CSS


def test_total_has_its_own_fixed_frame(client, db):
    block = REVEAL_CSS[REVEAL_CSS.index(".p-total {"):REVEAL_CSS.index(".p-total__label")]
    assert "var(--p-gold)" in block
    assert "125, 17, 27" in block, "Deep Red backdrop がない"
    assert "var(--p-glow-strong)" in REVEAL_CSS[
        REVEAL_CSS.index('.p-total[data-state="revealed"]'):
        REVEAL_CSS.index(".p-total__label")
    ]


def test_total_is_larger_than_any_judge_score(client, db):
    def font_size(selector: str) -> float:
        start = REVEAL_CSS.index(selector)
        block = REVEAL_CSS[start:REVEAL_CSS.index("}", start)]
        return float(re.search(r"font-size: calc\(var\(--u\) \* ([\d.]+)\)", block).group(1))

    assert font_size(".p-total__value {") > font_size(".p-active__score {")


# ---------------------------------------------------------------------------
# Audio (asset優先 + procedural fallback)
# ---------------------------------------------------------------------------

AUDIO_JS = (PRESENTATION_JS_DIR / "audio.js").read_text(encoding="utf-8")
MANIFEST_JS = (APP_DIR / "static" / "assets" / "sfx" / "manifest.js").read_text(encoding="utf-8")


def test_manifest_can_stay_empty(client, db):
    """素材を1つも置かなくても出荷できる(合成音が鳴る)。"""
    entries = re.findall(r"^\s*(\w+):\s*\"", MANIFEST_JS, flags=re.M)
    assert entries == [], entries


def test_manifest_documents_every_logical_cue(client, db):
    for cue in (
        "judgeStandard", "judgeSilver", "judgeGold", "judgePremium",
        "totalSting", "rankTick", "rankSettle", "winnerSting",
    ):
        assert cue in MANIFEST_JS, cue


def test_no_broadcast_audio_or_spoken_score(client, db):
    """放送音源・実在人物の音声・読み上げ・BGM を持ち込まない。"""
    code = _code_only(AUDIO_JS) + _code_only(MANIFEST_JS) + _code_only(PRESENTATION_JS)
    for token in ("speechSynthesis", "SpeechUtterance", "点!", "bgm", "music", "youtube", "http"):
        assert token not in code, token


def test_audio_is_decoration_not_a_gate(client, db):
    """演出の進行は AudioBus の戻り値を一切参照しない。"""
    for signature in ("function playSfx(", "function stopSfx("):
        body = _function_body(PRESENTATION_JS, signature)
        assert "return audio" not in body, signature
        assert "await" not in body, signature
    for token in ("if (audio.play(", "if (playSfx(", "await audio", "= playSfx("):
        assert token not in PRESENTATION_JS, token


def test_audio_failures_are_swallowed(client, db):
    play = _function_body(AUDIO_JS, "function playProcedural(")
    assert "try {" in play and "catch" in play
    assert "return false" in play
    asset = _function_body(AUDIO_JS, "function playAsset(")
    assert "playProcedural(key)" in asset, "asset失敗時のfallbackがない"


def test_one_audio_context_is_reused(client, db):
    """AudioContext を大量に作らない。"""
    assert _code_only(AUDIO_JS).count("new Ctor()") == 1
    body = _function_body(AUDIO_JS, "function ensureContext(")
    assert "if (context) return context;" in body


def test_oscillators_are_released_after_they_finish(client, db):
    body = _function_body(AUDIO_JS, "function track(")
    assert "onended" in body
    assert "disconnect()" in body
    layer = _function_body(AUDIO_JS, "function playLayer(")
    assert "oscillator.stop(" in layer, "止め忘れた node が残る"


def test_judge_cue_fires_when_the_score_becomes_visible(client, db):
    """★ カード登場時に先走らず、点数が見える瞬間に鳴らす。"""
    body = _function_body(PRESENTATION_JS, "function applySubjectStep(")
    enter = body.index('case "JUDGE_ENTER"')
    score = body.index('case "JUDGE_SCORE"')
    assert "playSfx" not in body[enter:score]
    assert "playSfx(core.judgeCue(" in body[score:]


def test_total_always_uses_the_same_cue(client, db):
    body = _function_body(PRESENTATION_JS, "function applySubjectStep(")
    assert body.count('playSfx("totalSting")') == 1
    assert "judgeCue(subject.total_score)" not in PRESENTATION_JS
    assert "scoreTier(subject.total_score)" not in PRESENTATION_JS


def test_winner_sting_fires_once_per_rank_group(client, db):
    """同率1位でも rank group 単位で1回だけ鳴る。"""
    final_step = _function_body(PRESENTATION_JS, "function applyFinalStep(")
    assert final_step.count('playSfx("winnerSting")') == 1
    rank_step = _function_body(PRESENTATION_JS, "function applyRankStep(")
    assert rank_step.count('playSfx("winnerSting")') == 1


def test_ranking_cues_are_not_fired_per_row(client, db):
    """rankTick が行ごとに何度も鳴らないこと(sequenceのstep単位で1回)。"""
    rank_step = _function_body(PRESENTATION_JS, "function applyRankStep(")
    assert rank_step.count('playSfx("rankTick")') == 1
    assert rank_step.count('playSfx("rankSettle")') == 1
    render = _function_body(PRESENTATION_JS, "function renderRankingList(")
    assert "playSfx" not in render


def test_audio_is_primed_from_a_host_click(client, db):
    """autoplay制限は最初のHostクリックで解除する。"""
    for handler in ("startRevealButton", "batchRevealButton", "revealSubjectButton"):
        body = _function_body(PRESENTATION_JS, f'getElementById("{handler}").addEventListener')
        assert "prepareAudio()" in body, handler
    prime = _function_body(AUDIO_JS, "prime: function (")
    assert "resume" in prime


def test_skip_stops_the_sound_of_the_skipped_sequence(client, db):
    body = _function_body(PRESENTATION_JS, "function skipCurrentSequence(")
    assert "stopSfx()" in body
    cancel = _function_body(AUDIO_JS, "cancel: function (")
    assert "cancelScheduledValues" in cancel
    assert "stop(now + 0.01)" in cancel


# ---------------------------------------------------------------------------
# reduced motion / Skip / controls
# ---------------------------------------------------------------------------


def test_reduced_motion_only_shortens_the_waits(client, db):
    """★ phase の順序も分岐も変えない(最終状態は通常再生と同じ)。"""
    body = _function_body(CORE_JS, "function reducedSteps(")
    assert "Math.min(step.duration, limit)" in body
    for token in ("filter(", "splice(", "phase =", "if (step.phase"):
        assert token not in body, token


def test_the_runner_honours_prefers_reduced_motion(client, db):
    body = _function_body(PRESENTATION_JS, "async function runSteps(")
    assert "prefersReducedMotion()" in body
    assert "core.reducedSteps(steps)" in body

    detect = _function_body(PRESENTATION_JS, "function prefersReducedMotion(")
    assert "(prefers-reduced-motion: reduce)" in detect
    # matchMedia が無い環境でも落ちない
    assert "catch" in detect
    assert "return false" in detect


def test_reduced_motion_does_not_mute_the_presentation(client, db):
    """音は自動でmuteしない(視覚だけで全情報が分かる設計は別途保証)。"""
    body = _function_body(PRESENTATION_JS, "function playSfx(")
    assert "audio.play(key)" in body
    # 尺を詰めた分、余韻の重なりだけを畳む
    assert "prefersReducedMotion() && audio.cancel" in body


def test_reduced_motion_css_covers_the_incoming_card(client, db):
    block = REVEAL_CSS[REVEAL_CSS.index("@media (prefers-reduced-motion: reduce)"):]
    block = block[:block.index("/* ---")]
    assert "transition-duration: 1ms" in block
    assert ".p-incoming" in block


def test_skip_still_never_touches_the_server(client, db):
    """Phase 9E で音の停止を足しても、Skipは client の演出だけを進める。"""
    body = _function_body(PRESENTATION_JS, "function skipCurrentSequence(")
    for token in ("apiFetch", "target_status", "transition", "/api/"):
        assert token not in body, token
    assert "finalState()" in body
    assert "onComplete()" in body
    assert "stopSfx()" in body


def test_controls_only_fade_while_a_sequence_is_running(client, db):
    body = _function_body(PRESENTATION_JS, "function flashControls(")
    assert 'stageControls.dataset.visible = "true"' in body
    assert 'presentationRoot.dataset.busy === "true"' in body
    assert "CONTROLS_HIDE_MS" in body

    hide_ms = int(re.search(r"CONTROLS_HIDE_MS = (\d+)", PRESENTATION_JS).group(1))
    assert 1500 <= hide_ms <= 4000, hide_ms


def test_controls_come_back_for_pointer_and_keyboard(client, db):
    for event in ("mousemove", "keydown", "focusin"):
        assert f'document.addEventListener("{event}"' in PRESENTATION_JS, event
    # focus / hover では CSS 側でも必ず見えるようにしてある
    assert ".p-controls:focus-within" in REVEAL_CSS
    assert '.p-root[data-busy="false"] .p-controls' in REVEAL_CSS


def test_controls_are_never_hidden_from_assistive_tech(client, db):
    assert "aria-hidden" not in PRESENT_HTML
    assert "display: none" not in REVEAL_CSS[
        REVEAL_CSS.index(".p-controls {"):REVEAL_CSS.index(".p-root .p-controls button")
    ]


def test_judge_timing_budget_is_unchanged(client, db):
    """Phase 9E で Judge の尺は再設計しない(既存の骨格を維持)。"""
    timing = _function_body(CORE_JS, "var TIMING = ")
    expected = {
        "judgeEnter": 450, "judgeHold": 500, "judgeScore": 250,
        "judgeVisible": 1000, "judgeSettle": 400, "judgeGap": 300,
        "allSettled": 1400, "totalEnter": 500, "totalVisible": 1300,
        "subjectExit": 400,
    }
    for key, value in expected.items():
        assert re.search(rf"{key}: {value}\b", timing), key
    assert "JUDGE_BUDGET_MS = 26000" in CORE_JS
    assert "PER_JUDGE_BASE_MS = 2900" in CORE_JS


# ---------------------------------------------------------------------------
# Phase 10B への回帰
# ---------------------------------------------------------------------------


def test_draw_page_engine_is_untouched(client, db):
    """抽選ページが使う純粋関数と結果決定ロジックに手を入れていない。"""
    for name in ("buildDrawSteps", "drawSpinIntervals", "remainingCandidates"):
        assert f"{name}: {name}," in CORE_JS, name

    draw_js = (JS_DIR / "draw.js").read_text(encoding="utf-8")
    assert "core.remainingCandidates(subjectNames(progress), drawnNames(progress))" in draw_js
    assert "expected_cursor: draw.draw_cursor" in draw_js
    # 抽選ページは結果発表のcueを持ち込まない
    assert "totalSting" not in draw_js
    assert "winnerSting" not in draw_js


def test_event_order_still_drives_the_subject_order(client, db):
    body = _function_body(PRESENTATION_JS, "function orderedSubjects(")
    assert "core.orderByEvent(summary.subjects)" in body
    assert "sort_order" not in body
