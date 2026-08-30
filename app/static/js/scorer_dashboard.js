(function () {
  const LABELS = { not_started: "未入力", in_progress: "入力中", submitted: "提出済み" };
  const CLASSES = { not_started: "pending", in_progress: "pending", submitted: "submitted" };

  async function load() {
    let data;
    try {
      data = await apiFetch("/api/scorer/me/evaluations");
    } catch (err) {
      if (err.status === 403) {
        window.location.href = "/join";
        return;
      }
      showMessage(err.message, { isError: true });
      return;
    }
    render(data);
  }

  function render(data) {
    document.getElementById("progressSummary").textContent =
      `${data.submitted_count} / ${data.total_count} 件 提出済み`;

    const container = document.getElementById("subjectRows");
    container.innerHTML = "";
    data.subjects.forEach((row) => {
      const el = document.createElement("a");
      el.href = `/scorer/subjects/${row.subject_id}`;
      el.className = "progress-row";
      el.style.textDecoration = "none";
      el.style.color = "inherit";

      const name = document.createElement("span");
      name.textContent = row.subject_name;
      el.appendChild(name);

      const badge = document.createElement("span");
      badge.className = `badge ${CLASSES[row.state]}`;
      badge.textContent = LABELS[row.state];
      el.appendChild(badge);

      container.appendChild(el);
    });
  }

  load();
})();
