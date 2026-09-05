/* presentation/audio.js を、偽の Web Audio / Audio 要素の上で実際に走らせる。

   Phase 9E の要:
   - asset があれば asset、無ければ Web Audio で合成する(hybrid)
   - asset の再生に失敗しても合成音へ落ちる。無音にはするが例外は投げない
   - 音が鳴らない環境でも play() は false を返すだけで、例外も停止も起こさない
   - AudioContext は1つだけを使い回し、鳴り終えた node は解放する
   - 強さの序列: judgeStandard < Silver < Gold < Premium < totalSting < winnerSting

   実時間は待たない。スケジュールされた値と node の状態を直接観測する。 */
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const AUDIO_JS = path.join(here, "../../app/static/js/presentation/audio.js");

class FakeParam {
  constructor() { this.events = []; this.value = 0; }
  setValueAtTime(v, t) { this.events.push(["set", v, t]); return this; }
  exponentialRampToValueAtTime(v, t) { this.events.push(["ramp", v, t]); return this; }
  cancelScheduledValues(t) { this.events.push(["cancel", t]); return this; }
  get peak() {
    return this.events.filter((e) => e[0] === "ramp").reduce((m, e) => Math.max(m, e[1]), 0);
  }
}

class FakeNode {
  constructor() { this.connected = []; this.disconnected = false; }
  connect(target) { this.connected.push(target); return target; }
  disconnect() { this.disconnected = true; }
}

class FakeOscillator extends FakeNode {
  constructor() { super(); this.frequency = new FakeParam(); this.type = "sine"; }
  start(t) { this.startedAt = t; }
  stop(t) { this.stoppedAt = t; }
  end() { if (this.onended) this.onended(); }
}

class FakeGain extends FakeNode {
  constructor() { super(); this.gain = new FakeParam(); }
}

class FakeAudioContext {
  constructor() {
    this.currentTime = 10;
    this.state = "suspended";
    this.destination = new FakeNode();
    this.oscillators = [];
    this.gains = [];
    this.resumed = 0;
    FakeAudioContext.instances.push(this);
  }
  createOscillator() { const o = new FakeOscillator(); this.oscillators.push(o); return o; }
  createGain() { const g = new FakeGain(); this.gains.push(g); return g; }
  resume() { this.resumed += 1; this.state = "running"; return Promise.resolve(); }
}
FakeAudioContext.instances = [];

class FakeAudio {
  constructor(src) {
    this.src = src;
    this.currentTime = 0;
    this.plays = 0;
    this.paused = 0;
    FakeAudio.instances.push(this);
  }
  play() {
    this.plays += 1;
    return FakeAudio.rejectPlay ? Promise.reject(new Error("blocked")) : Promise.resolve();
  }
  pause() { this.paused += 1; }
}
FakeAudio.instances = [];
FakeAudio.rejectPlay = false;

/** 毎回まっさらな AudioBus を読み込む(モジュールはシングルトンなので)。 */
function loadBus({ manifest = {}, withContext = true, withAudio = true } = {}) {
  FakeAudioContext.instances = [];
  FakeAudio.instances = [];
  FakeAudio.rejectPlay = false;
  globalThis.PRESENTATION_SFX = manifest;
  if (withContext) globalThis.AudioContext = FakeAudioContext;
  else delete globalThis.AudioContext;
  if (withAudio) globalThis.Audio = FakeAudio;
  else delete globalThis.Audio;
  delete require.cache[require.resolve(AUDIO_JS)];
  return require(AUDIO_JS);
}

const CUES = [
  "judgeStandard", "judgeSilver", "judgeGold", "judgePremium",
  "totalSting", "rankTick", "rankSettle", "winnerSting",
];

/* --------------------------------------------------------------------------
   cue の解決: asset 優先 / procedural fallback
   -------------------------------------------------------------------------- */

test("every logical cue exists as a procedural recipe", () => {
  const bus = loadBus();
  assert.deepEqual(bus.PROCEDURAL_CUES, CUES);
});

test("an empty manifest resolves every cue to the procedural fallback", () => {
  const bus = loadBus();
  for (const key of CUES) {
    assert.deepEqual(bus.resolveCue(key), { key, source: "procedural", src: null });
  }
});

test("a manifest entry takes priority over the procedural fallback", () => {
  const bus = loadBus({ manifest: { judgeGold: "/static/assets/sfx/gold.m4a" } });
  assert.deepEqual(bus.resolveCue("judgeGold"), {
    key: "judgeGold", source: "asset", src: "/static/assets/sfx/gold.m4a",
  });
  // 他のキーは合成音のまま。素材は1つずつ差し替えられる
  assert.equal(bus.resolveCue("judgeSilver").source, "procedural");
});

test("an unknown cue resolves to nothing and plays nothing", () => {
  const bus = loadBus();
  assert.deepEqual(bus.resolveCue("applause"), { key: "applause", source: "none", src: null });
  assert.equal(bus.play("applause"), false);
  assert.equal(FakeAudioContext.instances.length, 0, "無いcueでAudioContextを作らない");
});

/* --------------------------------------------------------------------------
   再生
   -------------------------------------------------------------------------- */

test("a procedural cue schedules oscillators with an envelope", () => {
  const bus = loadBus();
  assert.equal(bus.play("judgeStandard"), true);
  const ctx = FakeAudioContext.instances[0];
  assert.ok(ctx.oscillators.length >= 1);
  const osc = ctx.oscillators[0];
  assert.ok(osc.startedAt >= ctx.currentTime);
  assert.ok(osc.stoppedAt > osc.startedAt, "終端で必ず止める");
  const gain = ctx.gains.find((g) => g.gain.events.length);
  assert.equal(gain.gain.events[0][0], "set");
  assert.ok(gain.gain.peak > 0);
});

test("an asset cue plays the asset and does not synthesize", () => {
  const bus = loadBus({ manifest: { totalSting: "/static/assets/sfx/total.m4a" } });
  assert.equal(bus.play("totalSting"), true);
  assert.equal(FakeAudio.instances.length, 1);
  assert.equal(FakeAudio.instances[0].plays, 1);
  assert.equal(FakeAudioContext.instances.length, 0, "assetが鳴るなら合成しない");
});

test("a rejected asset playback falls back to the procedural cue", async () => {
  const bus = loadBus({ manifest: { winnerSting: "/static/assets/sfx/w.m4a" } });
  FakeAudio.rejectPlay = true;
  bus.play("winnerSting");
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(FakeAudioContext.instances.length, 1, "合成音へ落ちていない");
  assert.ok(FakeAudioContext.instances[0].oscillators.length > 0);
});

test("playback never throws when the browser has no audio at all", () => {
  const bus = loadBus({ withContext: false, withAudio: false });
  for (const key of CUES) {
    assert.equal(bus.play(key), false, key);
  }
  assert.doesNotThrow(() => bus.prime());
});

test("one AudioContext is reused for the whole presentation", () => {
  const bus = loadBus();
  for (let i = 0; i < 40; i += 1) bus.play("judgePremium");
  assert.equal(FakeAudioContext.instances.length, 1);
});

test("finished nodes are disconnected and forgotten", () => {
  const bus = loadBus();
  bus.play("judgeGold");
  const ctx = FakeAudioContext.instances[0];
  ctx.oscillators.forEach((osc) => osc.end());
  assert.ok(ctx.oscillators.every((osc) => osc.disconnected));
  // 解放後に cancel しても落ちない
  assert.doesNotThrow(() => bus.cancel());
});

test("cancel silences everything that is still ringing", () => {
  const bus = loadBus();
  bus.play("winnerSting");
  const ctx = FakeAudioContext.instances[0];
  const before = ctx.oscillators.map((o) => o.stoppedAt);
  bus.cancel();
  ctx.oscillators.forEach((osc, index) => {
    assert.ok(osc.stoppedAt <= before[index], "止め直していない");
    assert.ok(osc.stoppedAt <= ctx.currentTime + 0.02);
  });
  ctx.gains.forEach((gain) => {
    if (gain.gain.events.length) {
      assert.ok(gain.gain.events.some((e) => e[0] === "cancel"));
    }
  });
});

test("cancel also stops asset playback", () => {
  const bus = loadBus({ manifest: { rankSettle: "/static/assets/sfx/r.m4a" } });
  bus.play("rankSettle");
  bus.cancel();
  assert.equal(FakeAudio.instances[0].paused, 1);
});

/* --------------------------------------------------------------------------
   autoplay policy
   -------------------------------------------------------------------------- */

test("prime wakes the audio context from the host click", () => {
  const bus = loadBus();
  assert.equal(bus.isPrimed(), false);
  bus.prime();
  assert.equal(bus.isPrimed(), true);
  assert.equal(FakeAudioContext.instances[0].resumed, 1);
  // 2回目以降は何もしない
  bus.prime();
  assert.equal(FakeAudioContext.instances[0].resumed, 1);
});

test("a suspended context is resumed on the next cue as well", () => {
  const bus = loadBus();
  bus.play("rankTick");
  assert.ok(FakeAudioContext.instances[0].resumed >= 1);
});

/* --------------------------------------------------------------------------
   強さの序列
   -------------------------------------------------------------------------- */

function loudness(bus, key) {
  const ctx = FakeAudioContext.instances[0];
  const before = ctx.gains.length;
  bus.play(key);
  const gains = ctx.gains.slice(before);
  const peak = Math.max(...gains.map((g) => g.gain.peak));
  const tail = Math.max(...ctx.oscillators.slice(-gains.length).map((o) => o.stoppedAt));
  return { peak, tail };
}

test("the cue hierarchy rises from judge standard to the winner", () => {
  const bus = loadBus();
  bus.play("judgeStandard");
  const order = [
    "judgeStandard", "judgeSilver", "judgeGold", "judgePremium", "totalSting", "winnerSting",
  ];
  const measured = order.map((key) => loudness(bus, key));
  for (let i = 1; i < measured.length; i += 1) {
    assert.ok(
      measured[i].peak > measured[i - 1].peak,
      `${order[i]} は ${order[i - 1]} より強いはず`
    );
  }
  // 音量だけでなく余韻の長さでも格を出す
  const premium = measured[3].tail;
  const total = measured[4].tail;
  const winner = measured[5].tail;
  assert.ok(total > premium && winner > total);
});

test("ranking cues stay quieter than every judge cue", () => {
  const bus = loadBus();
  bus.play("judgeStandard");
  const standard = loudness(bus, "judgeStandard");
  assert.ok(loudness(bus, "rankTick").peak < standard.peak);
  assert.ok(loudness(bus, "rankSettle").peak < standard.peak);
});

/* --------------------------------------------------------------------------
   使わないもの
   -------------------------------------------------------------------------- */

test("the bus has no speech and no background music", () => {
  const source = require("node:fs").readFileSync(AUDIO_JS, "utf-8");
  for (const token of ["speechSynthesis", "SpeechSynthesis", "utterance", "loop = true", "loop=true"]) {
    assert.ok(!source.includes(token), token);
  }
});
