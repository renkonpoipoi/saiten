/* Host Settings の操作可否を、実際に host_settings.js を走らせて検証する。

   Phase 10A-7 で報告された「ホスト兼任の採点者を削除した直後、しばらく
   select も新規採点者入力も触れなくなる」という不具合の再発防止。

   静的な文字列検査だけでは「削除後に本当に操作可能へ戻るか」を保証できないため、
   最小限のDOMスタブ上でスクリプトを実行し、DELETE 完了直後の disabled 状態を
   直接観測する。タイマーもpollingも使わないので実時間は待たない。 */
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const SETTINGS_JS = path.join(here, "../../app/static/js/host_settings.js");

const ELEMENT_IDS = [
  "projectNameHeading", "projectStatusBadge", "hostDashboardLink", "draftOnlyNotice",
  "projectNameInput", "projectNameSaveButton", "addSubjectButton", "newSubjectName",
  "addScorerButton", "newScorerName", "startScoringButton", "subjectRows",
  "criterionRows", "scorerRows", "hostScorerSection", "hostScorerSelect",
  "saveHostScorerButton", "hostScorerEmptyNote", "projectNameForm", "addSubjectForm",
  "addScorerForm", "regenerateHostCodeButton", "newHostCodeBox", "newHostCodeText",
  "copyNewHostCodeButton", "messageBox",
];

class El {
  constructor(tag) {
    this.tagName = (tag || "div").toUpperCase();
    this.children = []; this.style = {}; this.dataset = {}; this.attrs = {};
    this._text = ""; this.value = ""; this.disabled = false; this.title = "";
    this.type = this.tagName === "BUTTON" ? "submit" : "text";
    this._classes = new Set(); this.listeners = {};
    this.classList = {
      add: (...c) => c.forEach((x) => this._classes.add(x)),
      remove: (...c) => c.forEach((x) => this._classes.delete(x)),
      contains: (c) => this._classes.has(c),
      toggle: (c, force) => {
        if (force === undefined) this._classes.has(c) ? this._classes.delete(c) : this._classes.add(c);
        else if (force) this._classes.add(c); else this._classes.delete(c);
      },
    };
  }
  get className() { return [...this._classes].join(" "); }
  set className(v) { this._classes = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get textContent() { return this._text; }
  set textContent(v) { this._text = String(v); this.children = []; }
  set innerHTML(v) { if (v === "") this.children = []; }
  get innerHTML() { return ""; }
  get hidden() { return this._classes.has("hidden"); }
  appendChild(c) { this.children.push(c); return c; }
  append(...cs) { cs.forEach((c) => this.appendChild(c)); }
  prepend(c) { this.children.unshift(c); }
  addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); }
  async fire(t, ev = {}) {
    for (const fn of this.listeners[t] || []) await fn({ preventDefault() {}, ...ev });
  }
  toggleAttribute(k, force) { if (force) this.attrs[k] = ""; else delete this.attrs[k]; }
  buttons() { return this.children.filter((c) => c.tagName === "BUTTON"); }
  button(label) { return this.buttons().find((b) => b.textContent === label); }
}

function baseProject() {
  return {
    id: 1, name: "p", status: "DRAFT", allow_host_scoring: true,
    subjects: [{ id: 1, name: "チームA", sort_order: 0 }],
    criteria: [{ id: 1, name: "独創性", max_score: 20, sort_order: 0 }],
    scorers: [
      { id: 1, display_name: "吉田", is_host_scorer: false, is_active: true },
      { id: 2, display_name: "田中", is_host_scorer: true, is_active: true },
    ],
  };
}

/** host_settings.js を新しいDOMスタブ上で起動する。 */
async function boot({ failDelete = false } = {}) {
  const ids = {};
  ELEMENT_IDS.forEach((id) => { ids[id] = new El(id.includes("Select") ? "select" : "div"); });
  const calls = [];
  const toasts = [];
  let state = baseProject();

  globalThis.document = {
    getElementById: (id) => ids[id] ?? null,
    createElement: (t) => new El(t),
    querySelector: () => null,
    body: new El("body"),
    title: "",
  };
  globalThis.window = { PROJECT_ID: 1 };
  globalThis.confirm = () => true;
  globalThis.showMessage = (t) => toasts.push(t);
  globalThis.copyToClipboard = () => Promise.resolve();
  globalThis.apiFetch = async (url, opts = {}) => {
    const method = opts.method || "GET";
    calls.push(`${method} ${url}`);
    if (method === "DELETE") {
      if (failDelete) { const e = new Error("削除できませんでした"); e.status = 409; throw e; }
      const id = Number(url.split("/").pop());
      state = { ...state, allow_host_scoring: false,
                scorers: state.scorers.filter((s) => s.id !== id) };
      return null;
    }
    if (method === "PATCH" || method === "POST") return { code: "scr_x", host_code: "host_x" };
    return JSON.parse(JSON.stringify(state));
  };

  // eslint-disable-next-line no-eval
  eval(fs.readFileSync(SETTINGS_JS, "utf8"));
  await settle();
  return { ids, calls, toasts, state: () => state };
}

const settle = () => new Promise((r) => setTimeout(r, 0));

const editableState = (ids) => ({
  select: ids.hostScorerSelect.disabled,
  newScorerName: ids.newScorerName.disabled,
  addScorerButton: ids.addScorerButton.disabled,
  newSubjectName: ids.newSubjectName.disabled,
  addSubjectButton: ids.addSubjectButton.disabled,
});

const ALL_ENABLED = {
  select: false, newScorerName: false, addScorerButton: false,
  newSubjectName: false, addSubjectButton: false,
};

/* --------------------------------------------------------------------------
   削除直後に操作可能へ戻ること
   -------------------------------------------------------------------------- */

test("draft controls start enabled", async () => {
  const { ids } = await boot();
  assert.deepEqual(editableState(ids), ALL_ENABLED);
  assert.equal(ids.hostScorerSection.hidden, false);
});

test("deleting the host scorer leaves every draft control usable at once", async () => {
  const { ids } = await boot();
  const tanaka = ids.scorerRows.children[1];
  await tanaka.button("削除").fire("click");
  await settle();

  // ★ タイマーもpollingも待たず、この時点で全て操作可能であること
  assert.deepEqual(editableState(ids), ALL_ENABLED);
  assert.equal(document.body.dataset.busy, "false");
});

test("the host scorer select is repopulated with the remaining scorers", async () => {
  const { ids } = await boot();
  await ids.scorerRows.children[1].button("削除").fire("click");
  await settle();

  const options = ids.hostScorerSelect.children.map((o) => o.textContent);
  assert.deepEqual(options, ["(なし)", "吉田"]);
  assert.equal(ids.hostScorerSelect.disabled, false);
  assert.equal(ids.scorerRows.children.length, 1);
});

test("a deleted scorer disappears from the select options", async () => {
  const { ids } = await boot();
  await ids.scorerRows.children[1].button("削除").fire("click");
  await settle();
  assert.ok(!ids.hostScorerSelect.children.some((o) => o.textContent === "田中"));
});

test("an inactive scorer is never offered as a host scorer", async () => {
  const ids = {};
  ELEMENT_IDS.forEach((id) => { ids[id] = new El(id.includes("Select") ? "select" : "div"); });
  globalThis.document = {
    getElementById: (id) => ids[id] ?? null,
    createElement: (t) => new El(t),
    querySelector: () => null, body: new El("body"), title: "",
  };
  globalThis.window = { PROJECT_ID: 1 };
  globalThis.confirm = () => true;
  globalThis.showMessage = () => {};
  globalThis.copyToClipboard = () => Promise.resolve();
  const project = baseProject();
  project.scorers[1].is_active = false;
  globalThis.apiFetch = async () => JSON.parse(JSON.stringify(project));

  // eslint-disable-next-line no-eval
  eval(fs.readFileSync(SETTINGS_JS, "utf8"));
  await settle();
  assert.deepEqual(ids.hostScorerSelect.children.map((o) => o.textContent), ["(なし)", "吉田"]);
});

test("a failed delete also releases the lock", async () => {
  const { ids, toasts } = await boot({ failDelete: true });
  await ids.scorerRows.children[1].button("削除").fire("click");
  await settle();

  // ★ 失敗しても永続的なdisabledが残らない
  assert.deepEqual(editableState(ids), ALL_ENABLED);
  assert.equal(document.body.dataset.busy, "false");
  assert.ok(toasts.includes("削除できませんでした"));
  assert.equal(ids.scorerRows.children.length, 2, "失敗したので行は消えない");
});

test("re-enabling does not depend on any timer", async () => {
  const source = fs.readFileSync(SETTINGS_JS, "utf8");
  assert.ok(!source.includes("setTimeout"), "settings画面はtimerに依存しない");
  assert.ok(!source.includes("setInterval"), "settings画面はpollingしない");

  // 実際にDELETE直後(タイマーを一切進めない状態)で操作可能なこと
  const { ids } = await boot();
  await ids.scorerRows.children[1].button("削除").fire("click");
  await settle();
  assert.equal(ids.hostScorerSelect.disabled, false);
});

test("the reassignment flow works right after a delete", async () => {
  const { ids, calls } = await boot();
  await ids.scorerRows.children[1].button("削除").fire("click");
  await settle();

  ids.hostScorerSelect.value = "1";
  await ids.saveHostScorerButton.fire("click");
  await settle();

  assert.ok(calls.some((c) => c.startsWith("PATCH /api/projects/1/host-scorer")));
  assert.deepEqual(editableState(ids), ALL_ENABLED);
});

/* --------------------------------------------------------------------------
   busy lifecycle
   -------------------------------------------------------------------------- */

test("controls are locked while a mutation is in flight and released after", async () => {
  const { ids } = await boot();
  let release;
  const gate = new Promise((r) => { release = r; });
  const original = globalThis.apiFetch;
  globalThis.apiFetch = async (url, opts = {}) => {
    if ((opts.method || "GET") === "DELETE") { await gate; }
    return original(url, opts);
  };

  const pending = ids.scorerRows.children[1].button("削除").fire("click");
  await settle();
  // 実行中はlockされる(二重送信防止)
  assert.equal(ids.hostScorerSelect.disabled, true);
  assert.equal(ids.newScorerName.disabled, true);
  assert.equal(document.body.dataset.busy, "true");

  release();
  await pending;
  await settle();
  // finally で必ず解除される
  assert.deepEqual(editableState(ids), ALL_ENABLED);
  assert.equal(document.body.dataset.busy, "false");
  globalThis.apiFetch = original;
});

/* --------------------------------------------------------------------------
   ボタン文言
   -------------------------------------------------------------------------- */

test("scorer and subject rows say 名前を更新, not 保存", async () => {
  const { ids } = await boot();
  const scorerLabels = ids.scorerRows.children[0].buttons().map((b) => b.textContent);
  const subjectLabels = ids.subjectRows.children[0].buttons().map((b) => b.textContent);

  assert.deepEqual(scorerLabels, ["名前を更新", "削除", "コード再発行"]);
  assert.deepEqual(subjectLabels, ["名前を更新", "削除"]);
  assert.ok(!scorerLabels.includes("保存"));
  assert.ok(!subjectLabels.includes("保存"));
});

test("the host scorer section keeps 保存", async () => {
  const { ids } = await boot();
  // テンプレート側の文言なのでスクリプトは書き換えない
  assert.equal(ids.saveHostScorerButton.textContent, "");
  const source = fs.readFileSync(SETTINGS_JS, "utf8");
  const start = source.indexOf('document.getElementById("saveHostScorerButton").addEventListener');
  assert.ok(start > 0, "保存ハンドラが見つからない");
  const handler = source.slice(start, source.indexOf("\n  });", start));
  assert.ok(!handler.includes("名前を更新"), "ホスト兼任の保存は文言を変えない");
  assert.ok(handler.includes("/host-scorer"));
});

test("row buttons are type=button so they never submit a form", async () => {
  const { ids } = await boot();
  for (const row of [...ids.scorerRows.children, ...ids.subjectRows.children]) {
    for (const button of row.buttons()) {
      assert.equal(button.type, "button", button.textContent);
    }
  }
});

/* --------------------------------------------------------------------------
   dirty state (名前を更新は変更したときだけ押せる)
   -------------------------------------------------------------------------- */

test("the update button enables only after the name changes", async () => {
  const { ids } = await boot();
  const row = ids.scorerRows.children[0];
  const input = row.children.find((c) => c.tagName === "INPUT");
  const update = row.button("名前を更新");

  assert.equal(update.disabled, true, "初期状態は押せない");
  input.value = "吉田さん";
  await input.fire("input");
  assert.equal(update.disabled, false, "変更したら押せる");
  input.value = "吉田";
  await input.fire("input");
  assert.equal(update.disabled, true, "元に戻したら再び押せない");
});

test("delete stays clickable regardless of the dirty state", async () => {
  const { ids } = await boot();
  const row = ids.scorerRows.children[0];
  assert.equal(row.button("削除").disabled, false);
  assert.equal(row.button("コード再発行").disabled, false);
});
