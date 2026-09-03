/* Presentation ステージの描画。DOM の組み立てと状態反映だけを担当し、
   「いつ何をするか」は core.js の step descriptor と runner が決める。

   設計上の要点:
   - 下部の Judge rail は DOM 上で動かさない。中央の active card は別レイヤの
     単一要素で、下からせり上がるように見せる(layout安定性と保守性のため)。
   - running total は存在しない。TOTAL は全Judge開示後にだけ出す。
   - ここから通信は行わない(サーバー状態を進めない)。 */
(function (root) {
  "use strict";

  var core = root.PresentationCore;

  var UNREVEALED = "—";

  function railSlots(refs) {
    return Array.prototype.slice.call(refs.rail.children);
  }

  /** 全Judgeのslotを未開示状態で並べる。Judge名は最初から見えていてよい。 */
  function buildRail(refs, judges) {
    var layout = core.railLayout(judges.length);
    refs.rail.innerHTML = "";
    refs.rail.style.setProperty("--cols", String(layout.cols));
    refs.rail.style.setProperty("--slot-scale", String(layout.slotScale));
    refs.rail.dataset.rows = String(layout.rows);

    judges.forEach(function (judge) {
      var slot = document.createElement("div");
      slot.className = "p-slot";
      slot.dataset.state = "waiting";
      slot.dataset.scorerId = String(judge.scorer_id);

      var score = document.createElement("div");
      score.className = "p-slot__score";
      // 未開示は「?」ではなく dash + 暗いスコアウィンドウ。
      // 「まだ点灯していないランプ」に読ませる。
      score.textContent = UNREVEALED;

      var name = document.createElement("div");
      name.className = "p-slot__name";
      name.textContent = judge.display_name;

      slot.append(score, name);
      refs.rail.appendChild(slot);
    });
  }

  function markSlot(refs, index, state) {
    var slot = railSlots(refs)[index];
    if (slot) slot.dataset.state = state;
  }

  function fillSlot(refs, index, judge) {
    var slot = railSlots(refs)[index];
    if (!slot) return;
    slot.dataset.state = "revealed";
    var score = slot.querySelector(".p-slot__score");
    if (score) score.textContent = String(judge.total);
  }

  /** 1 Subject の発表を始める前の初期化。中央は空、rail は全て未開示。 */
  function prepareSubject(refs, subject, judges) {
    refs.subjectName.textContent = subject.name;
    refs.eyebrow.textContent = "RESULT";
    buildRail(refs, judges);
    resetCenter(refs);
  }

  function resetCenter(refs) {
    refs.activeCard.dataset.state = "idle";
    refs.activeName.textContent = "";
    refs.activeScore.textContent = "";
    refs.total.dataset.state = "idle";
    refs.totalValue.textContent = "";
  }

  /** 中央カードを下からせり上げる。Judge名だけを見せ、点数はまだ出さない。 */
  function enterJudge(refs, judge, index) {
    var card = refs.activeCard;
    card.dataset.state = "idle";
    refs.activeName.textContent = judge.display_name;
    refs.activeScore.textContent = "";
    // 直前のsettleのtransitionをリセットしてから登場させる
    void card.offsetWidth;
    card.dataset.state = "enter";
    markSlot(refs, index, "active");
  }

  /** 点数だけを後から出す。glowが点くのはこの瞬間だけ。 */
  function revealJudgeScore(refs, judge) {
    refs.activeScore.textContent = String(judge.total);
    refs.activeCard.dataset.state = "revealed";
  }

  /** 中央カードを退場させ、同時に下部slotを開示済みへ更新する。 */
  function settleJudge(refs, judge, index) {
    refs.activeCard.dataset.state = "settle";
    fillSlot(refs, index, judge);
  }

  function clearCenter(refs) {
    refs.activeCard.dataset.state = "idle";
    refs.activeName.textContent = "";
    refs.activeScore.textContent = "";
  }

  /** TOTAL。全Judge開示後にのみ呼ばれる(running totalは存在しない)。 */
  function revealTotal(refs, totalScore) {
    refs.totalValue.textContent = String(totalScore);
    refs.total.dataset.state = "revealed";
  }

  /** Skip / reduced motion 用。この sequence の正しい最終状態を即座に作る。 */
  function showSubjectFinalState(refs, subject, judges) {
    buildRail(refs, judges);
    judges.forEach(function (judge, index) {
      fillSlot(refs, index, judge);
    });
    clearCenter(refs);
    revealTotal(refs, subject.total_score);
    refs.stage.dataset.phase = "TOTAL_VISIBLE";
  }

  var api = {
    UNREVEALED: UNREVEALED,
    buildRail: buildRail,
    prepareSubject: prepareSubject,
    resetCenter: resetCenter,
    enterJudge: enterJudge,
    revealJudgeScore: revealJudgeScore,
    settleJudge: settleJudge,
    clearCenter: clearCenter,
    revealTotal: revealTotal,
    showSubjectFinalState: showSubjectFinalState
  };

  root.PresentationStage = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
