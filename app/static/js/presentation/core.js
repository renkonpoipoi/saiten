/* Presentation engine の純粋関数。DOM もタイマーも API も触らない。
   「何がいつ起きるか」をここで step descriptor の配列として決め、
   実行は result_presentation.js の runner が行う。この分離により、
   演出の順序・尺・分岐を時間待ちなしでテストできる
   (tests/js/core.test.mjs)。 */
(function (root) {
  "use strict";

  // 演出の基準尺(ms)。Phase 9E の実ブラウザ調整でここだけを触る。
  var TIMING = {
    subjectIntro: 600,

    // Judge 1人あたり。合計 = PER_JUDGE_BASE_MS
    judgeEnter: 450,
    judgeHold: 500,
    judgeScore: 250,
    judgeVisible: 1000,
    judgeSettle: 400,
    judgeGap: 300,

    allSettled: 1400,
    totalEnter: 500,
    totalVisible: 1300,
    subjectExit: 400,

    // SEQUENTIAL: 暫定ランキングへの挿入
    // rankInsert = Incoming Score Card が staging に出るまで
    // rankHold   = staging で見せる間(順位はまだ出さない)
    rankInsert: 600,
    rankHold: 750,
    rankMove: 750,
    rankSettled: 500,
    finalTitle: 800,
    winnerGlow: 1200,

    // 発表順の抽選(Phase 10B)
    drawIntro: 700,
    drawSpin: 1800,
    drawSettle: 500,
    drawHold: 1600,

    // BATCH: Final Ranking Reveal
    finalIntro: 1200,
    quickGroup: 430,
    topPause: 900,
    topGroupEnter: 700,
    topGroupVisible: 900,
    topGroupSettle: 500,
    winnerHold: 1800,
    winnerReveal: 900,
    winnerSettle: 600
  };

  // Judge数が多くても「読めなくなる」ことがないよう、縮小には床を張る。
  var JUDGE_FLOORS = {
    judgeEnter: 260,
    judgeScore: 150,
    judgeVisible: 550,
    judgeSettle: 240
  };

  // Judge区間の総尺に予算を置く。固定尺だと13人で38秒かかり冗長、
  // 単純比例だと3人が速すぎる。予算制 + 床が両立する唯一の単純解。
  var JUDGE_BUDGET_MS = 26000;
  var PER_JUDGE_BASE_MS = 2900;

  var SCALABLE_JUDGE_KEYS = [
    "judgeEnter", "judgeHold", "judgeScore", "judgeVisible", "judgeSettle", "judgeGap"
  ];

  function clamp(min, value, max) {
    return Math.min(max, Math.max(min, value));
  }

  function round2(value) {
    return Math.round(value * 100) / 100;
  }

  function judgeScale(judgeCount) {
    var n = Number(judgeCount) || 0;
    if (n <= 0) return 1;
    return clamp(0.55, JUDGE_BUDGET_MS / (n * PER_JUDGE_BASE_MS), 1);
  }

  /** Judge数に応じて縮めた1人あたりの尺。床を下回らせない。 */
  function scaledJudgeTiming(judgeCount, timing) {
    var base = timing || TIMING;
    var scale = judgeScale(judgeCount);
    var out = {};
    SCALABLE_JUDGE_KEYS.forEach(function (key) {
      var scaled = Math.round(base[key] * scale);
      var floor = JUDGE_FLOORS[key];
      out[key] = floor ? Math.max(floor, scaled) : scaled;
    });
    return out;
  }

  /** 1 Subject の得点発表(Judge全員 -> TOTAL)。running total は存在しない。 */
  function buildSubjectSteps(judgeCount, timing) {
    var base = timing || TIMING;
    var n = Math.max(0, Number(judgeCount) || 0);
    var j = scaledJudgeTiming(n, base);
    var steps = [{ phase: "SUBJECT_INTRO", duration: base.subjectIntro }];

    for (var i = 0; i < n; i += 1) {
      steps.push({ phase: "JUDGE_ENTER", duration: j.judgeEnter, index: i });
      steps.push({ phase: "JUDGE_HOLD", duration: j.judgeHold, index: i });
      steps.push({ phase: "JUDGE_SCORE", duration: j.judgeScore, index: i });
      steps.push({ phase: "JUDGE_VISIBLE", duration: j.judgeVisible, index: i });
      steps.push({ phase: "JUDGE_SETTLE", duration: j.judgeSettle, index: i });
      // 最後のJudgeの後は ALL_SETTLED の長い溜めに繋げるので gap を入れない
      if (i < n - 1) {
        steps.push({ phase: "JUDGE_GAP", duration: j.judgeGap, index: i });
      }
    }

    steps.push({ phase: "ALL_SETTLED", duration: base.allSettled });
    steps.push({ phase: "TOTAL_ENTER", duration: base.totalEnter });
    steps.push({ phase: "TOTAL_VISIBLE", duration: base.totalVisible });
    steps.push({ phase: "SUBJECT_EXIT", duration: base.subjectExit });
    return steps;
  }

  /** SEQUENTIAL: 暫定ランキングへの挿入。最終Subjectだけ FINAL 化まで続ける。 */
  function buildSequentialRankSteps(isLast, timing) {
    var base = timing || TIMING;
    var steps = [
      { phase: "RANK_INSERT", duration: base.rankInsert },
      { phase: "RANK_HOLD", duration: base.rankHold },
      { phase: "RANK_MOVE", duration: base.rankMove },
      { phase: "RANK_SETTLED", duration: base.rankSettled }
    ];
    if (isLast) {
      steps.push({ phase: "FINAL_TITLE", duration: base.finalTitle });
      steps.push({ phase: "WINNER_GLOW", duration: base.winnerGlow });
    }
    steps.push({ phase: "COMPLETE", duration: 0 });
    return steps;
  }

  /** BATCH: Final Ranking。下位は速く、上位はゆっくり、1位が最大演出。 */
  function buildBatchFinalSteps(rankGroups, timing) {
    var base = timing || TIMING;
    var order = revealOrder(rankGroups || []);
    var steps = [{ phase: "FINAL_INTRO", duration: base.finalIntro }];

    var lower = order.filter(function (g) { return g.rank > 3; });
    var top = order.filter(function (g) { return g.rank <= 3; });

    lower.forEach(function (group) {
      steps.push({ phase: "QUICK_GROUP", duration: base.quickGroup, rank: group.rank });
    });

    if (top.length) {
      steps.push({ phase: "TOP_PAUSE", duration: base.topPause });
    }

    top.forEach(function (group) {
      if (group.rank === 1) {
        steps.push({ phase: "WINNER_HOLD", duration: base.winnerHold, rank: group.rank });
        steps.push({ phase: "WINNER_REVEAL", duration: base.winnerReveal, rank: group.rank });
        steps.push({ phase: "WINNER_SETTLE", duration: base.winnerSettle, rank: group.rank });
      } else {
        steps.push({ phase: "TOP_GROUP_ENTER", duration: base.topGroupEnter, rank: group.rank });
        steps.push({ phase: "TOP_GROUP_VISIBLE", duration: base.topGroupVisible, rank: group.rank });
        steps.push({ phase: "TOP_GROUP_SETTLE", duration: base.topGroupSettle, rank: group.rank });
      }
    });

    steps.push({ phase: "COMPLETE", duration: 0 });
    return steps;
  }

  /** 抽選演出の1回分。候補の高速切替 -> 減速 -> 結果に着地 -> 少し見せる。
   *
   * **切替はあくまで演出**で、着地するのはサーバーが返した1件だけ。
   * どの候補で止まるかを client の乱数が決めることは無い。
   */
  function buildDrawSteps(timing) {
    var base = timing || TIMING;
    return [
      { phase: "DRAW_INTRO", duration: base.drawIntro },
      { phase: "DRAW_SPIN", duration: base.drawSpin },
      { phase: "DRAW_SETTLE", duration: base.drawSettle },
      { phase: "DRAW_RESULT", duration: base.drawHold },
      { phase: "DRAW_COMPLETE", duration: 0 }
    ];
  }

  /** 候補の切替間隔。だんだん遅くする(等間隔だと機械的に見える)。 */
  function drawSpinIntervals(timing) {
    var base = timing || TIMING;
    var intervals = [];
    var elapsed = 0;
    var step = 60;
    while (elapsed < base.drawSpin) {
      intervals.push(step);
      elapsed += step;
      step = Math.round(step * 1.12);
    }
    return intervals;
  }

  /** まだ抽選されていない候補だけを残す。抽選済みを再び候補に出さない。 */
  function remainingCandidates(allSubjects, drawnNames) {
    var drawn = {};
    (drawnNames || []).forEach(function (name) { drawn[name] = true; });
    return (allSubjects || []).filter(function (name) { return !drawn[name]; });
  }

  /** 本番の進行順。server の event_order を唯一の根拠にし、
   *  それが無い応答(旧clientキャッシュ等)だけ sort_order へ戻す。 */
  function eventOrder(subject) {
    if (subject && typeof subject.event_order === "number") return subject.event_order;
    if (subject && typeof subject.sort_order === "number") return subject.sort_order;
    return 0;
  }

  /** Subject配列を本番の進行順に並べ替える(元配列は変更しない)。 */
  function orderByEvent(subjects) {
    return (subjects || []).slice().sort(function (a, b) {
      return eventOrder(a) - eventOrder(b);
    });
  }

  /* -------------------------------------------------------------------------
     Judge score tier (Phase 9E)

     **1採点者 = 100点満点固定**。percentage変換も、criteria max_score 合計に
     よる動的thresholdも行わない(採点仕様には一切触れない)。
     境界は 70 / 80 / 90 の3点だけ。

     STANDARD は「悪い得点」ではなく通常表示。下位側に negative な演出
     (赤い警告色・buzzer・shake等)を割り当ててはならない。高得点側だけを
     上方向へ強めるための段階分けである。
     ------------------------------------------------------------------------- */
  var SCORE_TIERS = ["standard", "silver", "gold", "premium"];

  // 上から順に評価する。境界値はその tier に含める(70 は SILVER)。
  var TIER_THRESHOLDS = [
    { tier: "premium", min: 90 },
    { tier: "gold", min: 80 },
    { tier: "silver", min: 70 }
  ];

  // tier -> Judge の効果音キー。TOTAL / Winner は得点に依存しない別系統。
  var JUDGE_CUES = {
    standard: "judgeStandard",
    silver: "judgeSilver",
    gold: "judgeGold",
    premium: "judgePremium"
  };

  /** 採点者1人がその被採点者へ付けた合計点(0〜100)から tier を決める。 */
  function scoreTier(score) {
    var value = Number(score);
    if (!isFinite(value)) return "standard";
    for (var i = 0; i < TIER_THRESHOLDS.length; i += 1) {
      if (value >= TIER_THRESHOLDS[i].min) return TIER_THRESHOLDS[i].tier;
    }
    return "standard";
  }

  /** その得点で鳴らす Judge の cue。TOTAL には絶対に使わない。 */
  function judgeCue(score) {
    return JUDGE_CUES[scoreTier(score)];
  }

  /** step配列の総尺(ms)。テストと運用尺の見積りに使う。 */
  function totalDuration(steps) {
    return (steps || []).reduce(function (sum, step) { return sum + step.duration; }, 0);
  }

  /* -------------------------------------------------------------------------
     Judge rail のレイアウト
     1〜5 / 6〜9 / 10〜13 を重点設計し、14人以上は縮小・多段で安全に退避する。
     Judge数の上限validationは追加しない(DB/Project側の制約は変更しない)。
     ------------------------------------------------------------------------- */
  function railLayout(judgeCount) {
    var n = Math.max(1, Number(judgeCount) || 1);
    if (n <= 5) return { rows: 1, cols: n, slotScale: 3.2 };
    if (n <= 9) return { rows: 1, cols: n, slotScale: 2.4 };
    if (n <= 13) return { rows: 2, cols: Math.ceil(n / 2), slotScale: 1.9 };
    if (n <= 20) {
      var cols2 = Math.ceil(n / 2);
      return { rows: 2, cols: cols2, slotScale: round2(clamp(1.2, 13 / cols2, 1.9)) };
    }
    var cols3 = Math.ceil(n / 3);
    return { rows: 3, cols: cols3, slotScale: round2(clamp(1.0, 13 / cols3, 1.6)) };
  }

  /* -------------------------------------------------------------------------
     competition ranking の rank group 化
     順位そのものはサーバーが確定済み。ここでは並べ替えとグルーピングしかしない。
     ------------------------------------------------------------------------- */
  function toRankGroups(subjects) {
    var ordered = (subjects || []).slice().sort(function (a, b) {
      return (a.rank - b.rank) || (a.sort_order - b.sort_order);
    });
    var groups = [];
    var byRank = {};
    ordered.forEach(function (subject) {
      var key = String(subject.rank);
      if (!byRank[key]) {
        byRank[key] = { rank: subject.rank, tied: false, subjects: [] };
        groups.push(byRank[key]);
      }
      byRank[key].subjects.push(subject);
    });
    groups.forEach(function (group) { group.tied = group.subjects.length > 1; });
    return groups;
  }

  /** Reveal順は下位group から。 */
  function revealOrder(groups) {
    return (groups || []).slice().reverse();
  }

  /** 同率は「同率N位」、単独は「第N位」。存在しない順位は作らない。 */
  function groupLabel(group) {
    if (!group) return "";
    return (group.tied ? "同率" : "第") + group.rank + "位";
  }

  /* -------------------------------------------------------------------------
     BATCH Final Ranking のレイアウト
     同率でTOP3エリアが膨らむ場合は split が破綻するので column へ退避する。
     ------------------------------------------------------------------------- */
  function finalLayout(subjects, groups) {
    var list = subjects || [];
    var count = list.length;
    var topCount = list.filter(function (s) { return s.rank <= 3; }).length;
    if (count <= 3) return "hero";
    if (count <= 6) return "column";
    if (topCount <= 4) return "split";
    return "column";
  }

  /* -------------------------------------------------------------------------
     timing を CSS custom property へ渡すための写像。
     JS を単一の真実源にし、CSS transition と JS の wait がズレないようにする。
     ------------------------------------------------------------------------- */
  function timingCssVars(judgeCount, timing) {
    var base = timing || TIMING;
    var j = scaledJudgeTiming(judgeCount, base);
    return {
      "--t-judge-enter": j.judgeEnter + "ms",
      "--t-judge-hold": j.judgeHold + "ms",
      "--t-judge-score": j.judgeScore + "ms",
      "--t-judge-visible": j.judgeVisible + "ms",
      "--t-judge-settle": j.judgeSettle + "ms",
      "--t-total-enter": base.totalEnter + "ms",
      "--t-subject-intro": base.subjectIntro + "ms",
      "--t-rank-insert": base.rankInsert + "ms",
      "--t-rank-move": base.rankMove + "ms",
      "--t-rank-settled": base.rankSettled + "ms",
      "--t-quick-group": base.quickGroup + "ms",
      "--t-top-enter": base.topGroupEnter + "ms",
      "--t-winner-reveal": base.winnerReveal + "ms"
    };
  }

  var api = {
    TIMING: TIMING,
    JUDGE_FLOORS: JUDGE_FLOORS,
    JUDGE_BUDGET_MS: JUDGE_BUDGET_MS,
    PER_JUDGE_BASE_MS: PER_JUDGE_BASE_MS,
    clamp: clamp,
    judgeScale: judgeScale,
    scaledJudgeTiming: scaledJudgeTiming,
    buildSubjectSteps: buildSubjectSteps,
    buildSequentialRankSteps: buildSequentialRankSteps,
    buildBatchFinalSteps: buildBatchFinalSteps,
    buildDrawSteps: buildDrawSteps,
    drawSpinIntervals: drawSpinIntervals,
    remainingCandidates: remainingCandidates,
    eventOrder: eventOrder,
    SCORE_TIERS: SCORE_TIERS,
    JUDGE_CUES: JUDGE_CUES,
    scoreTier: scoreTier,
    judgeCue: judgeCue,
    orderByEvent: orderByEvent,
    totalDuration: totalDuration,
    railLayout: railLayout,
    toRankGroups: toRankGroups,
    revealOrder: revealOrder,
    groupLabel: groupLabel,
    finalLayout: finalLayout,
    timingCssVars: timingCssVars
  };

  root.PresentationCore = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
