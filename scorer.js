const steps = {
  project: document.querySelector("#projectStep"),
  judge: document.querySelector("#judgeStep"),
  entry: document.querySelector("#entryStep"),
};

const els = {
  projectList: document.querySelector("#projectList"),
  selectedProjectName: document.querySelector("#selectedProjectName"),
  judgeGrid: document.querySelector("#judgeGrid"),
  activeJudgeName: document.querySelector("#activeJudgeName"),
  activeProjectName: document.querySelector("#activeProjectName"),
  activeJudgeLabel: document.querySelector("#activeJudgeLabel"),
  inputWindowNotice: document.querySelector("#inputWindowNotice"),
  scoreSheet: document.querySelector("#scoreSheet"),
  submitPanel: document.querySelector("#submitPanel"),
  completionText: document.querySelector("#completionText"),
  submitStatusText: document.querySelector("#submitStatusText"),
  messageBox: document.querySelector("#messageBox"),
  backToProjectsButton: document.querySelector("#backToProjectsButton"),
  changeJudgeButton: document.querySelector("#changeJudgeButton"),
};

const DEFAULT_SCORE_FIELDS = [
  { key: "originality", label: "独創性" },
  { key: "usefulness", label: "実用性" },
  { key: "design", label: "UI/UXデザイン" },
  { key: "technical", label: "技術力" },
  { key: "scalability", label: "拡張性" },
];

const initialParams = new URLSearchParams(window.location.search);
const initialProjectId = initialParams.get("project") || "";
const initialJudgeId = initialParams.get("judge") || "";

let projects = [];
let activeProject = null;
let activeSession = null;
let savedScores = {};
let activeEntryWindow = { open: true, restricted: false, label: null, start: null, end: null, message: "入力受付中です。" };
const saveTimers = new Map();

function currentScoreFields() {
  return activeProject?.scoreFields?.length ? activeProject.scoreFields : DEFAULT_SCORE_FIELDS;
}

function isTeamFinalized(teamId) {
  return Boolean(savedScores[teamId]?.finalizedAt);
}

els.backToProjectsButton.addEventListener("click", () => showStep("project"));
els.changeJudgeButton.addEventListener("click", () => showStep("judge"));

loadProjects();
setInterval(() => {
  if (activeSession) updateEntryWindowState();
}, 15000);

async function loadProjects() {
  try {
    const response = await fetch("/api/projects");
    if (!response.ok) throw new Error("プロジェクトを読み込めませんでした。");
    const data = await response.json();
    projects = data.projects || [];
    if (initialProjectId && initialJudgeId && selectInitialJudge()) return;
    renderProjects();
  } catch (error) {
    showMessage(error.message);
  }
}

function selectInitialJudge() {
  const project = projects.find((item) => item.id === initialProjectId);
  const judge = project?.judges?.find((item) => item.id === initialJudgeId);
  if (!project || !judge) {
    showMessage("共有URLのプロジェクトまたは採点者が見つかりません。");
    return false;
  }
  activeProject = project;
  startJudgeSession(judge.id);
  return true;
}

function renderProjects() {
  els.projectList.replaceChildren();
  if (!projects.length) {
    els.projectList.append(emptyState("採点プロジェクトがありません。"));
    return;
  }

  projects.forEach((project) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "project-button";
    button.innerHTML = `
      <strong></strong>
      <span></span>
    `;
    button.querySelector("strong").textContent = project.name;
    button.querySelector("span").textContent = `${project.teams.length}チーム / 採点者${project.judges.length}人`;
    button.addEventListener("click", () => selectProject(project.id));
    els.projectList.append(button);
  });
}

function selectProject(projectId) {
  activeProject = projects.find((project) => project.id === projectId);
  if (!activeProject) return;
  els.selectedProjectName.textContent = activeProject.name;
  renderJudges();
  showStep("judge");
}

function renderJudges() {
  els.judgeGrid.replaceChildren();
  loadProjectSummary(activeProject.id).then((summary) => {
    els.judgeGrid.querySelectorAll(".judge-button").forEach((button) => {
      const judge = summary?.judges?.find((item) => item.id === button.dataset.judgeId);
      const finalizedCount = judge?.finalizedTeamCount ?? 0;
      const totalCount = judge?.totalTeamCount ?? 0;
      const allDone = Boolean(judge?.allTeamsFinalized);
      button.classList.toggle("all-finalized", allDone);
      button.querySelector("span").textContent = allDone
        ? "入力済み"
        : finalizedCount > 0
          ? `確定 ${finalizedCount}/${totalCount}`
          : "この名前で入る";
    });
  });
  activeProject.judges.forEach((judge) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "judge-button";
    button.dataset.judgeId = judge.id;
    button.innerHTML = `
      <strong></strong>
      <span>この名前で入る</span>
    `;
    button.querySelector("strong").textContent = judge.name;
    button.addEventListener("click", () => startJudgeSession(judge.id));
    els.judgeGrid.append(button);
  });
}

async function startJudgeSession(judgeId) {
  try {
    const response = await fetch("/api/judge-session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ projectId: activeProject.id, judgeId }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "入力画面を開始できませんでした。");
    activeProject = data.project;
    activeSession = data.session;
    activeEntryWindow = data.entryWindow;
    savedScores = await loadScores(activeSession);
    renderEntry(activeSession);
    showStep("entry");
  } catch (error) {
    showMessage(error.message);
  }
}

async function loadScores(session) {
  const params = new URLSearchParams({
    projectId: session.projectId,
    judgeId: session.judgeId,
  });
  const response = await fetch(`/api/scores?${params.toString()}`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "保存済みの採点を読み込めませんでした。");
  if (data.entryWindow) activeEntryWindow = data.entryWindow;
  return data.scores || {};
}

function renderEntry(session) {
  els.activeJudgeName.textContent = session.judgeName;
  els.activeProjectName.textContent = session.projectName;
  els.activeJudgeLabel.textContent = session.judgeName;
  els.scoreSheet.replaceChildren();
  updateEntryWindowNotice();
  activeProject.teams.forEach((team) => {
    const card = document.createElement("article");
    card.className = "score-team-card";
    card.dataset.teamId = team.id;
    card.innerHTML = `
      <div class="score-team-head">
        <div class="team-title">
          <span class="team-order"></span>
          <strong></strong>
        </div>
        <div class="team-total">
          <span>合計</span>
          <strong data-total>0</strong>
        </div>
      </div>
      <div class="score-field-grid"></div>
      <label class="comment-field">
        <span>一言（任意）</span>
        <textarea rows="3" maxlength="160" placeholder="コメントがあれば入力"></textarea>
      </label>
      <div class="score-team-footer">
        <div class="save-status" data-status>未保存</div>
        <button class="finalize-button" data-finalize-button type="button" disabled>この発表者の採点を確定</button>
      </div>
    `;
    card.querySelector(".team-order").textContent = team.order;
    card.querySelector(".team-title strong").textContent = team.name;

    const scoreGrid = card.querySelector(".score-field-grid");
    const current = savedScores[team.id] || {};
    currentScoreFields().forEach((field) => {
      const label = document.createElement("label");
      label.className = "score-field";
      label.innerHTML = `
        <span></span>
        <input type="range" min="0" max="20" step="1">
        <output>--</output>
      `;
      label.querySelector("span").textContent = field.label;
      const input = label.querySelector("input");
      const output = label.querySelector("output");
      input.name = field.key;
      const currentValue = current[field.key];
      const hasValue = currentValue !== "" && currentValue != null;
      input.value = hasValue ? currentValue : 0;
      input.dataset.scored = hasValue ? "true" : "false";
      output.textContent = hasValue ? `${currentValue}/20` : "--/20";
      input.addEventListener("input", () => {
        if (!isEntryWindowOpen()) {
          input.value = input.dataset.previousValue || input.value;
          showMessage(inputWindowClosedMessage());
          updateEntryWindowState();
          return;
        }
        input.dataset.scored = "true";
        input.dataset.previousValue = input.value;
        output.textContent = `${input.value}/20`;
        updateTeamTotal(card);
        updateProgressReadout();
        queueSave(team.id, card);
      });
      scoreGrid.append(label);
    });

    const comment = card.querySelector("textarea");
    comment.value = current.comment || "";
    comment.addEventListener("input", () => {
      if (!isEntryWindowOpen()) {
        comment.value = comment.dataset.previousValue || "";
        showMessage(inputWindowClosedMessage());
        updateEntryWindowState();
        return;
      }
      comment.dataset.previousValue = comment.value;
      queueSave(team.id, card);
    });
    comment.dataset.previousValue = comment.value;

    card.querySelector("[data-finalize-button]").addEventListener("click", () => handleFinalizeClick(team.id, card));

    updateTeamTotal(card);
    card.querySelector("[data-status]").textContent = current.updatedAt ? "保存済み" : "未保存";
    els.scoreSheet.append(card);
  });
  updateProgressReadout();
}

function updateTeamTotal(card) {
  const total = currentScoreFields().reduce((sum, field) => {
    const input = card.querySelector(`[name="${field.key}"]`);
    return sum + (input.dataset.scored === "true" ? Number(input.value) : 0);
  }, 0);
  card.querySelector("[data-total]").textContent = total;
  updateTeamCardState(card);
}

function queueSave(teamId, card) {
  if (isTeamFinalized(teamId)) return;
  if (!isEntryWindowOpen()) {
    showMessage(inputWindowClosedMessage());
    updateEntryWindowState();
    return;
  }
  const status = card.querySelector("[data-status]");
  status.textContent = "保存中...";
  clearTimeout(saveTimers.get(teamId));
  saveTimers.set(
    teamId,
    setTimeout(() => {
      saveTeamScore(teamId, card);
    }, 360),
  );
}

async function saveTeamScore(teamId, card) {
  if (isTeamFinalized(teamId)) return false;
  if (!isEntryWindowOpen()) {
    card.querySelector("[data-status]").textContent = "時間外";
    showMessage(inputWindowClosedMessage());
    updateEntryWindowState();
    return false;
  }
  const entry = collectTeamEntry(card);
  try {
    const response = await fetch("/api/scores", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        projectId: activeSession.projectId,
        judgeId: activeSession.judgeId,
        teamId,
        entry,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "採点を保存できませんでした。");
    savedScores[teamId] = data.entry;
    card.querySelector("[data-status]").textContent = "保存済み";
    return true;
  } catch (error) {
    card.querySelector("[data-status]").textContent = "保存失敗";
    showMessage(error.message);
    return false;
  }
}

function collectTeamEntry(card) {
  const entry = {};
  currentScoreFields().forEach((field) => {
    const input = card.querySelector(`[name="${field.key}"]`);
    entry[field.key] = input.dataset.scored === "true" ? input.value : "";
  });
  entry.comment = card.querySelector("textarea").value;
  return entry;
}

function updateProgressReadout() {
  const total = activeProject.teams.length;
  const finalizedCount = activeProject.teams.filter((team) => isTeamFinalized(team.id)).length;
  const windowOpen = isEntryWindowOpen();
  els.completionText.textContent = `${finalizedCount}/${total} 確定済み`;
  els.submitStatusText.textContent = !windowOpen
    ? inputWindowClosedMessage()
    : finalizedCount === total && total > 0
      ? "すべてのチームの採点を確定しました"
      : "各チームのカードで採点を確定してください";
  els.submitPanel?.classList.toggle("all-finalized", finalizedCount === total && total > 0);
  updateEntryWindowNotice();
  applyEntryWindowLock();
}

function applyEntryWindowLock() {
  els.scoreSheet.querySelectorAll(".score-team-card").forEach((card) => {
    const teamId = card.dataset.teamId;
    const locked = isTeamFinalized(teamId) || !isEntryWindowOpen();
    card.querySelectorAll("input, textarea").forEach((input) => {
      input.disabled = locked;
    });
    updateTeamCardState(card);
    updateFinalizeButtonState(card);
  });
}

function updateFinalizeButtonState(card) {
  const button = card.querySelector("[data-finalize-button]");
  if (!button) return;
  const teamId = card.dataset.teamId;
  if (isTeamFinalized(teamId)) {
    button.disabled = true;
    button.textContent = "確定済み";
  } else if (!isEntryWindowOpen()) {
    button.disabled = true;
    button.textContent = "入力時間外";
  } else if (!isTeamEntered(card)) {
    button.disabled = true;
    button.textContent = "この発表者の採点を確定";
  } else {
    button.disabled = false;
    button.textContent = "この発表者の採点を確定";
  }
}

async function handleFinalizeClick(teamId, card) {
  const button = card.querySelector("[data-finalize-button]");
  button.disabled = true;
  button.textContent = "確定中...";
  hideMessage();

  clearTimeout(saveTimers.get(teamId));
  saveTimers.delete(teamId);

  const saved = await saveTeamScore(teamId, card);
  if (!saved) {
    updateFinalizeButtonState(card);
    return;
  }

  try {
    const response = await fetch("/api/scores/finalize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        projectId: activeSession.projectId,
        judgeId: activeSession.judgeId,
        teamId,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "確定できませんでした。");
    savedScores[teamId] = data.entry;
    card.querySelector("[data-status]").textContent = "確定済み";
    updateProgressReadout();
  } catch (error) {
    showMessage(error.message);
    updateFinalizeButtonState(card);
  }
}

async function loadProjectSummary(projectId) {
  try {
    const response = await fetch(`/api/result/summary?projectId=${encodeURIComponent(projectId)}`);
    if (!response.ok) return null;
    return response.json();
  } catch {
    return null;
  }
}

function isEntryWindowOpen(now = new Date()) {
  if (!activeEntryWindow.restricted) return true;
  return now >= new Date(activeEntryWindow.start) && now <= new Date(activeEntryWindow.end);
}

function inputWindowClosedMessage() {
  if (!activeEntryWindow.restricted) return "";
  const now = new Date();
  if (now < new Date(activeEntryWindow.start)) {
    return `入力開始前です。入力可能時間は ${activeEntryWindow.label} です。`;
  }
  return `入力時間は終了しました。入力可能時間は ${activeEntryWindow.label} でした。`;
}

function updateEntryWindowNotice() {
  if (!els.inputWindowNotice) return;
  const open = isEntryWindowOpen();
  els.inputWindowNotice.classList.toggle("closed", !open);
  els.inputWindowNotice.classList.toggle("open", open);
  els.inputWindowNotice.textContent = open
    ? activeEntryWindow.restricted
      ? `入力受付中: ${activeEntryWindow.label}`
      : "入力受付中です"
    : inputWindowClosedMessage();
}

function updateEntryWindowState() {
  updateEntryWindowNotice();
  updateProgressReadout();
}

function isTeamEntered(card) {
  return currentScoreFields().every((field) => {
    const input = card.querySelector(`[name="${field.key}"]`);
    return input?.dataset.scored === "true";
  });
}

function updateTeamCardState(card) {
  const teamId = card.dataset.teamId;
  const finalized = isTeamFinalized(teamId);
  const entered = isTeamEntered(card);
  card.classList.toggle("is-entered", entered && !finalized);
  card.classList.toggle("is-finalized", finalized);
  card.classList.toggle("is-closed", !isEntryWindowOpen() && !finalized);
  const status = card.querySelector("[data-status]");
  if (status && finalized) {
    status.textContent = "確定済み";
  } else if (status && entered && status.textContent === "未保存") {
    status.textContent = "入力済み";
  }
}

function showStep(name) {
  Object.entries(steps).forEach(([key, element]) => {
    element.classList.toggle("hidden", key !== name);
  });
  hideMessage();
}

function emptyState(text) {
  const div = document.createElement("div");
  div.className = "empty-row";
  div.textContent = text;
  return div;
}

function showMessage(text) {
  els.messageBox.textContent = text;
  els.messageBox.classList.remove("hidden");
}

function hideMessage() {
  els.messageBox.classList.add("hidden");
}
