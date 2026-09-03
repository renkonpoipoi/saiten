(function () {
  const subjectId = window.SUBJECT_ID;
  const AUTOSAVE_DEBOUNCE_MS = 360;
  // 確定後は複数の被採点者を続けて採点できるよう一覧へ戻す。確定した旨を
  // 読み取れる程度の間だけ待ってから遷移する。
  const SUBMIT_REDIRECT_DELAY_MS = 1200;

  let evaluationId = null;
  let evaluationDetail = null;
  let saveTimer = null;
  // 逐次発表方式で、この被採点者がいま採点可能かどうか
  let subjectStatus = "SCORING";

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
    subjectStatus = row.subject_status || "SCORING";
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
    // 順番が回ってきていない被採点者は読み取り専用にする。
    // これは表示上の配慮で、実際の拒否はサーバー側(409)が行う。
    const isLocked = !isSubmitted && subjectStatus !== "SCORING";
    const readOnly = isSubmitted || isLocked;

    const notice = document.getElementById("statusNotice");
    if (isSubmitted) {
      notice.textContent = "この採点は確定済みです。以降の変更はできません。";
      notice.classList.remove("hidden");
    } else if (isLocked) {
      notice.textContent =
        subjectStatus === "WAITING"
          ? "この被採点者はまだ採点の順番が来ていません。発表が始まるまでお待ちください。"
          : "この被採点者の採点は締め切られています。";
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
      input.disabled = readOnly;
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
    feedbackInput.disabled = readOnly;
    feedbackInput.oninput = () => queueSave();

    document.getElementById("submitButton").disabled = readOnly;
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
      showMessage("提出しました。一覧へ戻ります…");
      // 先にreadonly表示へ切り替えておく(遷移前に確定済みだと分かるように)
      render();
      setTimeout(() => {
        window.location.href = "/scorer";
      }, SUBMIT_REDIRECT_DELAY_MS);
    } catch (err) {
      showMessage(err.message, { isError: true });
    }
  });

  init();
})();
