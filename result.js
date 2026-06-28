const els = {
  standbyPanel: document.querySelector("#standbyPanel"),
  revealStage: document.querySelector("#revealStage"),
  projectName: document.querySelector("#projectName"),
  refreshButton: document.querySelector("#refreshButton"),
  startRevealButton: document.querySelector("#startRevealButton"),
  backButton: document.querySelector("#backButton"),
  submitCount: document.querySelector("#submitCount"),
  readyState: document.querySelector("#readyState"),
  rankingList: document.querySelector("#rankingList"),
  revealTeam: document.querySelector("#revealTeam"),
  revealScore: document.querySelector("#revealScore"),
  revealSub: document.querySelector("#revealSub"),
  revealRanking: document.querySelector("#revealRanking"),
  messageBox: document.querySelector("#messageBox"),
};

const params = new URLSearchParams(window.location.search);
const projectId = params.get("project") || "";
let summary = null;
let revealRunning = false;

els.refreshButton.addEventListener("click", loadSummary);
els.startRevealButton.addEventListener("click", startReveal);
els.backButton.addEventListener("click", () => {
  els.revealStage.classList.add("hidden");
  els.standbyPanel.classList.remove("hidden");
});

loadSummary();
setInterval(() => {
  if (!revealRunning) loadSummary();
}, 8000);

async function loadSummary() {
  try {
    const query = projectId ? `?projectId=${encodeURIComponent(projectId)}` : "";
    const response = await fetch(`/api/result/summary${query}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "結果データを読み込めませんでした。");
    summary = data;
    renderSummary();
    hideMessage();
  } catch (error) {
    showMessage(error.message);
  }
}

function renderSummary() {
  els.projectName.textContent = summary.project.name;
  els.submitCount.textContent = `${summary.submittedCount} / ${summary.totalJudges}`;
  els.readyState.textContent = summary.allSubmitted ? "発表できます" : "提出待ち";
  els.startRevealButton.disabled = !summary.allSubmitted || revealRunning;
  renderRanking();
}

function renderRanking() {
  els.rankingList.replaceChildren();
  if (!summary.teamResults.length) {
    els.rankingList.append(emptyRow("集計できる提出データがまだありません。"));
    return;
  }

  summary.teamResults.forEach((team, index) => {
    const row = document.createElement("article");
    row.className = "ranking-row";
    row.innerHTML = `
      <strong></strong>
      <div class="team-name">
        <em></em>
        <span></span>
      </div>
      <b></b>
    `;
    row.querySelector("strong").textContent = `${index + 1}`;
    row.querySelector("em").textContent = team.name;
    row.querySelector("span").textContent = `${team.judgeTotals.length}人分 / 平均 ${team.average}`;
    row.querySelector("b").textContent = team.total;
    els.rankingList.append(row);
  });
}

async function startReveal() {
  if (!summary?.allSubmitted || revealRunning) return;
  revealRunning = true;
  els.startRevealButton.disabled = true;
  els.standbyPanel.classList.add("hidden");
  els.revealStage.classList.remove("hidden");
  els.revealRanking.replaceChildren();
  els.revealTeam.textContent = "READY";
  els.revealScore.textContent = "---";
  els.revealSub.textContent = "結果発表";

  const revealOrder = [...summary.teamResults].sort((a, b) => a.order - b.order);
  for (const team of revealOrder) {
    await revealTeamScore(team);
  }

  renderFinalRanking();
  els.revealTeam.textContent = "FINAL RANKING";
  els.revealScore.textContent = "決定";
  els.revealSub.textContent = "最終結果";
  revealRunning = false;
  els.startRevealButton.disabled = !summary.allSubmitted;
}

async function revealTeamScore(team) {
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
  div.className = "ranking-row";
  div.textContent = text;
  return div;
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
