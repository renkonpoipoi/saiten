(function () {
  const projectId = window.PROJECT_ID;
  const STATE_LABELS = { not_started: "未入力", draft: "入力中", submitted: "提出済み" };
  const STATE_CLASSES = { not_started: "pending", draft: "pending", submitted: "submitted" };

  async function load() {
    let data;
    try {
      data = await apiFetch(`/api/projects/${projectId}/progress`);
    } catch (err) {
      if (err.status === 403) {
        window.location.href = "/host/login";
        return;
      }
      showMessage(err.message, { isError: true });
      return;
    }
    render(data);
  }

  function render(data) {
    document.getElementById("projectStatusBadge").textContent = data.project_status;
    document.getElementById("settingsLink").href = `/host/${projectId}/settings`;

    document.getElementById("progressSummary").textContent =
      `${data.submitted_count} / ${data.total_count} 件 提出済み`;
    document.getElementById("eligibleCount").textContent = data.eligible_scorer_count;
    document.getElementById("incompleteCount").textContent = data.incomplete_scorer_count;

    renderMatrix(data);

    const closeSection = document.getElementById("closeSection");
    const presentLink = document.getElementById("presentLink");
    if (data.project_status === "SCORING") {
      closeSection.classList.remove("hidden");
      presentLink.classList.add("hidden");
    } else {
      closeSection.classList.add("hidden");
      if (data.project_status === "LOCKED" || data.project_status === "PRESENTING" || data.project_status === "FINISHED") {
        presentLink.href = `/host/${projectId}/present`;
        presentLink.classList.remove("hidden");
      }
    }

    window._lastProgress = data;
  }

  function renderMatrix(data) {
    const headerRow = document.getElementById("matrixHeaderRow");
    headerRow.innerHTML = "<th>採点者</th>";
    data.subjects.forEach((subject) => {
      const th = document.createElement("th");
      th.textContent = subject.name;
      headerRow.appendChild(th);
    });
    const eligibleTh = document.createElement("th");
    eligibleTh.textContent = "eligible";
    headerRow.appendChild(eligibleTh);

    const body = document.getElementById("matrixBody");
    body.innerHTML = "";
    data.scorers.forEach((scorer) => {
      const tr = document.createElement("tr");
      const nameTd = document.createElement("td");
      nameTd.textContent = scorer.display_name + (scorer.is_host_scorer ? "(ホスト)" : "");
      tr.appendChild(nameTd);

      scorer.statuses.forEach((state) => {
        const td = document.createElement("td");
        const badge = document.createElement("span");
        badge.className = `badge ${STATE_CLASSES[state]}`;
        badge.textContent = STATE_LABELS[state];
        td.appendChild(badge);
        tr.appendChild(td);
      });

      const eligibleTd = document.createElement("td");
      eligibleTd.textContent = scorer.eligible ? "○" : "-";
      tr.appendChild(eligibleTd);

      body.appendChild(tr);
    });
  }

  document.getElementById("closeButton").addEventListener("click", async () => {
    const data = window._lastProgress;
    const incompleteCount = data ? data.incomplete_scorer_count : 0;
    const eligibleCount = data ? data.eligible_scorer_count : 0;
    const message =
      incompleteCount > 0
        ? `未完了の採点者が${incompleteCount}名います。この${incompleteCount}名の採点結果は集計から除外されます(eligible scorer数: ${eligibleCount})。締切りますか?`
        : `全採点者が完了しています。締切りますか?(eligible scorer数: ${eligibleCount})`;
    if (!confirm(message)) return;

    try {
      await apiFetch(`/api/projects/${projectId}/transition`, {
        method: "POST",
        body: JSON.stringify({ target_status: "LOCKED" }),
      });
      showMessage("締切りました");
      await load();
    } catch (err) {
      showMessage(err.message, { isError: true });
    }
  });

  load();
})();
