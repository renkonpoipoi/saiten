/* Presentation の効果音マニフェスト。
   キーは PresentationAudio が再生に使う論理名、値は /static 配下のパス。

   Phase 9 では実在人物の放送音声由来だった暫定素材への依存を外し、
   ここを空のまま出荷できる構造にしている。素材が1つも無くても
   Presentation は完全に動作する(AudioBus が no-op になる)。

   素材の選定と追加は Phase 9E で行う。適切な素材が用意できない場合は
   無音のまま運用してよい。 */
window.PRESENTATION_SFX = {
  // judgeMove:   "/static/assets/sfx/judge-move.m4a",
  // judgeHit:    "/static/assets/sfx/judge-hit.m4a",
  // rankTick:    "/static/assets/sfx/rank-tick.m4a",
  // rankSettle:  "/static/assets/sfx/rank-settle.m4a",
  // total:       "/static/assets/sfx/total.m4a",
  // winner:      "/static/assets/sfx/winner.m4a",
};
