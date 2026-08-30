(function () {
  const subjectId = window.SUBJECT_ID;
  const AUTOSAVE_DEBOUNCE_MS = 360;

  let evaluationId = null;
  let evaluationDetail = null;
  let saveTimer = null;

  async function init() {
    let dashboard;
    try {
      dashboard = await apiFetch("/api/scorer/me/evaluations");
    } catch (err) {
      if (err.status === 403) {
        window.location.href = "/join";
        return;
      }
      showMessage(err.message, { isError: true });
      return;
    }
    const row = dashboard.subjects.find((r) => r.subject_id === subjectId);
    if (!row) {
      showMessage("この被採点者はあなたの担当ではありません。", { isError: true });
      return;
    }
    evaluationId = row.evaluation_id;
    await loadDetail();
  }

  async function loadDetail() {
    try {
      evaluationDetail = await apiFetch(`/api/evaluations/${evaluationId}`);
    } catch (err) {
      showMessage(err.message, { isError: true });
      return;
    }
    render();
  }

  function render() {
    document.getElementById("subjectNameHeading").textContent = evaluationDetail.subject.name;
    document.title = `${evaluationDetail.subject.name} - 採点`;

    const isSubmitted = evaluationDetail.status === "submitted";
    const notice = document.getElementById("statusNotice");
    if (isSubmitted) {
      notice.textContent = "この採点は確定済みです。以降の変更はできません。";
      notice.classList.remove("hidden");
    } else {
      notice.classList.add("hidden");
    }

    const container = document.getElementById("criteriaContainer");
    container.innerHTML = "";
    evaluationDetail.criteria.forEach((criterion) => {
      const field = document.createElement("div");
      field.className = "field";

      const label = document.createElement("label");
      label.textContent = `${criterion.name} (0〜${criterion.max_score})`;
      field.appendChild(label);

      const row = document.createElement("div");
      row.style.display = "flex";
      row.style.gap = "12px";
      row.style.alignItems = "center";

      const input = document.createElement("input");
      input.type = "range";
      input.min = "0";
      input.max = String(criterion.max_score);
      input.step = "1";
      input.value = criterion.score != null ? String(criterion.score) : "0";
      input.disabled = isSubmitted;
      input.dataset.criterionId = criterion.id;

      const output = document.createElement("output");
      output.textContent = criterion.score != null ? String(criterion.score) : "-";
      output.style.minWidth = "2.5em";

      input.addEventListener("input", () => {
        output.textContent = input.value;
        queueSave();
      });

      row.append(input, output);
      field.appendChild(row);
      container.appendChild(field);
    });

    const feedbackInput = document.getElementById("feedbackInput");
    feedbackInput.value = evaluationDetail.feedback || "";
    feedbackInput.disabled = isSubmitted;
    feedbackInput.oninput = () => queueSave();

    document.getElementById("submitButton").disabled = isSubmitted;
  }

  function collectPayload() {
    const scores = {};
    document.querySelectorAll("#criteriaContainer input[type=range]").forEach((input) => {
      scores[input.dataset.criterionId] = Number(input.value);
    });
    return { scores, feedback: document.getElementById("feedbackInput").value };
  }

  function queueSave() {
    const statusEl = document.getElementById("saveStatus");
    statusEl.textContent = "保存中...";
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      try {
        evaluationDetail = await apiFetch(`/api/evaluations/${evaluationId}/scores`, {
          method: "POST",
          body: JSON.stringify(collectPayload()),
        });
        statusEl.textContent = "保存済み";
      } catch (err) {
        statusEl.textContent = "保存失敗: " + err.message;
      }
    }, AUTOSAVE_DEBOUNCE_MS);
  }

  document.getElementById("submitButton").addEventListener("click", async () => {
    if (!confirm("この採点を確定しますか?確定後は変更できません。")) return;
    try {
      evaluationDetail = await apiFetch(`/api/evaluations/${evaluationId}/submit`, {
        method: "POST",
      });
      showMessage("確定しました");
      render();
    } catch (err) {
      showMessage(err.message, { isError: true });
    }
  });

  init();
})();
