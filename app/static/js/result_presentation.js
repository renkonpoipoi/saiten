(function () {
  const projectId = window.PROJECT_ID;
  const core = window.PresentationCore;
  const stage = window.PresentationStage;

  const presentationRoot = document.getElementById("presentationRoot");
  const standbyPanel = document.getElementById("standbyPanel");
  const revealStage = document.getElementById("revealStage");
  const rankingPanel = document.getElementById("rankingPanel");
  const rankingActions = document.getElementById("rankingActions");
  const finishedPanel = document.getElementById("finishedPanel");
  const batchSubjectPanel = document.getElementById("batchSubjectPanel");
  const batchAdvancePanel = document.getElementById("batchAdvancePanel");
  const stageControls = document.getElementById("stageControls");

  // ステージ描画に渡す参照。DOM検索をここに集約し、presentation/stage.js は
  // 渡された要素だけを触る(idの二重管理を避けるため)。
  const stageRefs = {
    stage: revealStage,
    eyebrow: document.getElementById("revealEyebrow"),
    subjectName: document.getElementById("revealSubjectName"),
    rail: document.getElementById("revealJudgeRow"),
    activeCard: document.getElementById("activeJudgeCard"),
    activeName: document.getElementById("activeJudgeName"),
    activeScore: document.getElementById("activeJudgeScore"),
    total: document.getElementById("revealTotal"),
    totalValue: document.getElementById("revealTotalValue"),
  };

  // 取得済みの集計結果。FINISHED後のreplay/ランキング再表示は全てこの値の
  // 再描画だけで行い、サーバーの状態は一切変更しない。
  let currentSummary = null;

  // 効果音は presentation/audio.js の AudioBus に委譲する。素材が1つも
  // 無くても完全に no-op になるため、state machine は戻り値を参照しない
  // (音は装飾であり進行条件ではない)。
  const audio = window.PresentationAudio;

  function prepareAudio() {
    if (audio) audio.prime();
  }

  function playSfx(key) {
    if (audio) audio.play(key);
  }

  function wait(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  // ---------------------------------------------------------------------------
  // パネル切り替え
  // ---------------------------------------------------------------------------

  const ALL_PANELS = [
    standbyPanel,
    batchSubjectPanel,
    batchAdvancePanel,
    revealStage,
    rankingPanel,
    finishedPanel,
  ];

  function showOnly(...visible) {
    ALL_PANELS.forEach((panel) => {
      panel.classList.toggle("hidden", !visible.includes(panel));
    });
    hideSequentialPanels();
  }

  // ---------------------------------------------------------------------------
  // 演出 runner
  //
  // 「何がいつ起きるか」は core.js の純粋関数が step 配列として返し、ここは
  // それを1本のasyncループで実行するだけ。setTimeoutの入れ子を作らない。
  // この関数の内部からサーバーへPOSTしてはならない(animationの完了だけで
  // lifecycleを進めないため)。
  // ---------------------------------------------------------------------------

  let runToken = 0;
  let activeSequence = null;

  function setPhase(phase) {
    revealStage.dataset.phase = phase;
  }

  function setBusy(busy) {
    presentationRoot.dataset.busy = busy ? "true" : "false";
    stageControls.dataset.visible = busy ? "false" : "true";
  }

  async function runSteps(steps, applyStep) {
    runToken += 1;
    const token = runToken;
    setBusy(true);
    for (const step of steps) {
      if (token !== runToken) return false;
      setPhase(step.phase);
      applyStep(step);
      if (step.duration > 0) await wait(step.duration);
    }
    if (token !== runToken) return false;
    setBusy(false);
    return true;
  }

  /** 1つの意味のある演出sequenceを走らせる。完了か中断かを返す。 */
  async function playSequence({ steps, applyStep, finalState, onComplete }) {
    activeSequence = { finalState, onComplete };
    const completed = await runSteps(steps, applyStep);
    if (!completed) return false;
    activeSequence = null;
    onComplete();
    return true;
  }

  // Skip: いま走っている演出を、その演出の正しい最終状態へ即座に進める。
  // サーバーの状態は一切変更しない。
  function skipCurrentSequence() {
    const sequence = activeSequence;
    if (!sequence) return;
    activeSequence = null;
    runToken += 1;
    setBusy(false);
    sequence.finalState();
    sequence.onComplete();
  }

  // ---------------------------------------------------------------------------
  // 得点発表エンジン(BATCH / SEQUENTIAL 完全共通)
  //
  // Judge全員を1人ずつ開示し、全員終わってから初めてTOTALを出す。
  // 途中経過の合計(running total)は表示しない。
  // ---------------------------------------------------------------------------

  function applyTiming(judgeCount) {
    const vars = core.timingCssVars(judgeCount);
    Object.keys(vars).forEach((name) => {
      presentationRoot.style.setProperty(name, vars[name]);
    });
  }

  function applySubjectStep(step, subject, judges) {
    switch (step.phase) {
      case "JUDGE_ENTER":
        stage.enterJudge(stageRefs, judges[step.index], step.index);
        playSfx("judgeMove");
        break;
      case "JUDGE_SCORE":
        stage.revealJudgeScore(stageRefs, judges[step.index]);
        playSfx("judgeHit");
        break;
      case "JUDGE_SETTLE":
        stage.settleJudge(stageRefs, judges[step.index], step.index);
        break;
      case "ALL_SETTLED":
        // 中央を一度空にして、TOTALの前に長めの間を作る
        stage.clearCenter(stageRefs);
        break;
      case "TOTAL_ENTER":
        stage.revealTotal(stageRefs, subject.total_score);
        playSfx("total");
        break;
      default:
        break;
    }
  }

  async function revealSubjectSequence(subject, onComplete) {
    const judges = subject.judge_totals || [];
    revealStage.classList.remove("hidden");
    stage.prepareSubject(stageRefs, subject, judges);
    applyTiming(judges.length);

    return playSequence({
      steps: core.buildSubjectSteps(judges.length),
      applyStep: (step) => applySubjectStep(step, subject, judges),
      finalState: () => stage.showSubjectFinalState(stageRefs, subject, judges),
      onComplete,
    });
  }

  // ---------------------------------------------------------------------------
  // 結果取得
  // ---------------------------------------------------------------------------

  async function loadSummary() {
    try {
      return await apiFetch(`/api/projects/${projectId}/result-summary`);
    } catch (err) {
      if (err.status === 403) {
        window.location.href = "/host/login";
        return null;
      }
      if (err.status === 409) {
        showMessage("まだ結果発表を開始できません(締切前です)。", { isError: true });
        return null;
      }
      showMessage(err.message, { isError: true });
      return null;
    }
  }

  function showRanking(summary) {
    standbyPanel.classList.add("hidden");
    revealStage.classList.add("hidden");
    rankingPanel.classList.remove("hidden");
    rankingPanel.dataset.preserved = "false";

    const list = document.getElementById("rankingList");
    list.innerHTML = "";
    const sorted = [...summary.subjects].sort(
      (a, b) => a.rank - b.rank || a.sort_order - b.sort_order
    );
    sorted.forEach((subject) => {
      const row = document.createElement("div");
      row.className = `ranking-row${subject.rank === 1 ? " rank-1" : ""}`;

      const rank = document.createElement("div");
      rank.className = "rank";
      rank.textContent = `${subject.rank}位`;

      const name = document.createElement("div");
      name.className = "name";
      name.textContent = subject.name;

      const score = document.createElement("div");
      score.className = "score";
      score.textContent = `${subject.total_score}点 (平均 ${subject.mean_score})`;

      row.append(rank, name, score);
      list.appendChild(row);
    });

    renderRankingActions(summary);
  }

  // 最終ランキングに常設する導線。BATCH / SEQUENTIAL 共通で、showRanking()から
  // 必ず呼ぶ。ランキングを出した結果ここがhiddenのままになると袋小路になるため、
  // action area自体のhiddenは毎回無条件で外す。
  function renderRankingActions(summary) {
    rankingActions.classList.remove("hidden");

    const finishButton = document.getElementById("finishButton");
    finishButton.classList.toggle("hidden", summary.project.status !== "PRESENTING");

    // 再生はFINISHED後だけ。PRESENTING中は「発表を終了する」を先に押させる。
    document
      .getElementById("replayButton")
      .classList.toggle("hidden", summary.project.status !== "FINISHED");
  }

  function setFinishedPanelVisible(visible) {
    finishedPanel.classList.toggle("hidden", !visible);
  }

  // ---------------------------------------------------------------------------
  // BATCH: 被採点者1組ごとに停止する進行
  //
  // 1クリック = 「Judge全員 -> TOTAL」という1つの意味のある演出sequence。
  // Judge1人ごとのクリックは要求せず、かつ全被採点者を通しで自動再生もしない。
  // ---------------------------------------------------------------------------

  let batchOrder = [];
  let batchIndex = 0;

  function orderedSubjects(summary) {
    return [...summary.subjects].sort((a, b) => a.sort_order - b.sort_order);
  }

  function isLastBatchSubject() {
    return batchIndex >= batchOrder.length - 1;
  }

  function showBatchSubjectStandby(index) {
    batchIndex = index;
    const subject = batchOrder[index];
    if (!subject) return;
    document.getElementById("batchSubjectName").textContent = subject.name;
    showOnly(batchSubjectPanel);
    revealStage.dataset.phase = "idle";
  }

  function showBatchAdvance() {
    const button = document.getElementById("batchAdvanceButton");
    const note = document.getElementById("batchAdvanceNote");
    if (isLastBatchSubject()) {
      button.textContent = "最終ランキングを発表する";
      note.textContent = "全員の得点発表が終わりました。";
    } else {
      button.textContent = "次の被採点者へ";
      note.textContent = `${batchIndex + 1} / ${batchOrder.length} 組の発表が終わりました。`;
    }
    showOnly(revealStage, batchAdvancePanel);
  }

  function startBatchPresentation(summary) {
    batchOrder = orderedSubjects(summary);
    batchIndex = 0;
    showBatchSubjectStandby(0);
  }

  // FINISHED後のreplayもこの経路を使う。GETで取得済みのsummaryを描き直すだけで、
  // サーバーへの通信も状態変更も発生しない。
  function restartPresentation() {
    if (!currentSummary) return;
    rankingPanel.classList.add("hidden");
    startBatchPresentation(currentSummary);
  }

  // Phase 9D で演出付きのFinal Ranking Revealに差し替える。
  function showBatchFinalRanking() {
    showRanking(currentSummary);
    if (currentSummary.project.status === "FINISHED") {
      setFinishedPanelVisible(true);
    }
  }

  document.getElementById("batchRevealButton").addEventListener("click", async () => {
    prepareAudio();
    showOnly(revealStage);
    const subject = batchOrder[batchIndex];
    if (!subject) return;
    await revealSubjectSequence(subject, showBatchAdvance);
  });

  document.getElementById("batchAdvanceButton").addEventListener("click", () => {
    if (isLastBatchSubject()) {
      showBatchFinalRanking();
      return;
    }
    showBatchSubjectStandby(batchIndex + 1);
  });

  document.getElementById("startRevealButton").addEventListener("click", async () => {
    try {
      await apiFetch(`/api/projects/${projectId}/transition`, {
        method: "POST",
        body: JSON.stringify({ target_status: "PRESENTING" }),
      });
    } catch (err) {
      showMessage(err.message, { isError: true });
      return;
    }
    prepareAudio();
    currentSummary = await loadSummary();
    if (currentSummary) startBatchPresentation(currentSummary);
  });

  // --- FINISHED後の導線。いずれも表示操作のみで、APIはGETしか使わない ---

  document.getElementById("replayButton").addEventListener("click", async () => {
    if (!currentSummary) return;
    setFinishedPanelVisible(false);
    prepareAudio();
    restartPresentation();
  });

  document.getElementById("showRankingButton").addEventListener("click", () => {
    if (!currentSummary) return;
    showRanking(currentSummary);
    setFinishedPanelVisible(true);
  });

  document.getElementById("finishButton").addEventListener("click", async () => {
    try {
      await apiFetch(`/api/projects/${projectId}/transition`, {
        method: "POST",
        body: JSON.stringify({ target_status: "FINISHED" }),
      });
      showMessage("発表を終了しました");
      // 手元のsummaryもFINISHEDに揃えて、導線を「終了する」から「もう一度見る」へ
      // 差し替える。サーバーからの再取得はしない(GET以外も発生させない)。
      if (currentSummary) {
        currentSummary.project.status = "FINISHED";
        renderRankingActions(currentSummary);
      } else {
        document.getElementById("finishButton").classList.add("hidden");
      }
      setFinishedPanelVisible(true);
    } catch (err) {
      showMessage(err.message, { isError: true });
    }
  });

  // ---------------------------------------------------------------------------
  // ステージ操作(観客画面を邪魔しないよう右下に小さく置く)
  //
  // Skipは演出を最終状態へ進めるだけで、サーバーの状態は変更しない。
  // Fullscreenは失敗してもPresentation本体に影響させない。
  // ---------------------------------------------------------------------------

  let controlsTimer = null;

  function flashControls() {
    stageControls.dataset.visible = "true";
    clearTimeout(controlsTimer);
    // auto-hideの秒数はPhase 9Eの実ブラウザ調整で決める
    controlsTimer = setTimeout(() => {
      if (presentationRoot.dataset.busy === "true") {
        stageControls.dataset.visible = "false";
      }
    }, 2400);
  }

  document.getElementById("skipButton").addEventListener("click", () => {
    skipCurrentSequence();
  });

  document.getElementById("fullscreenButton").addEventListener("click", () => {
    try {
      const element = document.documentElement;
      if (document.fullscreenElement) {
        if (document.exitFullscreen) document.exitFullscreen().catch(() => {});
        return;
      }
      if (element.requestFullscreen) {
        const result = element.requestFullscreen();
        if (result && result.catch) result.catch(() => {});
      }
    } catch (err) {
      /* 全画面にできない環境でも発表は続行する */
    }
  });

  document.addEventListener("mousemove", flashControls);
  document.addEventListener("keydown", (event) => {
    flashControls();
    if (event.key === "Escape") skipCurrentSequence();
  });

  // -------------------------------------------------------------------------
  // SEQUENTIAL: Subject単位の発表
  // -------------------------------------------------------------------------

  const SEQUENTIAL_POLL_INTERVAL_MS = 15000;

  const sequentialWaitingPanel = document.getElementById("sequentialWaitingPanel");
  const sequentialStandbyPanel = document.getElementById("sequentialStandbyPanel");
  const confirmNextPanel = document.getElementById("confirmNextPanel");
  const finalRankingPanel = document.getElementById("finalRankingPanel");

  let pollTimer = null;
  let pollInFlight = false;
  let pollingStopped = false;
  let pendingSubjectId = null;

  function hideSequentialPanels() {
    [sequentialWaitingPanel, sequentialStandbyPanel, confirmNextPanel, finalRankingPanel]
      .forEach((panel) => panel.classList.add("hidden"));
  }

  function stopPolling() {
    pollingStopped = true;
    clearTimeout(pollTimer);
    pollTimer = null;
  }

  function scheduleNextPoll() {
    clearTimeout(pollTimer);
    if (pollingStopped) return;
    pollTimer = setTimeout(pollState, SEQUENTIAL_POLL_INTERVAL_MS);
  }

  async function loadState() {
    try {
      return await apiFetch(`/api/projects/${projectId}/presentation-state`);
    } catch (err) {
      if (err.status === 403) {
        stopPolling();
        window.location.href = "/host/login";
        return null;
      }
      return null;
    }
  }

  async function pollState() {
    // タブが非表示の間は通信しない。重複requestも避ける。
    // 演出中(busy)はサーバー状態を見に行かない。
    if (document.hidden || pollInFlight || pollingStopped) {
      scheduleNextPoll();
      return;
    }
    pollInFlight = true;
    try {
      const state = await loadState();
      if (state) await renderSequential(state);
    } finally {
      pollInFlight = false;
    }
    scheduleNextPoll();
  }

  async function renderSequential(state) {
    hideSequentialPanels();
    standbyPanel.classList.add("hidden");
    batchSubjectPanel.classList.add("hidden");
    batchAdvancePanel.classList.add("hidden");

    if (state.project.status !== "SCORING") {
      // 全Subjectの発表が終わり、最終ランキング段階へ入っている
      stopPolling();
      await showFinalRanking();
      return;
    }

    if (state.presentable_subject_id) {
      stopPolling();
      pendingSubjectId = state.presentable_subject_id;
      const subject = state.subjects.find((s) => s.id === pendingSubjectId);
      document.getElementById("sequentialSubjectName").textContent = subject.name;
      revealStage.classList.add("hidden");
      sequentialStandbyPanel.classList.remove("hidden");
      return;
    }

    if (state.all_subjects_presented) {
      stopPolling();
      finalRankingPanel.classList.remove("hidden");
      return;
    }

    const current = state.subjects.find((s) => s.id === state.current_subject_id);
    document.getElementById("sequentialWaitingText").textContent = current
      ? `${current.name}: ${current.submitted_count} / ${current.scorer_count} 名が提出済み`
      : "採点の開始を待っています。";
    revealStage.classList.add("hidden");
    sequentialWaitingPanel.classList.remove("hidden");
    pollingStopped = false;
    scheduleNextPoll();
  }

  async function showFinalRanking() {
    currentSummary = await loadSummary();
    if (!currentSummary) return;
    showRanking(currentSummary);
    if (currentSummary.project.status === "FINISHED") {
      setFinishedPanelVisible(true);
    }
  }

  document.getElementById("revealSubjectButton").addEventListener("click", async () => {
    let payload;
    try {
      payload = await apiFetch(
        `/api/projects/${projectId}/subjects/${pendingSubjectId}/result`
      );
    } catch (err) {
      showMessage(err.message, { isError: true });
      return;
    }
    hideSequentialPanels();
    prepareAudio();
    revealStage.classList.remove("hidden");
    // BATCHとまったく同じ演出engineを再利用する
    await revealSubjectSequence(payload.subject, () => {
      confirmNextPanel.classList.remove("hidden");
    });
  });

  document.getElementById("confirmNextButton").addEventListener("click", async () => {
    try {
      await apiFetch(
        `/api/projects/${projectId}/subjects/${pendingSubjectId}/present`,
        { method: "POST" }
      );
    } catch (err) {
      showMessage(err.message, { isError: true });
      return;
    }
    revealStage.classList.add("hidden");
    pendingSubjectId = null;
    pollingStopped = false;
    const state = await loadState();
    if (state) await renderSequential(state);
  });

  document.getElementById("finalRankingButton").addEventListener("click", async () => {
    // Subjectの発表は全て終わっているので、ここからは最終ランキングへ進むだけ。
    // Project statusは SCORING -> LOCKED -> PRESENTING と前向きにしか動かさない。
    try {
      for (const target of ["LOCKED", "PRESENTING"]) {
        await apiFetch(`/api/projects/${projectId}/transition`, {
          method: "POST",
          body: JSON.stringify({ target_status: target }),
        });
      }
    } catch (err) {
      showMessage(err.message, { isError: true });
      return;
    }
    hideSequentialPanels();
    await showFinalRanking();
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !pollingStopped) {
      pollState();
    }
  });

  // -------------------------------------------------------------------------

  async function initBatch() {
    currentSummary = await loadSummary();
    if (!currentSummary) return;

    if (currentSummary.project.status === "LOCKED") {
      standbyPanel.classList.remove("hidden");
    } else if (currentSummary.project.status === "PRESENTING") {
      // 再訪問時は最初の被採点者の待機画面から始める。演出は必ずHostの
      // クリックで始まり、勝手に自動再生はしない(演出の途中状態は永続化しない)。
      startBatchPresentation(currentSummary);
    } else if (currentSummary.project.status === "FINISHED") {
      // 発表済み。ランキングを出したうえで、再生・再閲覧・戻る導線を提供する。
      // ここからFINISHEDを巻き戻す操作は一切行わない。
      showRanking(currentSummary);
      setFinishedPanelVisible(true);
    }
  }

  async function init() {
    document.getElementById("backToDashboardLink").href = `/host/${projectId}`;
    document.getElementById("finishedAnalysisLink").href = `/host/${projectId}/analysis`;
    // SEQUENTIALの採点待ち画面用。発表画面を出したまま別タブで進捗を見るため
    // target="_blank"(テンプレート側)で開く。
    document.getElementById("sequentialDashboardLink").href = `/host/${projectId}`;

    stageControls.classList.remove("hidden");
    setBusy(false);

    const state = await loadState();
    if (!state) return;

    if (state.project.presentation_mode === "SEQUENTIAL") {
      await renderSequential(state);
    } else {
      await initBatch();
    }
  }

  init();
})();
