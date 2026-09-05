/* 発表順の抽選ページ (Phase 10B-6)。
 *
 * 重要な原則:
 * - **結果を決めるのは常にサーバー**。ここで回すルーレットは演出でしかなく、
 *   着地するのは Draw API が返した1件だけ。client の Math.random() は
 *   「切替中に何を映すか」しか決めない。
 * - 1クリック = 1回の抽選。連続で自動抽選しない。
 * - Skip は今走っている演出を即座に最終状態へ進めるだけで、
 *   次の抽選を実行しない(=サーバーの状態を変えない)。
 */
(function () {
  const projectId = window.PROJECT_ID;
  const core = window.PresentationCore;
  const audio = window.PresentationAudio;

  const presentationRoot = document.getElementById("presentationRoot");
  const unavailablePanel = document.getElementById("drawUnavailablePanel");
  const unavailableTitle = document.getElementById("drawUnavailableTitle");
  const unavailableText = document.getElementById("drawUnavailableText");
  const stageEl = document.getElementById("drawStage");
  const progressLabel = document.getElementById("drawProgressLabel");
  const drawCard = document.getElementById("drawCard");
  const drawName = document.getElementById("drawName");
  const historyEl = document.getElementById("drawHistory");
  const controlPanel = document.getElementById("drawControlPanel");
  const controlNote = document.getElementById("drawControlNote");
  const drawButton = document.getElementById("drawButton");
  const stageControls = document.getElementById("stageControls");

  const dashboardHref = `/host/${projectId}`;
  document.getElementById("drawDashboardLink").href = dashboardHref;
  document.getElementById("unavailableDashboardLink").href = dashboardHref;

  // 直近に読み込んだ progress。次に送る expected_cursor は必ずこの値を使い、
  // client 側で件数を数え直さない。
  let progress = null;
  let busy = false;

  function wait(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function playSfx(key) {
    if (audio) audio.play(key);
  }

  function setBusy(value) {
    busy = value;
    presentationRoot.dataset.busy = value ? "true" : "false";
    stageControls.dataset.visible = value ? "false" : "true";
    drawButton.disabled = value;
  }

  // ---------------------------------------------------------------------------
  // 演出 runner (Phase 9 と同じ step descriptor 方式)
  // ---------------------------------------------------------------------------

  let runToken = 0;
  let activeSequence = null;
  let spinTimer = null;

  function stopSpin() {
    if (spinTimer !== null) {
      clearTimeout(spinTimer);
      spinTimer = null;
    }
  }

  /** 候補名を、だんだん遅くなる間隔で切り替える。**着地は決めない。** */
  function startSpin(candidates) {
    stopSpin();
    if (!candidates.length) return;
    const intervals = core.drawSpinIntervals();
    let tick = 0;
    let shown = -1;

    const step = () => {
      // 同じ名前が連続すると「止まった」ように見えるので、必ず別の候補へ移す
      let next = Math.floor(Math.random() * candidates.length);
      if (candidates.length > 1 && next === shown) {
        next = (next + 1) % candidates.length;
      }
      shown = next;
      drawName.textContent = candidates[next];

      const delay = intervals[Math.min(tick, intervals.length - 1)];
      tick += 1;
      spinTimer = setTimeout(step, delay);
    };
    step();
  }

  async function runSteps(steps, applyStep) {
    runToken += 1;
    const token = runToken;
    setBusy(true);
    for (const step of steps) {
      if (token !== runToken) return false;
      stageEl.dataset.phase = step.phase;
      applyStep(step);
      if (step.duration > 0) await wait(step.duration);
    }
    if (token !== runToken) return false;
    setBusy(false);
    return true;
  }

  async function playSequence({ steps, applyStep, finalState, onComplete }) {
    activeSequence = { finalState, onComplete };
    const completed = await runSteps(steps, applyStep);
    if (!completed) return false;
    activeSequence = null;
    await onComplete();
    return true;
  }

  /** Skip: 今の演出だけを最終状態へ飛ばす。次の抽選は絶対に行わない。 */
  function skipCurrentSequence() {
    const sequence = activeSequence;
    if (!sequence) return;
    activeSequence = null;
    runToken += 1;
    stopSpin();
    setBusy(false);
    sequence.finalState();
    sequence.onComplete();
  }

  function showResult(name) {
    stopSpin();
    drawName.textContent = name;
    drawCard.dataset.state = "result";
  }

  function applyDrawStep(step, candidates, resultName) {
    switch (step.phase) {
      case "DRAW_INTRO":
        drawCard.dataset.state = "idle";
        drawName.textContent = "";
        break;
      case "DRAW_SPIN":
        drawCard.dataset.state = "spinning";
        startSpin(candidates);
        playSfx("judgeMove");
        break;
      case "DRAW_SETTLE":
        drawCard.dataset.state = "settling";
        break;
      case "DRAW_RESULT":
        showResult(resultName);
        playSfx("total");
        break;
      default:
        break;
    }
  }

  // ---------------------------------------------------------------------------
  // 状態の取得と描画
  // ---------------------------------------------------------------------------

  async function loadProgress() {
    try {
      return await apiFetch(`/api/projects/${projectId}/progress`);
    } catch (err) {
      if (err.status === 403) {
        window.location.href = "/host/login";
        return null;
      }
      showMessage(err.message, { isError: true });
      return null;
    }
  }

  function subjectNames(data) {
    return (data.subjects || []).map((row) => row.name);
  }

  function drawnNames(data) {
    return ((data.draw || {}).drawn || []).map((entry) => entry.name);
  }

  function showUnavailable(title, text) {
    unavailableTitle.textContent = title;
    unavailableText.textContent = text;
    unavailablePanel.classList.remove("hidden");
    stageEl.classList.add("hidden");
    controlPanel.classList.add("hidden");
    stageControls.classList.add("hidden");
  }

  function renderHistory(data) {
    historyEl.innerHTML = "";
    ((data.draw || {}).drawn || []).forEach((entry) => {
      const item = document.createElement("span");
      item.className = "p-draw-history__item";
      const position = document.createElement("span");
      position.className = "position";
      position.textContent = entry.position;
      item.appendChild(position);
      item.appendChild(document.createTextNode(entry.name));
      historyEl.appendChild(item);
    });
  }

  function render(data) {
    progress = data;
    const draw = data.draw || {};

    if (draw.subject_order_mode !== "RANDOM_DRAW") {
      showUnavailable(
        "このプロジェクトは抽選を使いません",
        "発表順は「手動で並べる」設定です。設定画面で並び順を変更できます。"
      );
      return;
    }
    if (data.project_status !== "SCORING") {
      showUnavailable(
        "いまは抽選できません",
        `抽選は採点中(SCORING)のみ行えます。現在の状態: ${data.project_status}`
      );
      return;
    }

    unavailablePanel.classList.add("hidden");
    stageEl.classList.remove("hidden");
    controlPanel.classList.remove("hidden");
    stageControls.classList.remove("hidden");

    const total = (data.subjects || []).length;
    progressLabel.textContent = `発表順抽選 ${draw.draw_cursor} / ${total} 組`;
    renderHistory(data);

    if (draw.remaining_count > 0) {
      controlNote.textContent = `残り ${draw.remaining_count} 組`;
      drawButton.classList.remove("hidden");
      drawButton.disabled = busy;
    } else {
      controlNote.textContent = "全員の発表順が決まりました。";
      drawButton.classList.add("hidden");
    }

    // 抽選済みで、まだ今回の結果を出していない状態(reload直後)では
    // 直前の1組を出したままにする。未来は一切表示しない。
    if (drawCard.dataset.state !== "result") {
      const drawn = drawnNames(data);
      if (drawn.length) {
        drawName.textContent = drawn[drawn.length - 1];
        drawCard.dataset.state = "result";
      } else {
        drawName.textContent = "？？？";
        drawCard.dataset.state = "idle";
      }
    }
  }

  async function refresh() {
    const data = await loadProgress();
    if (data) render(data);
  }

  // ---------------------------------------------------------------------------
  // 抽選(1クリック = 1回)
  // ---------------------------------------------------------------------------

  async function drawNext() {
    if (busy || !progress) return;
    const draw = progress.draw || {};

    // 演出の候補は「全Subject - 抽選済み」。抽選済みを再びルーレットに
    // 出さない。今回の結果は必ずこの集合に含まれる。
    const candidates = core.remainingCandidates(subjectNames(progress), drawnNames(progress));

    setBusy(true);
    let result;
    try {
      result = await apiFetch(`/api/projects/${projectId}/draw-next-subject`, {
        method: "POST",
        body: JSON.stringify({ expected_cursor: draw.draw_cursor }),
      });
    } catch (err) {
      setBusy(false);
      if (err.status === 403) {
        window.location.href = "/host/login";
        return;
      }
      showMessage(err.message, { isError: true });
      await refresh();
      return;
    }

    const resultName = result.subject.name;
    if (result.replayed) {
      // 再送で同じ組が返ってきただけ。次の組を消費してはいない。
      showMessage("同じ組を再表示しました(抽選は進んでいません)。");
    }

    await playSequence({
      steps: core.buildDrawSteps(),
      applyStep: (step) => applyDrawStep(step, candidates, resultName),
      finalState: () => showResult(resultName),
      onComplete: refresh,
    });
  }

  drawButton.addEventListener("click", () => {
    drawNext();
  });

  document.getElementById("skipButton").addEventListener("click", () => {
    skipCurrentSequence();
  });

  document.getElementById("fullscreenButton").addEventListener("click", () => {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {
        showMessage("全画面表示に切り替えられませんでした。", { isError: true });
      });
    } else {
      document.exitFullscreen();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") return;
    if (event.key === "s" || event.key === "S") skipCurrentSequence();
  });

  setBusy(false);
  refresh();
})();
