/* presentation/stage.js を最小限のDOMスタブ上で実際に走らせる。

   Phase 9E の要:
   - Incoming Score Card は **ランキングの外** から始まり、staging中は順位を
     持たない(順位を入れる要素がDOMに存在しない)。
   - 順位が表示されるのは、確定slotへ到着した瞬間だけ。しかもサーバー値。
   - Judge score tier は中央Revealだけでなく、settle後のrailにも残る。

   実時間もタイマーも使わない。DOMの最終状態を直接観測する。 */
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));

class El {
  constructor(tag) {
    this.tagName = (tag || "div").toUpperCase();
    this.children = [];
    this.dataset = {};
    this._text = "";
    this._classes = new Set();
    this.offsetWidth = 0;
    this._rect = { top: 0, left: 0, width: 100, height: 20 };
    this.style = {
      setProperty: (name, value) => { this.style[name] = value; },
      transition: "",
      transform: "",
    };
  }
  get className() { return [...this._classes].join(" "); }
  set className(v) { this._classes = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get textContent() { return this._text; }
  set textContent(v) { this._text = String(v); this.children = []; }
  set innerHTML(v) { if (v === "") this.children = []; }
  get innerHTML() { return ""; }
  appendChild(c) { this.children.push(c); return c; }
  append(...cs) { cs.forEach((c) => this.appendChild(c)); }
  replaceChild(next, previous) {
    const index = this.children.indexOf(previous);
    if (index >= 0) this.children[index] = next;
    else this.children.push(next);
    return previous;
  }
  setRect(rect) { this._rect = Object.assign({}, this._rect, rect); return this; }
  getBoundingClientRect() { return this._rect; }
  matches(selector) {
    if (selector.startsWith(".")) return this._classes.has(selector.slice(1));
    const attr = selector.match(/^\[data-([a-z-]+)="(.*)"\]$/);
    if (attr) {
      const key = attr[1].replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      return this.dataset[key] === attr[2];
    }
    return false;
  }
  querySelector(selector) {
    for (const child of this.children) {
      if (child.matches && child.matches(selector)) return child;
      const nested = child.querySelector ? child.querySelector(selector) : null;
      if (nested) return nested;
    }
    return null;
  }
}

globalThis.document = { createElement: (tag) => new El(tag) };
const core = require(path.join(here, "../../app/static/js/presentation/core.js"));
const stage = require(path.join(here, "../../app/static/js/presentation/stage.js"));

function refs() {
  return {
    stage: new El(),
    eyebrow: new El(),
    subjectName: new El(),
    rail: new El(),
    activeCard: new El(),
    activeName: new El(),
    activeScore: new El(),
    total: new El(),
    totalValue: new El(),
    rankingRest: new El(),
    rankingTop: new El(),
    rankingTitle: new El(),
    incomingCard: new El(),
    incomingName: new El(),
    incomingScore: new El(),
  };
}

const SUBJECTS = [
  { id: 1, name: "A", total_score: 240, rank: 1, sort_order: 0 },
  { id: 2, name: "B", total_score: 200, rank: 2, sort_order: 1 },
  { id: 3, name: "C", total_score: 194, rank: 3, sort_order: 2 },
];

const rowText = (row) => row.children.map((c) => c.textContent);

/* --------------------------------------------------------------------------
   Incoming Score Card
   -------------------------------------------------------------------------- */

test("the incoming card shows only the name and the total score", () => {
  const r = refs();
  stage.showIncomingCard(r, { id: 3, name: "チームC", total_score: 194 });
  assert.equal(r.incomingName.textContent, "チームC");
  assert.equal(r.incomingScore.textContent, "194");
  assert.equal(r.incomingCard.dataset.state, "staging");
});

test("the incoming card has no rank element to show a rank in", () => {
  // 「第1位が第3位の下に浮く」状態は、DOM構造として作れない
  const html = require("node:fs").readFileSync(
    path.join(here, "../../app/templates/result_presentation.html"), "utf-8"
  );
  const card = html.slice(html.indexOf('id="incomingCard"'), html.indexOf('</div>\n  </div>', html.indexOf('id="incomingCard"')));
  assert.ok(card.includes("incomingName"));
  assert.ok(card.includes("incomingScore"));
  assert.ok(!card.includes("rank"), card);
});

test("the placeholder reserves the slot without carrying a rank", () => {
  const row = stage.buildPlaceholderRow(SUBJECTS[2]);
  assert.equal(row.dataset.placeholder, "true");
  assert.equal(row.dataset.subjectId, "3");
  assert.equal(row.dataset.rank, undefined);
  assert.deepEqual(rowText(row), ["", "C", "194"]);
});

test("a normal ranking row does carry the server rank", () => {
  const groups = core.toRankGroups(SUBJECTS);
  const row = stage.buildRankingRow(SUBJECTS[2], groups);
  assert.equal(row.dataset.rank, "3");
  assert.deepEqual(rowText(row), ["第3位", "C", "194"]);
});

test("moving the card measures the gap between staging and the final slot", () => {
  const r = refs();
  r.incomingCard.setRect({ top: 600, left: 300, width: 400, height: 80 });
  // 中心どうしの差分を埋め、着地slotに合わせて縮む
  const target = new El().setRect({ top: 200, left: 100, width: 200, height: 40 });
  stage.moveIncomingToSlot(r, target, 750);
  assert.equal(r.incomingCard.dataset.state, "moving");
  assert.match(r.incomingCard.style.transform, /^translate\(-300px,-420px\) scale\(0\.5\)$/);
  assert.match(r.incomingCard.style.transition, /750ms/);
});

test("moving without a target slot changes nothing", () => {
  const r = refs();
  assert.equal(stage.moveIncomingToSlot(r, null, 750), null);
  assert.equal(r.incomingCard.dataset.state, undefined);
});

test("arrival replaces the placeholder with the server ranked row", () => {
  const r = refs();
  const groups = core.toRankGroups(SUBJECTS);
  r.rankingRest.append(
    stage.buildRankingRow(SUBJECTS[0], groups),
    stage.buildRankingRow(SUBJECTS[1], groups),
    stage.buildPlaceholderRow(SUBJECTS[2])
  );
  stage.showIncomingCard(r, SUBJECTS[2]);

  const row = stage.normalizeIncomingRow(r, SUBJECTS[2], groups);

  assert.equal(r.rankingRest.children.length, 3);
  assert.equal(r.rankingRest.children[2], row);
  assert.equal(row.dataset.rank, "3");
  assert.equal(row.dataset.highlight, "true");
  assert.deepEqual(rowText(row), ["第3位", "C", "194"]);
  // 到着と同時に staging のカードは引っ込む
  assert.equal(r.incomingCard.dataset.state, "idle");
  assert.equal(r.rankingRest.querySelector('[data-placeholder="true"]'), null);
});

test("arrival marks the winner row so the top row can be highlighted", () => {
  const r = refs();
  const groups = core.toRankGroups(SUBJECTS);
  r.rankingRest.append(stage.buildPlaceholderRow(SUBJECTS[0]));
  const row = stage.normalizeIncomingRow(r, SUBJECTS[0], groups);
  assert.equal(row.dataset.top, "true");
  assert.equal(row.dataset.rank, "1");
});

test("arrival into a tie group uses the server label, not a recomputed one", () => {
  const tied = [
    { id: 1, name: "A", total_score: 240, rank: 1, sort_order: 0 },
    { id: 2, name: "B", total_score: 240, rank: 1, sort_order: 1 },
    { id: 3, name: "C", total_score: 194, rank: 3, sort_order: 2 },
  ];
  const r = refs();
  r.rankingRest.append(stage.buildPlaceholderRow(tied[1]));
  const row = stage.normalizeIncomingRow(r, tied[1], core.toRankGroups(tied));
  assert.deepEqual(rowText(row), ["同率1位", "B", "240"]);
});

test("arrival still works if the placeholder is gone (skip raced ahead)", () => {
  const r = refs();
  const row = stage.normalizeIncomingRow(r, SUBJECTS[2], core.toRankGroups(SUBJECTS));
  assert.equal(r.rankingRest.children.length, 1);
  assert.equal(row.dataset.rank, "3");
});

test("hiding the incoming card clears the inline move transform", () => {
  const r = refs();
  r.incomingCard.setRect({ top: 10, left: 10, width: 100, height: 20 });
  stage.moveIncomingToSlot(r, new El().setRect({ top: 0, left: 0, width: 100, height: 20 }), 750);
  stage.hideIncomingCard(r);
  assert.equal(r.incomingCard.style.transform, "");
  assert.equal(r.incomingCard.style.transition, "");
  assert.equal(r.incomingCard.dataset.state, "idle");
});

/* --------------------------------------------------------------------------
   Judge score tier: 中央Reveal と settle後のrail
   -------------------------------------------------------------------------- */

const JUDGES = [
  { scorer_id: 1, display_name: "田中", total: 68 },
  { scorer_id: 2, display_name: "吉田", total: 76 },
  { scorer_id: 3, display_name: "佐藤", total: 84 },
  { scorer_id: 4, display_name: "鈴木", total: 93 },
];

const slotTiers = (r) => r.rail.children.map((slot) => slot.dataset.tier);
const slotScores = (r) =>
  r.rail.children.map((slot) => slot.querySelector(".p-slot__score").textContent);

test("the central card takes the tier at the moment the score is revealed", () => {
  const r = refs();
  stage.buildRail(r, JUDGES);
  stage.enterJudge(r, JUDGES[3], 3);
  assert.equal(r.activeCard.dataset.tier, undefined, "登場時点ではtierを持たない");

  stage.revealJudgeScore(r, JUDGES[3]);
  assert.equal(r.activeCard.dataset.tier, "premium");
  assert.equal(r.activeCard.dataset.state, "revealed");
  assert.equal(r.activeScore.textContent, "93");
});

test("every tier reaches the central card", () => {
  const r = refs();
  const expected = ["standard", "silver", "gold", "premium"];
  JUDGES.forEach((judge, index) => {
    stage.revealJudgeScore(r, judge);
    assert.equal(r.activeCard.dataset.tier, expected[index], judge.display_name);
  });
});

test("the tier stays on the rail after the judge settles", () => {
  const r = refs();
  stage.buildRail(r, JUDGES);
  JUDGES.forEach((judge, index) => {
    stage.revealJudgeScore(r, judge);
    stage.settleJudge(r, judge, index);
  });
  assert.deepEqual(slotTiers(r), ["standard", "silver", "gold", "premium"]);
  assert.deepEqual(slotScores(r), ["68", "76", "84", "93"]);
});

test("clearing the centre drops the tier but the rail keeps it", () => {
  const r = refs();
  stage.buildRail(r, JUDGES);
  stage.revealJudgeScore(r, JUDGES[3]);
  stage.settleJudge(r, JUDGES[3], 3);
  stage.clearCenter(r);
  assert.equal(r.activeCard.dataset.tier, undefined);
  assert.equal(r.rail.children[3].dataset.tier, "premium");
});

test("the skip final state still carries every rail tier", () => {
  const r = refs();
  stage.showSubjectFinalState(r, { name: "チームA", total_score: 321 }, JUDGES);
  assert.deepEqual(slotTiers(r), ["standard", "silver", "gold", "premium"]);
  assert.equal(r.totalValue.textContent, "321");
  assert.equal(r.activeCard.dataset.tier, undefined);
});

test("unrevealed rail slots carry no tier at all", () => {
  const r = refs();
  stage.buildRail(r, JUDGES);
  assert.deepEqual(slotTiers(r), [undefined, undefined, undefined, undefined]);
  assert.deepEqual(slotScores(r), ["—", "—", "—", "—"]);
});

/* --------------------------------------------------------------------------
   TOTAL は得点に関係なく固定
   -------------------------------------------------------------------------- */

test("the total looks identical for a low and a high total", () => {
  const low = refs();
  const high = refs();
  stage.revealTotal(low, 250);
  stage.revealTotal(high, 450);
  assert.equal(low.total.dataset.state, high.total.dataset.state);
  assert.equal(low.total.dataset.tier, undefined);
  assert.equal(high.total.dataset.tier, undefined);
  assert.equal(low.totalValue.textContent, "250");
  assert.equal(high.totalValue.textContent, "450");
});

test("the total never borrows a judge tier", () => {
  const r = refs();
  for (const total of [0, 69, 70, 90, 100, 194, 500]) {
    stage.revealTotal(r, total);
    assert.equal(r.total.dataset.tier, undefined, String(total));
    assert.equal(r.total.dataset.state, "revealed");
  }
});
