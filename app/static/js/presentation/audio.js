/* Presentation の効果音バス。

   設計上の絶対条件:
     - 音は「装飾」であって進行条件ではない。state machine は AudioBus の
       戻り値を一切参照しない。
     - マニフェストが空でも(=音源が1つも無くても)完全に動作する。
       未定義キーの play() は完全な no-op で、404 も例外も発生しない。
     - 再生失敗で Presentation が止まらない。同期例外は try/catch、
       Promise rejection は .catch() で握り潰す。 */
(function (root) {
  "use strict";

  var elements = {};
  var primed = false;

  function manifest() {
    var m = root.PRESENTATION_SFX;
    return (m && typeof m === "object") ? m : {};
  }

  // 未定義キーは null をキャッシュして「無い」ことを覚える。
  // Audio要素は生成しないので、存在しない素材へのHTTP要求も起きない。
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

  function swallow(promise) {
    if (promise && typeof promise.catch === "function") {
      promise.catch(function () { /* 再生できない環境では無視する */ });
    }
  }

  var AudioBus = {
    /** Hostのクリック(user gesture)から呼ぶ。autoplay policy を回避する。 */
    prime: function () {
      if (primed) return;
      primed = true;
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

    /** 素材が無ければ false を返して何もしない。呼び出し側は戻り値を見なくてよい。 */
    play: function (key) {
      var element = ensure(key);
      if (!element) return false;
      try {
        element.currentTime = 0;
        swallow(element.play());
      } catch (err) {
        return false;
      }
      return true;
    },

    /** マニフェストを差し替えたときのキャッシュ破棄(テスト用)。 */
    reset: function () {
      elements = {};
      primed = false;
    },

    isPrimed: function () {
      return primed;
    }
  };

  root.PresentationAudio = AudioBus;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = AudioBus;
  }
})(typeof window !== "undefined" ? window : globalThis);
