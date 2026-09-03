(function () {
  const projectId = window.PROJECT_ID;

  async function load() {
    let data;
    try {
      data = await apiFetch(`/api/projects/${projectId}/analysis`);
    } catch (err) {
      if (err.status === 403) {
        window.location.href = "/host/login";
        return;
      }
      if (err.status === 409) {
        showMessage("結果分析は採点の締切後に利用できます。", { isError: true });
        return;
      }
      showMessage(err.message, { isError: true });
      return;
    }
    render(data);
  }

  function render(data) {
    document.getElementById("projectNameHeading").textContent = data.project.name;
    document.title = `${data.project.name} - 結果分析`;
    document.getElementById("projectStatusBadge").textContent = data.project.status;
    document.getElementById("backToDashboardLink").href = `/host/${projectId}`;
    document.getElementById("presentLink").href = `/host/${projectId}/present`;
    document.getElementById("exportCsvLink").href = `/api/projects/${projectId}/export.csv`;
    document.getElementById("exportMarkdownLink").href =
      `/api/projects/${projectId}/export.md`;

    document.getElementById("analysisSummary").textContent =
      `公式集計対象: ${data.official_scorer_count}名 / ` +
      `被採点者: ${data.subjects.length}名 / ` +
      `1人あたり満点: ${data.theoretical_max_total}点`;

    const excludedNotice = document.getElementById("excludedNotice");
    if (data.excluded_scorer_count > 0) {
      excludedNotice.textContent =
        `公式集計対象外の採点者が${data.excluded_scorer_count}名います。` +
        `提出済みのフィードバックは下に表示されますが、点数は集計に含まれていません。`;
      excludedNotice.classList.remove("hidden");
    } else {
      excludedNotice.classList.add("hidden");
    }

    renderRanking(data);
    renderSubjects(data);
  }

  function renderRanking(data) {
    const body = document.getElementById("rankingTableBody");
    body.innerHTML = "";
    data.subjects.forEach((subject) => {
      const tr = document.createElement("tr");
      [
        `${subject.rank}位`,
        subject.name,
        `${subject.total_score}点`,
        String(subject.mean_score),
      ].forEach((text) => {
        const td = document.createElement("td");
        td.textContent = text;
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
  }

  function criterionAverageTable(subject) {
    const table = document.createElement("table");
    const thead = document.createElement("thead");
    thead.innerHTML = "<tr><th>採点軸</th><th>平均</th><th>満点</th></tr>";
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    subject.criterion_averages.forEach((average) => {
      const tr = document.createElement("tr");
      [average.name, String(average.average), String(average.max_score)].forEach((text) => {
        const td = document.createElement("td");
        td.textContent = text;
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    return table;
  }

  function feedbackList(evaluations, official) {
    const wrapper = document.createElement("div");
    const rows = evaluations.filter((e) => e.official_included === official);
    if (!rows.length) return null;

    const heading = document.createElement("h4");
    heading.textContent = official ? "フィードバック" : "フィードバック(公式集計対象外)";
    wrapper.appendChild(heading);

    rows.forEach((evaluation) => {
      const row = document.createElement("div");
      row.className = "feedback-row";

      const name = document.createElement("div");
      name.className = "feedback-scorer";
      name.textContent = `${evaluation.scorer_name}(${evaluation.total}点)`;
      if (!evaluation.official_included) {
        const badge = document.createElement("span");
        badge.className = "badge pending";
        badge.textContent = "集計対象外";
        badge.style.marginLeft = "8px";
        name.appendChild(badge);
      }

      const text = document.createElement("div");
      text.className = "feedback-text";
      text.textContent = evaluation.feedback || "(記入なし)";

      row.append(name, text);
      wrapper.appendChild(row);
    });
    return wrapper;
  }

  function renderSubjects(data) {
    const container = document.getElementById("subjectSections");
    container.innerHTML = "";

    data.subjects.forEach((subject) => {
      const section = document.createElement("section");
      section.className = "panel";

      const heading = document.createElement("h2");
      heading.textContent = `${subject.rank}位 ${subject.name}`;
      section.appendChild(heading);

      const summary = document.createElement("p");
      summary.style.color = "var(--muted)";
      summary.textContent =
        `合計 ${subject.total_score}点 / 平均 ${subject.mean_score}点 / ` +
        `公式集計対象 ${subject.scorer_count}名`;
      section.appendChild(summary);

      section.appendChild(criterionAverageTable(subject));

      const official = feedbackList(subject.evaluations, true);
      if (official) section.appendChild(official);
      const excluded = feedbackList(subject.evaluations, false);
      if (excluded) section.appendChild(excluded);

      container.appendChild(section);
    });
  }

  load();
})();
