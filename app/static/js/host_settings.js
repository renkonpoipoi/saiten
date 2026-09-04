(function () {
  const projectId = window.PROJECT_ID;
  let project = null;

  async function load() {
    try {
      project = await apiFetch(`/api/projects/${projectId}`);
    } catch (err) {
      if (err.status === 403) {
        window.location.href = "/host/login";
        return;
      }
      showMessage(err.message, { isError: true });
      return;
    }
    render();
  }

  function render() {
    const isDraft = project.status === "DRAFT";

    document.getElementById("projectNameHeading").textContent = project.name;
    document.title = `${project.name} - Host Settings`;
    const badge = document.getElementById("projectStatusBadge");
    badge.textContent = project.status;

    // Host Dashboardへ戻る導線は状態に関係なく常に出す。単なるGET遷移で、
    // 保存も状態遷移も伴わない(「採点を開始する」とは独立している)。
    const dashboardLink = document.getElementById("hostDashboardLink");
    dashboardLink.href = `/host/${projectId}`;
    dashboardLink.classList.remove("hidden");

    document.getElementById("draftOnlyNotice").classList.toggle("hidden", isDraft);
    document.getElementById("projectNameInput").value = project.name;
    document.getElementById("projectNameSaveButton").disabled = !isDraft;
    document.getElementById("addSubjectButton").disabled = !isDraft;
    document.getElementById("newSubjectName").disabled = !isDraft;
    document.getElementById("addScorerButton").disabled = !isDraft;
    document.getElementById("newScorerName").disabled = !isDraft;
    document
      .getElementById("startScoringButton")
      .toggleAttribute("hidden", !isDraft);

    renderSubjects(isDraft);
    renderCriteria(isDraft);
    renderScorers(isDraft);
    renderHostScorer(isDraft);
  }

  function renderSubjects(isDraft) {
    const container = document.getElementById("subjectRows");
    container.innerHTML = "";
    project.subjects.forEach((subject) => {
      const row = document.createElement("div");
      row.className = "progress-row";
      const input = document.createElement("input");
      input.type = "text";
      input.value = subject.name;
      input.disabled = !isDraft;
      row.appendChild(input);

      if (isDraft) {
        const saveButton = document.createElement("button");
        saveButton.textContent = "保存";
        saveButton.addEventListener("click", async () => {
          try {
            await apiFetch(`/api/projects/${projectId}/subjects/${subject.id}`, {
              method: "PATCH",
              body: JSON.stringify({ name: input.value.trim() }),
            });
            showMessage("保存しました");
            await load();
          } catch (err) {
            showMessage(err.message, { isError: true });
          }
        });
        const deleteButton = document.createElement("button");
        deleteButton.textContent = "削除";
        deleteButton.className = "danger";
        deleteButton.addEventListener("click", async () => {
          if (!confirm(`被採点者「${subject.name}」を削除しますか?この操作は取り消せません。`)) return;
          try {
            await apiFetch(`/api/projects/${projectId}/subjects/${subject.id}`, { method: "DELETE" });
            showMessage(`「${subject.name}」を削除しました`);
            await load();
          } catch (err) {
            showMessage(err.message, { isError: true });
          }
        });
        row.append(saveButton, deleteButton);
      }
      container.appendChild(row);
    });
  }

  function renderCriteria(isDraft) {
    const container = document.getElementById("criterionRows");
    container.innerHTML = "";
    project.criteria.forEach((criterion) => {
      const row = document.createElement("div");
      row.className = "progress-row";
      const input = document.createElement("input");
      input.type = "text";
      input.value = criterion.name;
      input.disabled = !isDraft;
      row.appendChild(input);

      const maxLabel = document.createElement("span");
      maxLabel.textContent = `満点 ${criterion.max_score}`;
      maxLabel.style.color = "var(--muted)";
      row.appendChild(maxLabel);

      if (isDraft) {
        const saveButton = document.createElement("button");
        saveButton.textContent = "保存";
        saveButton.addEventListener("click", async () => {
          try {
            await apiFetch(`/api/projects/${projectId}/criteria/${criterion.id}`, {
              method: "PATCH",
              body: JSON.stringify({ name: input.value.trim() }),
            });
            showMessage("保存しました");
            await load();
          } catch (err) {
            showMessage(err.message, { isError: true });
          }
        });
        row.appendChild(saveButton);
      }
      container.appendChild(row);
    });
  }

  // ホスト兼任の採点者。付け替えはフラグの移動だけで、採点者の追加・削除は
  // 一切行わない(旧方式で作られた「ホスト」という名前のScorerも自動削除しない)。
  //
  // **DRAFTである限り、ホスト兼任が今いるかどうかに関係なく常に操作可能にする。**
  // allow_host_scoring が false だからdisabledにする、という設計にはしない。
  // これをやると、ホスト兼任のScorerを削除した直後に誰も再割当できなくなる。
  function renderHostScorer(isDraft) {
    const section = document.getElementById("hostScorerSection");
    section.classList.toggle("hidden", !isDraft);

    const select = document.getElementById("hostScorerSelect");
    const saveButton = document.getElementById("saveHostScorerButton");
    // DRAFT以外では編集させない(構成変更はDRAFT限定という既存の制約)。
    select.disabled = !isDraft;
    saveButton.disabled = !isDraft;
    if (!isDraft) return;

    select.innerHTML = "";

    const none = document.createElement("option");
    none.value = "";
    none.textContent = "(なし)";
    select.appendChild(none);

    // 選択肢は現在activeな採点者だけ。削除済みの採点者は当然出てこない。
    const candidates = project.scorers.filter((scorer) => scorer.is_active);
    candidates.forEach((scorer) => {
      const option = document.createElement("option");
      option.value = String(scorer.id);
      option.textContent = scorer.display_name;
      if (scorer.is_host_scorer) option.selected = true;
      select.appendChild(option);
    });

    // 採点者が1人もいないときだけは選びようがないので、その旨を出す。
    const empty = candidates.length === 0;
    select.disabled = empty;
    saveButton.disabled = empty;
    document.getElementById("hostScorerEmptyNote").classList.toggle("hidden", !empty);
  }

  document.getElementById("saveHostScorerButton").addEventListener("click", async () => {
    const value = document.getElementById("hostScorerSelect").value;
    try {
      await apiFetch(`/api/projects/${projectId}/host-scorer`, {
        method: "PATCH",
        body: JSON.stringify({ scorer_id: value === "" ? null : Number(value) }),
      });
      showMessage(value === "" ? "ホスト兼任を解除しました" : "ホスト兼任の採点者を保存しました");
      await load();
    } catch (err) {
      showMessage(err.message, { isError: true });
    }
  });

  function renderScorers(isDraft) {
    const container = document.getElementById("scorerRows");
    container.innerHTML = "";
    project.scorers.forEach((scorer) => {
      const row = document.createElement("div");
      row.className = "progress-row";
      const input = document.createElement("input");
      input.type = "text";
      input.value = scorer.display_name;
      input.disabled = !isDraft;
      row.appendChild(input);

      const statusBadge = document.createElement("span");
      statusBadge.className = "badge submitted";
      statusBadge.textContent = scorer.is_host_scorer ? "ホスト兼任・発行済み" : "発行済み";
      row.appendChild(statusBadge);

      if (isDraft) {
        const saveButton = document.createElement("button");
        saveButton.textContent = "保存";
        saveButton.addEventListener("click", async () => {
          try {
            await apiFetch(`/api/projects/${projectId}/scorers/${scorer.id}`, {
              method: "PATCH",
              body: JSON.stringify({ display_name: input.value.trim() }),
            });
            showMessage("保存しました");
            await load();
          } catch (err) {
            showMessage(err.message, { isError: true });
          }
        });
        const deleteButton = document.createElement("button");
        deleteButton.textContent = "削除";
        deleteButton.className = "danger";
        deleteButton.addEventListener("click", async () => {
          if (!confirm(`採点者「${scorer.display_name}」を削除しますか?発行済みの参加コードも使えなくなります。`)) return;
          try {
            await apiFetch(`/api/projects/${projectId}/scorers/${scorer.id}`, { method: "DELETE" });
            showMessage(`「${scorer.display_name}」を削除しました`);
            await load();
          } catch (err) {
            showMessage(err.message, { isError: true });
          }
        });
        row.append(saveButton, deleteButton);
      }

      const regenButton = document.createElement("button");
      regenButton.textContent = "コード再発行";
      regenButton.addEventListener("click", async () => {
        if (!confirm(`${scorer.display_name}の参加コードを再発行しますか?旧コードは無効になります。`)) return;
        try {
          const result = await apiFetch(
            `/api/projects/${projectId}/scorers/${scorer.id}/regenerate-code`,
            { method: "POST" }
          );
          showNewScorerCode(scorer.display_name, result.code);
        } catch (err) {
          showMessage(err.message, { isError: true });
        }
      });
      row.appendChild(regenButton);

      container.appendChild(row);
    });
  }

  function showNewScorerCode(displayName, code) {
    const box = document.createElement("div");
    box.className = "code-box";
    box.style.marginTop = "8px";
    const codeEl = document.createElement("code");
    codeEl.textContent = `${displayName}: ${code}`;
    const copyButton = document.createElement("button");
    copyButton.textContent = "コピー";
    copyButton.addEventListener("click", () => copyToClipboard(code).then(() => showMessage("コピーしました")));
    box.append(codeEl, copyButton);
    document.getElementById("scorerRows").prepend(box);
  }

  document.getElementById("projectNameForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await apiFetch(`/api/projects/${projectId}`, {
        method: "PATCH",
        body: JSON.stringify({ name: document.getElementById("projectNameInput").value.trim() }),
      });
      showMessage("保存しました");
      await load();
    } catch (err) {
      showMessage(err.message, { isError: true });
    }
  });

  document.getElementById("addSubjectForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = document.getElementById("newSubjectName").value.trim();
    if (!name) return;
    try {
      await apiFetch(`/api/projects/${projectId}/subjects`, {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      document.getElementById("newSubjectName").value = "";
      await load();
    } catch (err) {
      showMessage(err.message, { isError: true });
    }
  });

  document.getElementById("addScorerForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = document.getElementById("newScorerName").value.trim();
    if (!name) return;
    try {
      const result = await apiFetch(`/api/projects/${projectId}/scorers`, {
        method: "POST",
        body: JSON.stringify({ display_name: name }),
      });
      document.getElementById("newScorerName").value = "";
      showNewScorerCode(name, result.code);
      await load();
    } catch (err) {
      showMessage(err.message, { isError: true });
    }
  });

  document.getElementById("regenerateHostCodeButton").addEventListener("click", async () => {
    if (!confirm("ホストコードを再発行しますか?旧コードは無効になります。")) return;
    try {
      const result = await apiFetch(`/api/projects/${projectId}/regenerate-host-code`, {
        method: "POST",
      });
      document.getElementById("newHostCodeText").textContent = result.host_code;
      document.getElementById("newHostCodeBox").classList.remove("hidden");
    } catch (err) {
      showMessage(err.message, { isError: true });
    }
  });

  document.getElementById("copyNewHostCodeButton").addEventListener("click", () => {
    copyToClipboard(document.getElementById("newHostCodeText").textContent).then(() =>
      showMessage("コピーしました")
    );
  });

  document.getElementById("startScoringButton").addEventListener("click", async () => {
    if (!confirm("採点を開始しますか?開始後はプロジェクト構成を変更できません。")) return;
    try {
      await apiFetch(`/api/projects/${projectId}/transition`, {
        method: "POST",
        body: JSON.stringify({ target_status: "SCORING" }),
      });
      showMessage("採点を開始しました");
      await load();
    } catch (err) {
      showMessage(err.message, { isError: true });
    }
  });

  load();
})();
