const els = {
  dashboard: document.querySelector("#dashboard"),
  revealStage: document.querySelector("#revealStage"),
  projectName: document.querySelector("#projectName"),
  refreshButton: document.querySelector("#refreshButton"),
  logoutButton: document.querySelector("#logoutButton"),
  startRevealButton: document.querySelector("#startRevealButton"),
  backButton: document.querySelector("#backButton"),
  submitCount: document.querySelector("#submitCount"),
  readyState: document.querySelector("#readyState"),
  judgeStatusGrid: document.querySelector("#judgeStatusGrid"),
  previewList: document.querySelector("#previewList"),
  revealTeam: document.querySelector("#revealTeam"),
  revealScore: document.querySelector("#revealScore"),
  revealSub: document.querySelector("#revealSub"),
  revealRanking: document.querySelector("#revealRanking"),
  messageBox: document.querySelector("#messageBox"),
};

let summary = null;
let revealRunning = false;

els.refreshButton.addEventListener("click", loadSummary);
els.logoutButton.addEventListener("click", logout);
els.startRevealButton.addEventListener("click", startReveal);
els.backButton.addEventListener("click", () => {
  els.revealStage.classList.add("hidden");
  els.dashboard.classList.remove("hidden");
});

loadSummary();
setInterval(loadSummary, 8000);

async function loadSummary() {
  try {
    const response = await fetch("/api/admin/summary");
    const data = await response.json();
    if (response.status === 401) {
      location.href = "/admin";
      return;
    }
    if (!response.ok) throw new Error(data.error || "管理データを読み込めませんでした。");
    summary = data;
    renderSummary();
    hideMessage();
  } catch (error) {
    showMessage(error.message);
  }
}

async function logout() {
  await fetch("/api/admin/logout", { method: "POST" });
  location.href = "/admin";
}

function renderSummary() {
  els.projectName.textContent = summary.project.name;
  els.submitCount.textContent = `${summary.submittedCount} / ${summary.totalJudges}`;
  els.readyState.textContent = summary.allSubmitted ? "発表できます" : "提出待ち";
  els.startRevealButton.disabled = !summary.allSubmitted || revealRunning;
  renderJudges();
  renderPreview();
}

function renderJudges() {
  els.judgeStatusGrid.replaceChildren();
  summary.judges.forEach((judge) => {
    const card = document.createElement("article");
    card.className = `judge-card ${judge.submitted ? "submitted" : "pending"}`;
    card.innerHTML = `
      <strong></strong>
      <span></span>
      <div class="status-pill"></div>
    `;
    card.querySelector("strong").textContent = judge.name;
    card.querySelector("span").textContent = judge.submitted
      ? formatDate(judge.submittedAt)
      : judge.complete
        ? "入力完了・未提出"
        : `未入力 ${judge.missingCount}項目`;
    card.querySelector(".status-pill").textContent = judge.submitted ? "提出済み" : "未提出";
    els.judgeStatusGrid.append(card);
  });
}

function renderPreview() {
  els.previewList.replaceChildren();
  if (!summary.teamResults.length) {
    els.previewList.append(emptyRow("まだ集計できる提出データがありません。"));
    return;
  }
  summary.teamResults.forEach((team, index) => {
    const row = document.createElement("div");
    row.className = "preview-row";
    row.innerHTML = `
      <div class="preview-rank"></div>
      <div>
        <strong></strong>
        <span></span>
      </div>
      <div class="preview-total"></div>
    `;
    row.querySelector(".preview-rank").textContent = index + 1;
    row.querySelector("strong").textContent = team.name;
    row.querySelector("span").textContent = `${team.judgeTotals.length}人分 / 平均 ${team.average}`;
    row.querySelector(".preview-total").textContent = team.total;
    els.previewList.append(row);
  });
}

async function startReveal() {
  if (!summary?.allSubmitted || revealRunning) return;
  revealRunning = true;
  els.startRevealButton.disabled = true;
  els.dashboard.classList.add("hidden");
  els.revealStage.classList.remove("hidden");
  els.revealRanking.replaceChildren();
  els.revealTeam.textContent = "READY";
  els.revealScore.textContent = "---";
  els.revealSub.textContent = "結果発表";

  const revealOrder = [...summary.teamResults].sort((a, b) => a.order - b.order);
  for (const team of revealOrder) {
    await revealTeam(team);
  }
  renderFinalRanking();
  els.revealTeam.textContent = "FINAL RANKING";
  els.revealScore.textContent = "決定";
  els.revealSub.textContent = "最終結果";
  revealRunning = false;
  els.startRevealButton.disabled = !summary.allSubmitted;
}

async function revealTeam(team) {
  els.revealTeam.textContent = team.name;
  els.revealSub.textContent = "TOTAL SCORE";
  els.revealScore.textContent = "0";
  await wait(420);
  await countTo(team.total);
  els.revealScore.classList.remove("pop");
  void els.revealScore.offsetWidth;
  els.revealScore.classList.add("pop");
  await wait(900);
}

async function countTo(target) {
  const steps = 24;
  const start = Math.max(0, target - 120);
  for (let i = 0; i <= steps; i += 1) {
    const value = Math.round(start + ((target - start) * i) / steps);
    els.revealScore.textContent = value;
    await wait(36);
  }
}

function renderFinalRanking() {
  els.revealRanking.replaceChildren();
  summary.teamResults.forEach((team, index) => {
    const row = document.createElement("div");
    row.className = "reveal-rank-row";
    row.style.animationDelay = `${index * 0.12}s`;
    row.innerHTML = `
      <strong></strong>
      <span></span>
      <b></b>
    `;
    row.querySelector("strong").textContent = `${index + 1}`;
    row.querySelector("span").textContent = team.name;
    row.querySelector("b").textContent = team.total;
    els.revealRanking.append(row);
  });
}

function emptyRow(text) {
  const div = document.createElement("div");
  div.className = "preview-row";
  div.textContent = text;
  return div;
}

function formatDate(value) {
  if (!value) return "提出済み";
  return new Date(value).toLocaleString("ja-JP", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function showMessage(text) {
  els.messageBox.textContent = text;
  els.messageBox.classList.remove("hidden");
}

function hideMessage() {
  els.messageBox.classList.add("hidden");
}
