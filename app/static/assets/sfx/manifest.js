/* Presentation の効果音マニフェスト。
   キーは PresentationAudio が再生に使う論理名、値は /static 配下のパス。

   **ここは空のままで完全に動作する。** 素材が無いキーは AudioBus が
   Web Audio API でその場で合成する(asset優先 / procedural fallback)。
   将来、適切にライセンスされた素材を用意できたら、下の行をコメント解除して
   パスを入れるだけでそちらが優先される。演出コードの変更は不要。

   放送音源・実在人物の音声・楽曲を持ち込まないこと。音声読み上げ
   (「87点!」等)と BGM も扱わない。Silence も演出の一部である。 */
window.PRESENTATION_SFX = {
  // 採点者の得点開示。得点帯(0-69 / 70-79 / 80-89 / 90-100)ごとに1つ。
  // judgeStandard: "/static/assets/sfx/judge-standard.m4a",
  // judgeSilver:   "/static/assets/sfx/judge-silver.m4a",
  // judgeGold:     "/static/assets/sfx/judge-gold.m4a",
  // judgePremium:  "/static/assets/sfx/judge-premium.m4a",

  // 合計点。得点によるvariantは作らない(TOTALは常にこの1つ)。
  // totalSting:    "/static/assets/sfx/total-sting.m4a",

  // ランキング。rankTick は何度も鳴るので特に控えめな素材にすること。
  // rankTick:      "/static/assets/sfx/rank-tick.m4a",
  // rankSettle:    "/static/assets/sfx/rank-settle.m4a",

  // 優勝。全Presentationで最も強い1つ。同率1位でも1回だけ鳴らす。
  // winnerSting:   "/static/assets/sfx/winner-sting.m4a",
};
