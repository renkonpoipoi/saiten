(function () {
  const projectId = window.PROJECT_ID;

  const standbyPanel = document.getElementById("standbyPanel");
  const revealStage = document.getElementById("revealStage");
  const rankingPanel = document.getElementById("rankingPanel");
  const finishedPanel = document.getElementById("finishedPanel");

  // 取得済みの集計結果。FINISHED後のreplay/ランキング再表示は全てこの値の
  // 再描画だけで行い、サーバーの状態は一切変更しない。
  let currentSummary = null;

  let hitAudio = null;
  let stingAudio = null;

  function prepareAudio() {
    if (!hitAudio) {
      hitAudio = new Audio("/static/assets/reveal-hit.m4a");
      stingAudio = new Audio("/static/assets/reveal-sting.m4a");
    }
  }

  function playHit() {
    if (!hitAudio) return;
    try {
      hitAudio.currentTime = 0;
      hitAudio.play().catch(() => {});
    } catch (err) {
      /* 再生できない環境では無視する */
    }
  }

  function playSting() {
    if (!stingAudio) return;
    try {
      stingAudio.currentTime = 0;
      stingAudio.play().catch(() => {});
    } catch (err) {
      /* 再生できない環境では無視する */
    }
  }

  function wait(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function scoreTone(total, theoreticalMax) {
    if (!theoreticalMax) return "";
    const ratio = total / theoreticalMax;
    if (ratio >= 0.95) return "gold";
    if (ratio >= 0.9) return "silver";
    if (ratio >= 0.85) return "bronze";
    return "";
  }

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

    const finishButton = document.getElementById("finishButton");
    finishButton.classList.toggle("hidden", summary.project.status !== "PRESENTING");
  }

  async function revealSubject(subject, theoreticalMax) {
    revealStage.classList.remove("hidden");
    document.getElementById("revealSubjectName").textContent = subject.name;
    const judgeRow = document.getElementById("revealJudgeRow");
    judgeRow.innerHTML = "";
    const totalEl = document.getElementById("revealTotal");
    totalEl.textContent = "0";

    let runningTotal = 0;
    for (const judge of subject.judge_totals) {
      await wait(500);
      playHit();
      runningTotal += judge.total;

      const bubble = document.createElement("div");
      const tone = scoreTone(judge.total, theoreticalMax);
      bubble.className = `judge-score-bubble${tone ? " " + tone : ""}`;
      const nameEl = document.createElement("span");
      nameEl.className = "name";
      nameEl.textContent = judge.display_name;
      const scoreEl = document.createElement("span");
      scoreEl.className = "score";
      scoreEl.textContent = judge.total;
      bubble.append(nameEl, scoreEl);
      judgeRow.appendChild(bubble);

      totalEl.textContent = String(runningTotal);
    }
    await wait(700);
  }

  async function runRevealSequence(summary) {
    prepareAudio();
    standbyPanel.classList.add("hidden");
    rankingPanel.classList.add("hidden");
    playSting();

    const ordered = [...summary.subjects].sort((a, b) => a.sort_order - b.sort_order);
    for (const subject of ordered) {
      await revealSubject(subject, summary.theoretical_max_total);
      await wait(500);
    }
    await wait(600);
    showRanking(summary);
  }

  function setFinishedPanelVisible(visible) {
    finishedPanel.classList.toggle("hidden", !visible);
  }

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
    currentSummary = await loadSummary();
    if (currentSummary) await runRevealSequence(currentSummary);
  });

  // --- FINISHED後の導線。いずれも表示操作のみで、APIはGETしか使わない ---

  document.getElementById("replayButton").addEventListener("click", async () => {
    if (!currentSummary) return;
    setFinishedPanelVisible(false);
    await runRevealSequence(currentSummary);
    setFinishedPanelVisible(true);
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
      document.getElementById("finishButton").classList.add("hidden");
    } catch (err) {
      showMessage(err.message, { isError: true });
    }
  });

  async function init() {
    currentSummary = await loadSummary();
    if (!currentSummary) return;

    document.getElementById("backToDashboardLink").href = `/host/${projectId}`;
    document.getElementById("finishedAnalysisLink").href = `/host/${projectId}/analysis`;

    if (currentSummary.project.status === "LOCKED") {
      standbyPanel.classList.remove("hidden");
    } else if (currentSummary.project.status === "PRESENTING") {
      // 再訪問時は毎回演出を最初からやり直す(演出の途中状態は永続化しない)
      await runRevealSequence(currentSummary);
    } else if (currentSummary.project.status === "FINISHED") {
      // 発表済み。ランキングを出したうえで、再生・再閲覧・戻る導線を提供する。
      // ここからFINISHEDを巻き戻す操作は一切行わない。
      showRanking(currentSummary);
      setFinishedPanelVisible(true);
    }
  }

  init();
})();
