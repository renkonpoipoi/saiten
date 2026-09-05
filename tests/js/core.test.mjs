/* Presentation engine の純粋関数のユニットテスト。
   node --test tests/js で実行する(pytest からは test_phase9_js_unit.py 経由)。

   ここでは実時間を一切待たない。演出の正しさを
   「step descriptor 配列の順序・尺・分岐」として検証することで、
   タイマー待ちに依存する脆いテストを避けている。 */
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const core = require(path.join(here, "../../app/static/js/presentation/core.js"));

const phases = (steps) => steps.map((s) => s.phase);

/* --------------------------------------------------------------------------
   judgeScale: Judge区間の総尺に予算を置き、下限で床を張る
   -------------------------------------------------------------------------- */

test("judgeScale keeps small panels at full speed", () => {
  for (const n of [1, 2, 3, 5, 8]) {
    assert.equal(core.judgeScale(n), 1, `n=${n}`);
  }
});

test("judgeScale compresses large panels but never below the floor", () => {
  assert.ok(core.judgeScale(13) < 1);
  assert.ok(core.judgeScale(13) > 0.55);
  assert.equal(core.judgeScale(40), 0.55);
  assert.equal(core.judgeScale(400), 0.55);
});

test("judgeScale is monotonically non-increasing in judge count", () => {
  let previous = Infinity;
  for (let n = 1; n <= 40; n += 1) {
    const scale = core.judgeScale(n);
    assert.ok(scale <= previous, `n=${n}`);
    previous = scale;
  }
});

test("judgeScale tolerates degenerate input", () => {
  assert.equal(core.judgeScale(0), 1);
  assert.equal(core.judgeScale(undefined), 1);
});

test("scaledJudgeTiming never drops below the readability floors", () => {
  for (const n of [9, 13, 20, 40, 200]) {
    const t = core.scaledJudgeTiming(n);
    assert.ok(t.judgeEnter >= core.JUDGE_FLOORS.judgeEnter, `enter n=${n}`);
    assert.ok(t.judgeScore >= core.JUDGE_FLOORS.judgeScore, `score n=${n}`);
    assert.ok(t.judgeVisible >= core.JUDGE_FLOORS.judgeVisible, `visible n=${n}`);
    assert.ok(t.judgeSettle >= core.JUDGE_FLOORS.judgeSettle, `settle n=${n}`);
  }
});

test("PER_JUDGE_BASE_MS matches the sum of the per-judge phases", () => {
  const t = core.TIMING;
  const sum = t.judgeEnter + t.judgeHold + t.judgeScore
    + t.judgeVisible + t.judgeSettle + t.judgeGap;
  assert.equal(sum, core.PER_JUDGE_BASE_MS);
});

/* --------------------------------------------------------------------------
   buildSubjectSteps: Judge全員 -> TOTAL。running total は存在しない。
   -------------------------------------------------------------------------- */

test("buildSubjectSteps reveals every judge and only then the total", () => {
  const steps = buildFor(3);
  assert.deepEqual(phases(steps), [
    "SUBJECT_INTRO",
    "JUDGE_ENTER", "JUDGE_HOLD", "JUDGE_SCORE", "JUDGE_VISIBLE", "JUDGE_SETTLE", "JUDGE_GAP",
    "JUDGE_ENTER", "JUDGE_HOLD", "JUDGE_SCORE", "JUDGE_VISIBLE", "JUDGE_SETTLE", "JUDGE_GAP",
    "JUDGE_ENTER", "JUDGE_HOLD", "JUDGE_SCORE", "JUDGE_VISIBLE", "JUDGE_SETTLE",
    "ALL_SETTLED", "TOTAL_ENTER", "TOTAL_VISIBLE", "SUBJECT_EXIT",
  ]);

  function buildFor(n) { return core.buildSubjectSteps(n); }
});

test("buildSubjectSteps emits no total phase before the last judge settles", () => {
  const steps = core.buildSubjectSteps(5);
  const lastJudge = steps.map((s) => s.phase).lastIndexOf("JUDGE_SETTLE");
  const firstTotal = steps.findIndex((s) => s.phase.startsWith("TOTAL_"));
  assert.ok(firstTotal > lastJudge);
  assert.equal(steps.filter((s) => s.phase === "TOTAL_ENTER").length, 1);
});

test("buildSubjectSteps indexes judges in order and omits a trailing gap", () => {
  const steps = core.buildSubjectSteps(4);
  const enters = steps.filter((s) => s.phase === "JUDGE_ENTER").map((s) => s.index);
  assert.deepEqual(enters, [0, 1, 2, 3]);
  assert.equal(steps.filter((s) => s.phase === "JUDGE_GAP").length, 3);
});

test("buildSubjectSteps handles a single judge", () => {
  const steps = core.buildSubjectSteps(1);
  assert.equal(steps.filter((s) => s.phase === "JUDGE_GAP").length, 0);
  assert.equal(steps.filter((s) => s.phase === "JUDGE_ENTER").length, 1);
  assert.ok(phases(steps).includes("TOTAL_ENTER"));
});

test("buildSubjectSteps still reaches the total with zero judges", () => {
  assert.deepEqual(phases(core.buildSubjectSteps(0)), [
    "SUBJECT_INTRO", "ALL_SETTLED", "TOTAL_ENTER", "TOTAL_VISIBLE", "SUBJECT_EXIT",
  ]);
});

test("judge segment stays inside the pacing budget for large panels", () => {
  const judgeSegment = (n) => core.buildSubjectSteps(n)
    .filter((s) => s.phase.startsWith("JUDGE_"))
    .reduce((sum, s) => sum + s.duration, 0);

  assert.ok(judgeSegment(3) < 10000, "3 judges should stay brisk");
  assert.ok(judgeSegment(13) <= 27000, `13 judges: ${judgeSegment(13)}ms`);
  assert.ok(judgeSegment(13) >= 20000, "13 judges should still feel deliberate");
});

test("a subject reveal is long enough to feel like an event", () => {
  assert.ok(core.totalDuration(core.buildSubjectSteps(3)) > 10000);
});

/* --------------------------------------------------------------------------
   railLayout: 1-5 / 6-9 / 10-13 を重点設計、14+ は安全に退避
   -------------------------------------------------------------------------- */

test("railLayout uses a single row up to nine judges", () => {
  assert.deepEqual(core.railLayout(3), { rows: 1, cols: 3, slotScale: 3.2 });
  assert.deepEqual(core.railLayout(5), { rows: 1, cols: 5, slotScale: 3.2 });
  assert.deepEqual(core.railLayout(6), { rows: 1, cols: 6, slotScale: 2.4 });
  assert.deepEqual(core.railLayout(9), { rows: 1, cols: 9, slotScale: 2.4 });
});

test("railLayout switches to two rows for the 10-13 main case", () => {
  assert.deepEqual(core.railLayout(10), { rows: 2, cols: 5, slotScale: 1.9 });
  assert.deepEqual(core.railLayout(13), { rows: 2, cols: 7, slotScale: 1.9 });
});

test("railLayout degrades gracefully beyond the designed range", () => {
  const twenty = core.railLayout(20);
  assert.equal(twenty.rows, 2);
  assert.equal(twenty.cols, 10);
  assert.ok(twenty.slotScale >= 1.2 && twenty.slotScale < 1.9);

  const many = core.railLayout(30);
  assert.equal(many.rows, 3);
  assert.equal(many.cols, 10);
  assert.ok(many.slotScale >= 1.0 && many.slotScale <= 1.6);
});

test("railLayout always covers every judge and never returns zero columns", () => {
  for (let n = 1; n <= 60; n += 1) {
    const layout = core.railLayout(n);
    assert.ok(layout.cols >= 1, `n=${n}`);
    assert.ok(layout.rows * layout.cols >= n, `n=${n}`);
    assert.ok(layout.slotScale > 0, `n=${n}`);
  }
});

/* --------------------------------------------------------------------------
   rank group: competition ranking(1,1,3 / 1,2,2,4)を壊さない
   -------------------------------------------------------------------------- */

const subject = (name, rank, order) => ({ name, rank, sort_order: order, total_score: 0 });

test("toRankGroups groups ties and marks them", () => {
  const groups = core.toRankGroups([
    subject("A", 1, 0), subject("B", 1, 1), subject("C", 3, 2),
  ]);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].rank, 1);
  assert.equal(groups[0].tied, true);
  assert.deepEqual(groups[0].subjects.map((s) => s.name), ["A", "B"]);
  assert.equal(groups[1].rank, 3);
  assert.equal(groups[1].tied, false);
});

test("toRankGroups keeps 1,2,2,4 intact", () => {
  const groups = core.toRankGroups([
    subject("D", 4, 3), subject("B", 2, 1), subject("A", 1, 0), subject("C", 2, 2),
  ]);
  assert.deepEqual(groups.map((g) => g.rank), [1, 2, 4]);
  assert.deepEqual(groups.map((g) => g.tied), [false, true, false]);
  assert.deepEqual(groups[1].subjects.map((s) => s.name), ["B", "C"]);
});

test("toRankGroups orders ties by sort_order, not input order", () => {
  const groups = core.toRankGroups([subject("Z", 1, 5), subject("A", 1, 2)]);
  assert.deepEqual(groups[0].subjects.map((s) => s.name), ["A", "Z"]);
});

test("revealOrder goes from the lowest rank group upwards", () => {
  const groups = core.toRankGroups([
    subject("A", 1, 0), subject("B", 2, 1), subject("C", 2, 2), subject("D", 4, 3),
  ]);
  assert.deepEqual(core.revealOrder(groups).map((g) => g.rank), [4, 2, 1]);
});

test("revealOrder does not mutate its input", () => {
  const groups = core.toRankGroups([subject("A", 1, 0), subject("B", 2, 1)]);
  core.revealOrder(groups);
  assert.deepEqual(groups.map((g) => g.rank), [1, 2]);
});

test("groupLabel distinguishes solo ranks from ties", () => {
  assert.equal(core.groupLabel({ rank: 1, tied: false }), "第1位");
  assert.equal(core.groupLabel({ rank: 1, tied: true }), "同率1位");
  assert.equal(core.groupLabel({ rank: 2, tied: true }), "同率2位");
  assert.equal(core.groupLabel({ rank: 4, tied: false }), "第4位");
});

test("a 1,1,3 board never produces a second place", () => {
  const groups = core.toRankGroups([
    subject("A", 1, 0), subject("B", 1, 1), subject("C", 3, 2),
  ]);
  assert.ok(!groups.some((g) => g.rank === 2));
  assert.deepEqual(core.revealOrder(groups).map(core.groupLabel), ["第3位", "同率1位"]);
});

/* --------------------------------------------------------------------------
   finalLayout: Subject数と同率の広がりでレイアウトを決める
   -------------------------------------------------------------------------- */

const board = (ranks) => ranks.map((rank, i) => subject(`S${i}`, rank, i));

test("finalLayout uses hero for very small fields", () => {
  assert.equal(core.finalLayout(board([1]), []), "hero");
  assert.equal(core.finalLayout(board([1, 2]), []), "hero");
  assert.equal(core.finalLayout(board([1, 2, 3]), []), "hero");
});

test("finalLayout uses a single column for mid-sized fields", () => {
  assert.equal(core.finalLayout(board([1, 2, 3, 4]), []), "column");
  assert.equal(core.finalLayout(board([1, 2, 3, 4, 5, 6]), []), "column");
});

test("finalLayout splits top3 and the rest from seven subjects up", () => {
  assert.equal(core.finalLayout(board([1, 2, 3, 4, 5, 6, 7]), []), "split");
  assert.equal(core.finalLayout(board([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]), []), "split");
});

test("finalLayout keeps split when a tie makes top3 four cards", () => {
  // 1,2,3,3 -> rank<=3 が4件。split のまま扱える上限。
  assert.equal(core.finalLayout(board([1, 2, 3, 3, 5, 6, 7]), []), "split");
});

test("finalLayout falls back to column when ties overflow the top area", () => {
  // 1,1,1,1,1,6,7 -> rank<=3 が5件。split では破綻するので退避する。
  assert.equal(core.finalLayout(board([1, 1, 1, 1, 1, 6, 7]), []), "column");
});

/* --------------------------------------------------------------------------
   Final Ranking / Sequential ranking の step 構成
   -------------------------------------------------------------------------- */

test("buildBatchFinalSteps stacks lower ranks then slows down for the top", () => {
  const groups = core.toRankGroups(board([1, 2, 3, 4, 5]));
  const steps = core.buildBatchFinalSteps(groups);
  assert.deepEqual(phases(steps), [
    "FINAL_INTRO",
    "QUICK_GROUP", "QUICK_GROUP",
    "TOP_PAUSE",
    "TOP_GROUP_ENTER", "TOP_GROUP_VISIBLE", "TOP_GROUP_SETTLE",
    "TOP_GROUP_ENTER", "TOP_GROUP_VISIBLE", "TOP_GROUP_SETTLE",
    "WINNER_HOLD", "WINNER_REVEAL", "WINNER_SETTLE",
    "COMPLETE",
  ]);
  const quick = steps.filter((s) => s.phase === "QUICK_GROUP").map((s) => s.rank);
  assert.deepEqual(quick, [5, 4], "lower ranks stack from the bottom up");
});

test("buildBatchFinalSteps reveals a tied first place exactly once", () => {
  const groups = core.toRankGroups(board([1, 1, 3]));
  const steps = core.buildBatchFinalSteps(groups);
  assert.deepEqual(phases(steps), [
    "FINAL_INTRO", "TOP_PAUSE",
    "TOP_GROUP_ENTER", "TOP_GROUP_VISIBLE", "TOP_GROUP_SETTLE",
    "WINNER_HOLD", "WINNER_REVEAL", "WINNER_SETTLE",
    "COMPLETE",
  ]);
  assert.equal(steps.filter((s) => s.phase === "WINNER_REVEAL").length, 1);
  const top = steps.find((s) => s.phase === "TOP_GROUP_ENTER");
  assert.equal(top.rank, 3, "the only non-winner top group is 3rd");
});

test("buildBatchFinalSteps handles 1,2,2,4 without inventing a third place", () => {
  const groups = core.toRankGroups(board([1, 2, 2, 4]));
  const steps = core.buildBatchFinalSteps(groups);
  assert.deepEqual(steps.filter((s) => s.phase === "QUICK_GROUP").map((s) => s.rank), [4]);
  assert.deepEqual(steps.filter((s) => s.phase === "TOP_GROUP_ENTER").map((s) => s.rank), [2]);
  assert.equal(steps.filter((s) => s.phase === "WINNER_REVEAL").length, 1);
});

test("buildBatchFinalSteps builds the winner beat even for a single subject", () => {
  const steps = core.buildBatchFinalSteps(core.toRankGroups(board([1])));
  assert.deepEqual(phases(steps), [
    "FINAL_INTRO", "TOP_PAUSE", "WINNER_HOLD", "WINNER_REVEAL", "WINNER_SETTLE", "COMPLETE",
  ]);
});

test("buildBatchFinalSteps keeps the lower stack quick and the winner slow", () => {
  const steps = core.buildBatchFinalSteps(core.toRankGroups(board([1, 2, 3, 4, 5, 6, 7])));
  const quick = steps.find((s) => s.phase === "QUICK_GROUP");
  const winnerHold = steps.find((s) => s.phase === "WINNER_HOLD");
  assert.ok(quick.duration <= 500);
  assert.ok(winnerHold.duration >= 1500);
});

test("buildSequentialRankSteps stops at the ranking for a normal subject", () => {
  assert.deepEqual(phases(core.buildSequentialRankSteps(false)), [
    "RANK_INSERT", "RANK_HOLD", "RANK_MOVE", "RANK_SETTLED", "COMPLETE",
  ]);
});

test("buildSequentialRankSteps promotes the last subject to the final ranking", () => {
  assert.deepEqual(phases(core.buildSequentialRankSteps(true)), [
    "RANK_INSERT", "RANK_HOLD", "RANK_MOVE", "RANK_SETTLED",
    "FINAL_TITLE", "WINNER_GLOW", "COMPLETE",
  ]);
});

/* --------------------------------------------------------------------------
   timing -> CSS custom property
   -------------------------------------------------------------------------- */

test("timingCssVars mirrors the scaled judge timing", () => {
  const vars = core.timingCssVars(13);
  const scaled = core.scaledJudgeTiming(13);
  assert.equal(vars["--t-judge-enter"], `${scaled.judgeEnter}ms`);
  assert.equal(vars["--t-judge-visible"], `${scaled.judgeVisible}ms`);
});

test("timingCssVars emits only ms values on custom property names", () => {
  for (const [name, value] of Object.entries(core.timingCssVars(5))) {
    assert.ok(name.startsWith("--t-"), name);
    assert.match(value, /^\d+ms$/, `${name}=${value}`);
  }
});

/* --------------------------------------------------------------------------
   Phase 10B: 発表順の抽選演出
   演出は「切り替えて止まる」だけで、**結果を決めない**ことを固定する。
   -------------------------------------------------------------------------- */

test("buildDrawSteps runs intro -> spin -> settle -> result -> complete", () => {
  assert.deepEqual(phases(core.buildDrawSteps()), [
    "DRAW_INTRO",
    "DRAW_SPIN",
    "DRAW_SETTLE",
    "DRAW_RESULT",
    "DRAW_COMPLETE",
  ]);
});

test("buildDrawSteps ends on a zero-duration completion step", () => {
  const steps = core.buildDrawSteps();
  assert.equal(steps[steps.length - 1].duration, 0);
  steps.slice(0, -1).forEach((step) => assert.ok(step.duration > 0, step.phase));
});

test("buildDrawSteps carries no subject, index or result field", () => {
  // 結果を step 側に埋め込めてしまうと「演出が結果を決める」構造になる。
  core.buildDrawSteps().forEach((step) => {
    assert.deepEqual(Object.keys(step).sort(), ["duration", "phase"]);
  });
});

test("buildDrawSteps is deterministic and independent of any randomness", () => {
  assert.deepEqual(core.buildDrawSteps(), core.buildDrawSteps());
});

test("buildDrawSteps honours an injected timing table", () => {
  const steps = core.buildDrawSteps({
    drawIntro: 10,
    drawSpin: 20,
    drawSettle: 30,
    drawHold: 40,
  });
  assert.deepEqual(steps.map((s) => s.duration), [10, 20, 30, 40, 0]);
});

test("drawSpinIntervals slows down and covers the spin duration", () => {
  const intervals = core.drawSpinIntervals();
  assert.ok(intervals.length > 1);
  for (let i = 1; i < intervals.length; i += 1) {
    assert.ok(intervals[i] >= intervals[i - 1], `interval ${i}`);
  }
  const total = intervals.reduce((sum, ms) => sum + ms, 0);
  assert.ok(total >= core.TIMING.drawSpin);
});

test("remainingCandidates removes every already drawn subject", () => {
  assert.deepEqual(
    core.remainingCandidates(["A", "B", "C", "D"], ["C", "A"]),
    ["B", "D"]
  );
});

test("remainingCandidates keeps every subject before the first draw", () => {
  assert.deepEqual(core.remainingCandidates(["A", "B", "C"], []), ["A", "B", "C"]);
});

test("remainingCandidates returns nothing once all subjects are drawn", () => {
  assert.deepEqual(core.remainingCandidates(["A", "B"], ["B", "A"]), []);
});

test("remainingCandidates preserves the display order it was given", () => {
  // 候補の並びが秘密順に見えてはいけないので、渡された順(sort_order)を保つ
  assert.deepEqual(core.remainingCandidates(["D", "C", "B", "A"], ["C"]), ["D", "B", "A"]);
});

test("remainingCandidates tolerates missing arguments", () => {
  assert.deepEqual(core.remainingCandidates(), []);
  assert.deepEqual(core.remainingCandidates(["A"], undefined), ["A"]);
});
