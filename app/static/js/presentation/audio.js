/* Presentation の効果音バス。**asset優先 + Web Audio procedural fallback**。

   設計上の絶対条件:
     - 音は「装飾」であって進行条件ではない。state machine は AudioBus の
       戻り値を一切参照しない。
     - マニフェストが空でも完全に動作する。素材が無いキーは Web Audio で
       その場で合成する(404 も例外も発生しない)。
     - 再生失敗で Presentation が止まらない。同期例外は try/catch、
       Promise rejection は .catch() で握り潰し、asset が鳴らなければ
       procedural へ落ちる。
     - AudioContext は**1つだけ**を生成して使い回す。鳴らし終えた
       oscillator / gain は onended で切り離して解放する。
     - 音声読み上げ(「87点!」等)と BGM は扱わない。SFX のみ。 */
(function (root) {
  "use strict";

  var elements = {};
  var primed = false;
  var context = null;
  var master = null;
  var live = [];

  function manifest() {
    var m = root.PRESENTATION_SFX;
    return (m && typeof m === "object") ? m : {};
  }

  /* -------------------------------------------------------------------------
     procedural レシピ

     方向性は luxury / broadcast-like / restrained。arcade / retro game /
     casino にしない。Judge は「共通の base impact + tier ごとの accent」で
     積み上げ、4種類の別世界の音にはしない。

     強さの序列(音量だけでなく、余韻の長さと低域の厚みでも差をつける):
       judgeStandard < judgeSilver < judgeGold < judgePremium
                     < totalSting < winnerSting
     ------------------------------------------------------------------------- */

  // 全 Judge 共通の短い impact。ここに tier ごとの accent を足していく。
  var JUDGE_BASE = [
    { type: "sine", from: 190, to: 96, dur: 0.10, gain: 1 },
    { type: "triangle", freq: 440, dur: 0.05, gain: 0.18 }
  ];

  function judgeCue(gain, accents) {
    return { gain: gain, layers: JUDGE_BASE.concat(accents || []) };
  }

  var PROCEDURAL = {
    // 0〜69: 通常表示。短く控えめで、negative な色を持たせない。
    judgeStandard: judgeCue(0.22, []),

    // 70〜79: base + ごく薄い明るい倍音
    judgeSilver: judgeCue(0.26, [
      { type: "sine", freq: 1318.5, dur: 0.18, gain: 0.26, delay: 0.02 }
    ]),

    // 80〜89: base + 抑制された chime。Silver より少し長い余韻。
    judgeGold: judgeCue(0.30, [
      { type: "sine", freq: 880, dur: 0.34, gain: 0.30, delay: 0.02 },
      { type: "sine", freq: 1318.5, dur: 0.30, gain: 0.16, delay: 0.03 }
    ]),

    // 90〜100: base + 明るい倍音 + ごく短い shimmer。Judge 内で最も華やか。
    judgePremium: judgeCue(0.36, [
      { type: "sine", freq: 1046.5, dur: 0.42, gain: 0.32, delay: 0.02 },
      { type: "sine", freq: 1568, dur: 0.36, gain: 0.18, delay: 0.04 },
      { type: "sine", freq: 2637, dur: 0.20, gain: 0.10, delay: 0.10 }
    ]),

    // TOTAL: 得点に関係なく常にこれ。低い impact + rise + gold harmonic + 長い減衰。
    totalSting: {
      gain: 0.50,
      layers: [
        { type: "sine", from: 70, to: 46, dur: 0.50, gain: 0.95 },
        { type: "triangle", from: 220, to: 660, dur: 0.28, gain: 0.20, delay: 0.04 },
        { type: "sine", freq: 523.25, dur: 1.20, gain: 0.34, delay: 0.18 },
        { type: "sine", freq: 784, dur: 1.10, gain: 0.22, delay: 0.20 },
        { type: "sine", freq: 1046.5, dur: 0.95, gain: 0.14, delay: 0.22 }
      ]
    },

    // ランキングの移動。何度も鳴るので徹底的に控えめにする。
    rankTick: {
      gain: 0.08,
      layers: [{ type: "triangle", freq: 1200, dur: 0.05, gain: 0.6 }]
    },

    // 着地。rankTick より少しだけ明確。
    rankSettle: {
      gain: 0.14,
      layers: [
        { type: "sine", freq: 660, dur: 0.22, gain: 0.6 },
        { type: "sine", freq: 990, dur: 0.20, gain: 0.28, delay: 0.02 }
      ]
    },

    // 全 Presentation で最も強い。ただし音量だけで押さず、和音と余韻で格を出す。
    winnerSting: {
      gain: 0.60,
      layers: [
        { type: "sine", from: 98, to: 65, dur: 0.80, gain: 0.95 },
        { type: "sine", freq: 392, dur: 1.70, gain: 0.34, delay: 0.06 },
        { type: "sine", freq: 523.25, dur: 1.65, gain: 0.30, delay: 0.10 },
        { type: "sine", freq: 659.25, dur: 1.55, gain: 0.24, delay: 0.14 },
        { type: "sine", freq: 784, dur: 1.45, gain: 0.20, delay: 0.18 },
        { type: "sine", freq: 2093, dur: 0.50, gain: 0.08, delay: 0.30 }
      ]
    }
  };

  /** そのキーを何で鳴らすか。asset が最優先で、無ければ procedural。
      将来 manifest に素材を足すだけで、演出コードは一行も変えずに切り替わる。 */
  function resolveCue(key) {
    var src = manifest()[key];
    if (src) return { key: key, source: "asset", src: src };
    if (PROCEDURAL[key]) return { key: key, source: "procedural", src: null };
    return { key: key, source: "none", src: null };
  }

  /* -------------------------------------------------------------------------
     asset 再生
     ------------------------------------------------------------------------- */

  function ensure(key) {
    if (Object.prototype.hasOwnProperty.call(elements, key)) {
      return elements[key];
    }
    var src = manifest()[key];
    var element = null;
    if (src && typeof root.Audio === "function") {
      try {
        element = new root.Audio(src);
        element.preload = "auto";
      } catch (err) {
        element = null;
      }
    }
    elements[key] = element;
    return element;
  }

  function swallow(promise, onFailure) {
    if (promise && typeof promise.catch === "function") {
      promise.catch(function () {
        // 素材が鳴らせなかったときは合成音へ落とす(無音にしない)
        if (onFailure) {
          try { onFailure(); } catch (err) { /* 音は装飾なので無視する */ }
        }
      });
    }
  }

  function playAsset(key) {
    var element = ensure(key);
    if (!element) return false;
    try {
      element.currentTime = 0;
      swallow(element.play(), function () { playProcedural(key); });
    } catch (err) {
      return false;
    }
    return true;
  }

  /* -------------------------------------------------------------------------
     procedural 再生 (Web Audio)
     ------------------------------------------------------------------------- */

  function ensureContext() {
    if (context) return context;
    var Ctor = root.AudioContext || root.webkitAudioContext || null;
    if (!Ctor) return null;
    try {
      context = new Ctor();
      master = context.createGain();
      master.gain.value = 0.9;
      master.connect(context.destination);
    } catch (err) {
      context = null;
      master = null;
    }
    return context;
  }

  function track(oscillator, gain) {
    var entry = { oscillator: oscillator, gain: gain };
    live.push(entry);
    oscillator.onended = function () {
      var index = live.indexOf(entry);
      if (index >= 0) live.splice(index, 1);
      try {
        oscillator.disconnect();
        gain.disconnect();
      } catch (err) { /* 既に切れている場合は何もしない */ }
    };
  }

  function playLayer(layer, startAt, cueGain) {
    var oscillator = context.createOscillator();
    var gain = context.createGain();
    var begin = startAt + (layer.delay || 0);
    var duration = layer.dur;

    oscillator.type = layer.type || "sine";
    if (layer.from) {
      oscillator.frequency.setValueAtTime(layer.from, begin);
      oscillator.frequency.exponentialRampToValueAtTime(
        Math.max(1, layer.to || layer.from), begin + duration
      );
    } else {
      oscillator.frequency.setValueAtTime(layer.freq, begin);
    }

    var peak = Math.max(0.0001, cueGain * (layer.gain == null ? 1 : layer.gain));
    gain.gain.setValueAtTime(0.0001, begin);
    gain.gain.exponentialRampToValueAtTime(peak, begin + 0.008);
    gain.gain.exponentialRampToValueAtTime(0.0001, begin + duration);

    oscillator.connect(gain);
    gain.connect(master);
    oscillator.start(begin);
    // 終端で必ず止める。鳴りっぱなしの node を残さない。
    oscillator.stop(begin + duration + 0.02);
    track(oscillator, gain);
  }

  function playProcedural(key) {
    var recipe = PROCEDURAL[key];
    if (!recipe) return false;
    if (!ensureContext()) return false;
    try {
      if (context.state === "suspended" && context.resume) swallow(context.resume());
      var startAt = context.currentTime + 0.001;
      recipe.layers.forEach(function (layer) {
        playLayer(layer, startAt, recipe.gain);
      });
    } catch (err) {
      return false;
    }
    return true;
  }

  var AudioBus = {
    /** Hostのクリック(user gesture)から呼ぶ。autoplay policy を回避する。 */
    prime: function () {
      if (primed) return;
      primed = true;
      try {
        if (ensureContext() && context.state === "suspended" && context.resume) {
          swallow(context.resume());
        }
      } catch (err) {
        /* AudioContext を起こせない環境でも Presentation は続行する */
      }
      Object.keys(manifest()).forEach(function (key) {
        var element = ensure(key);
        if (!element) return;
        try {
          element.muted = true;
          swallow(element.play());
          element.pause();
          element.currentTime = 0;
          element.muted = false;
        } catch (err) {
          /* prime できない環境でも後続の play() は試みる */
        }
      });
    },

    /** asset があれば asset、無ければ合成音。どちらも無ければ何もしない。
        呼び出し側は戻り値を見なくてよい(音は進行条件ではない)。 */
    play: function (key) {
      var cue = resolveCue(key);
      if (cue.source === "asset" && playAsset(key)) return true;
      return playProcedural(key);
    },

    /** Skip / reduced motion 用。今鳴っている合成音を即座に畳む。
        古い oscillator が Skip の後から鳴り続けないようにするためのもの。 */
    cancel: function () {
      if (context) {
        var now = context.currentTime;
        live.slice().forEach(function (entry) {
          try {
            entry.gain.gain.cancelScheduledValues(now);
            entry.gain.gain.setValueAtTime(0.0001, now);
            entry.oscillator.stop(now + 0.01);
          } catch (err) { /* 既に停止している node は無視する */ }
        });
      }
      Object.keys(elements).forEach(function (key) {
        var element = elements[key];
        if (!element) return;
        try {
          element.pause();
          element.currentTime = 0;
        } catch (err) { /* 停止できない環境は無視する */ }
      });
    },

    /** マニフェストを差し替えたときのキャッシュ破棄(テスト用)。 */
    reset: function () {
      elements = {};
      primed = false;
    },

    isPrimed: function () {
      return primed;
    },

    resolveCue: resolveCue,
    PROCEDURAL_CUES: Object.keys(PROCEDURAL)
  };

  root.PresentationAudio = AudioBus;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = AudioBus;
  }
})(typeof window !== "undefined" ? window : globalThis);
