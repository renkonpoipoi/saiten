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
  submitButton: document.querySelector("#submitButton"),
  messageBox: document.querySelector("#messageBox"),
  backToProjectsButton: document.querySelector("#backToProjectsButton"),
  changeJudgeButton: document.querySelector("#changeJudgeButton"),
};

const scoreFields = [
  { key: "originality", label: "独創性" },
  { key: "usefulness", label: "実用性" },
  { key: "design", label: "UI/UXデザイン" },
  { key: "technical", label: "技術力" },
  { key: "scalability", label: "拡張性" },
];

const initialParams = new URLSearchParams(window.location.search);
const initialProjectId = initialParams.get("project") || "";
const initialJudgeId = initialParams.get("judge") || "";
const ENTRY_WINDOW_START = new Date("2026-07-02T14:30:00+09:00");
const ENTRY_WINDOW_END = new Date("2026-07-02T16:10:00+09:00");
const ENTRY_WINDOW_LABEL = "2026年7月2日 14:30〜16:10";

let projects = [];
let activeProject = null;
let activeSession = null;
let savedScores = {};
let activeSubmitted = false;
const saveTimers = new Map();

els.backToProjectsButton.addEventListener("click", () => showStep("project"));
els.changeJudgeButton.addEventListener("click", () => showStep("judge"));
els.submitButton.addEventListener("click", submitScores);

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
      const submitted = Boolean(judge?.submitted);
      button.classList.toggle("submitted", submitted);
      button.querySelector("span").textContent = submitted ? "入力済み" : "この名前で入る";
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
  activeSubmitted = Boolean(data.submitted);
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
      <div class="save-status" data-status>未保存</div>
    `;
    card.querySelector(".team-order").textContent = team.order;
    card.querySelector(".team-title strong").textContent = team.name;

    const scoreGrid = card.querySelector(".score-field-grid");
    const current = savedScores[team.id] || {};
    scoreFields.forEach((field) => {
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
        updateSubmitState();
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

    updateTeamTotal(card);
    card.querySelector("[data-status]").textContent = current.updatedAt ? "保存済み" : "未保存";
    els.scoreSheet.append(card);
  });
  updateSubmitState();
  setSubmittedMode(activeSubmitted);
}

function updateTeamTotal(card) {
  const total = scoreFields.reduce((sum, field) => {
    const input = card.querySelector(`[name="${field.key}"]`);
    return sum + (input.dataset.scored === "true" ? Number(input.value) : 0);
  }, 0);
  card.querySelector("[data-total]").textContent = total;
  updateTeamCardState(card);
}

function queueSave(teamId, card) {
  if (activeSubmitted) return;
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
  scoreFields.forEach((field) => {
    const input = card.querySelector(`[name="${field.key}"]`);
    entry[field.key] = input.dataset.scored === "true" ? input.value : "";
  });
  entry.comment = card.querySelector("textarea").value;
  return entry;
}

function updateSubmitState() {
  const missing = missingRequiredInputs();
  const complete = missing.length === 0;
  els.completionText.textContent = complete ? "入力完了" : `未入力 ${missing.length}項目`;
  const windowOpen = isEntryWindowOpen();
  els.submitStatusText.textContent = !windowOpen
    ? inputWindowClosedMessage()
    : activeSubmitted
    ? "提出済みです"
    : complete
      ? "提出できます。提出後は変更できません。"
      : "3チームすべて入力すると提出できます";
  els.submitButton.disabled = activeSubmitted || !complete || !windowOpen;
  updateEntryWindowNotice();
  els.scoreSheet.querySelectorAll(".score-team-card").forEach(updateTeamCardState);
}

function missingRequiredInputs() {
  const missing = [];
  els.scoreSheet.querySelectorAll(".score-team-card").forEach((card) => {
    const teamName = card.querySelector(".team-title strong").textContent;
    scoreFields.forEach((field) => {
      const input = card.querySelector(`[name="${field.key}"]`);
      if (input.dataset.scored !== "true") missing.push(`${teamName} ${field.label}`);
    });
  });
  return missing;
}

async function submitScores() {
  if (activeSubmitted || missingRequiredInputs().length) return;
  if (!isEntryWindowOpen()) {
    showMessage(inputWindowClosedMessage());
    updateEntryWindowState();
    return;
  }
  els.submitButton.disabled = true;
  els.submitStatusText.textContent = "保存して提出中...";
  hideMessage();

  for (const [teamId, timer] of saveTimers.entries()) {
    clearTimeout(timer);
    saveTimers.delete(teamId);
  }

  const cards = [...els.scoreSheet.querySelectorAll(".score-team-card")];
  for (const card of cards) {
    const ok = await saveTeamScore(card.dataset.teamId, card);
    if (!ok) {
      updateSubmitState();
      return;
    }
  }

  try {
    const response = await fetch("/api/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        projectId: activeSession.projectId,
        judgeId: activeSession.judgeId,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "提出できませんでした。");
    activeSubmitted = true;
    setSubmittedMode(true);
    updateSubmitState();
  } catch (error) {
    showMessage(error.message);
    updateSubmitState();
  }
}

function setSubmittedMode(submitted) {
  els.scoreSheet.querySelectorAll("input, textarea").forEach((input) => {
    input.disabled = submitted || !isEntryWindowOpen();
  });
  els.submitButton.textContent = submitted ? "提出済み" : "提出";
  els.submitPanel?.classList.toggle("submitted", submitted);
  els.scoreSheet.querySelectorAll(".score-team-card").forEach(updateTeamCardState);
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
  return now >= ENTRY_WINDOW_START && now <= ENTRY_WINDOW_END;
}

function inputWindowClosedMessage() {
  const now = new Date();
  if (now < ENTRY_WINDOW_START) return `入力開始前です。入力可能時間は ${ENTRY_WINDOW_LABEL} です。`;
  return `入力時間は終了しました。入力可能時間は ${ENTRY_WINDOW_LABEL} でした。`;
}

function updateEntryWindowNotice() {
  if (!els.inputWindowNotice) return;
  const open = isEntryWindowOpen();
  els.inputWindowNotice.classList.toggle("closed", !open);
  els.inputWindowNotice.classList.toggle("open", open);
  els.inputWindowNotice.textContent = open
    ? `入力受付中: ${ENTRY_WINDOW_LABEL}`
    : inputWindowClosedMessage();
}

function updateEntryWindowState() {
  updateEntryWindowNotice();
  setSubmittedMode(activeSubmitted);
  updateSubmitState();
}

function isTeamEntered(card) {
  return scoreFields.every((field) => {
    const input = card.querySelector(`[name="${field.key}"]`);
    return input?.dataset.scored === "true";
  });
}

function updateTeamCardState(card) {
  const entered = isTeamEntered(card);
  card.classList.toggle("is-entered", entered);
  card.classList.toggle("is-submitted", activeSubmitted);
  card.classList.toggle("is-closed", !isEntryWindowOpen() && !activeSubmitted);
  const status = card.querySelector("[data-status]");
  if (status && entered && !activeSubmitted && status.textContent === "未保存") {
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
