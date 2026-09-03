(function () {
  const LABELS = { not_started: "未入力", in_progress: "入力中", submitted: "提出済み" };
  const CLASSES = { not_started: "pending", in_progress: "pending", submitted: "submitted" };
  // 逐次発表方式で、まだ順番が回ってきていない/既に締め切られた被採点者のラベル
  const SUBJECT_LABELS = {
    WAITING: "待機中",
    LOCKED: "締切済み",
    PRESENTED: "発表済み",
  };

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
      // 提出前で、かつ順番が回ってきていない場合は被採点者側の状態を優先して見せる
      const waitingLabel =
        row.state !== "submitted" ? SUBJECT_LABELS[row.subject_status] : null;
      badge.className = `badge ${waitingLabel ? "pending" : CLASSES[row.state]}`;
      badge.textContent = waitingLabel || LABELS[row.state];
      el.appendChild(badge);

      container.appendChild(el);
    });
  }

  load();
})();
