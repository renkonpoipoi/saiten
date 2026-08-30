(function () {
  const subjectList = document.getElementById("subjectList");
  const scorerList = document.getElementById("scorerList");
  const criterionList = document.getElementById("criterionList");
  const form = document.getElementById("createForm");
  const formPanel = document.getElementById("formPanel");
  const createdPanel = document.getElementById("createdPanel");

  function addRepeatableRow(container, placeholder, { removable = true } = {}) {
    const row = document.createElement("div");
    row.className = "repeatable-row";
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = placeholder;
    row.appendChild(input);
    if (removable) {
      const removeButton = document.createElement("button");
      removeButton.type = "button";
      removeButton.textContent = "削除";
      removeButton.className = "ghost";
      removeButton.addEventListener("click", () => row.remove());
      row.appendChild(removeButton);
    }
    container.appendChild(row);
    return input;
  }

  // 初期表示: 被採点者2件・採点者2件・採点軸5件(固定、削除不可)
  addRepeatableRow(subjectList, "例: チームA");
  addRepeatableRow(subjectList, "例: チームB");
  addRepeatableRow(scorerList, "例: 採点者1");
  addRepeatableRow(scorerList, "例: 採点者2");

  const defaultCriteria = ["独創性", "実用性", "デザイン", "技術力", "拡張性"];
  defaultCriteria.forEach((label) => {
    addRepeatableRow(criterionList, label, { removable: false });
  });

  document.getElementById("addSubjectButton").addEventListener("click", () => {
    addRepeatableRow(subjectList, "被採点者名");
  });
  document.getElementById("addScorerButton").addEventListener("click", () => {
    addRepeatableRow(scorerList, "採点者名");
  });

  function collectValues(container) {
    return Array.from(container.querySelectorAll("input"))
      .map((input) => input.value.trim())
      .filter((v) => v);
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      name: document.getElementById("projectName").value.trim(),
      subjects: collectValues(subjectList),
      scorers: collectValues(scorerList),
      criteria: collectValues(criterionList),
      allow_host_scoring: document.getElementById("allowHostScoring").checked,
    };

    try {
      const result = await apiFetch("/api/projects", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      showCreated(result);
    } catch (err) {
      showMessage(err.message, { isError: true });
    }
  });

  function showCreated(result) {
    formPanel.classList.add("hidden");
    createdPanel.classList.remove("hidden");

    // 表示上のURLだけ変える。実ページ遷移・再読み込み時のGET再取得は発生しない。
    // reload後は(サーバーにコードが残っていないため)このコードを再表示できない。
    if (window.history && window.history.pushState) {
      window.history.pushState({}, "", `/projects/${result.project_id}/created`);
    }

    document.getElementById("hostCodeText").textContent = result.host_code;

    const tbody = document.getElementById("scorerCodeTableBody");
    tbody.innerHTML = "";
    result.scorers.forEach((scorer) => {
      const tr = document.createElement("tr");
      const nameTd = document.createElement("td");
      nameTd.textContent = scorer.display_name + (scorer.is_host_scorer ? "(ホスト)" : "");
      const codeTd = document.createElement("td");
      const code = document.createElement("code");
      code.textContent = scorer.code;
      codeTd.appendChild(code);
      const copyTd = document.createElement("td");
      const copyButton = document.createElement("button");
      copyButton.type = "button";
      copyButton.textContent = "コピー";
      copyButton.addEventListener("click", () => copyToClipboard(scorer.code).then(() => showMessage("コピーしました")));
      copyTd.appendChild(copyButton);
      tr.append(nameTd, codeTd, copyTd);
      tbody.appendChild(tr);
    });

    document.getElementById("goToHostLoginLink").href = "/host/login";

    document.querySelectorAll("[data-copy-target]").forEach((button) => {
      button.addEventListener("click", () => {
        const text = document.getElementById(button.dataset.copyTarget).textContent;
        copyToClipboard(text).then(() => showMessage("コピーしました"));
      });
    });
  }
})();
