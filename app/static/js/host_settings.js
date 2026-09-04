(function () {
  const projectId = window.PROJECT_ID;
  let project = null;

  // ---------------------------------------------------------------------------
  // UI lock
  //
  // 変更系APIの実行中だけ操作を止める(二重送信防止)。解除は必ず finally で
  // 行い、成功・失敗のどちらでも永続的なdisabledが残らないようにする。
  // 再enableはその場の render() で完了し、polling や timer を待たない。
  // ---------------------------------------------------------------------------

  let busy = false;

  function isBusy() {
    return busy;
  }

  /** DRAFTで、かつ変更系APIの実行中でないときだけ編集できる。 */
  function canEdit() {
    return project !== null && project.status === "DRAFT" && !busy;
  }

  function setBusy(value) {
    busy = value;
    document.body.dataset.busy = value ? "true" : "false";
  }

  /** 変更系APIの共通ラッパ。終了時に必ずlockを解いて最新stateを描き直す。 */
  async function mutate(action) {
    if (busy) return false;
    setBusy(true);
    if (project) render();
    let ok = false;
    try {
      await action();
      ok = true;
    } catch (err) {
      showMessage(err.message, { isError: true });
    } finally {
      // 成功でも失敗でも必ずここを通す。lockを解いてから最新stateを取り直し、
      // render() の時点でDRAFT用controlが操作可能に戻る。
      setBusy(false);
      await load();
    }
    return ok;
  }

  async function load() {
    try {
      project = await apiFetch(`/api/projects/${projectId}`);
    } catch (err) {
      if (err.status === 403) {
        window.location.href = "/host/login";
        return;
      }
      showMessage(err.message, { isError: true });
      // 取得に失敗しても手元のstateで描き直し、UIがlockされたままにならないようにする。
      if (project) render();
      return;
    }
    render();
  }

  function render() {
    const isDraft = project.status === "DRAFT";
    // 構造(表示/非表示)は status だけで決め、操作可否は busy も含めて決める。
    const editable = canEdit();

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
    document.getElementById("projectNameSaveButton").disabled = !editable;
    document.getElementById("addSubjectButton").disabled = !editable;
    document.getElementById("newSubjectName").disabled = !editable;
    document.getElementById("addScorerButton").disabled = !editable;
    document.getElementById("newScorerName").disabled = !editable;
    document
      .getElementById("startScoringButton")
      .toggleAttribute("hidden", !isDraft);

    renderSubjects(isDraft, editable);
    renderCriteria(isDraft, editable);
    renderScorers(isDraft, editable);
    renderHostScorer(isDraft, editable);
  }

  /** 既存の名前を編集して確定するボタン。
   *
   * 新規追加は「追加」を押した時点で保存されるので、この操作は
   * **既存の名前の更新**にしか使わない。文言もそれに合わせる。
   * 入力を変更したときだけ押せるようにして、押しても何も起きない状態を減らす。
   */
  function buildNameUpdateButton(input, originalName, editable, action) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "名前を更新";
    button.title = "入力した名前でこの行を更新します";
    const refresh = () => {
      const changed = input.value.trim() !== originalName;
      button.disabled = !editable || !changed;
    };
    refresh();
    input.addEventListener("input", refresh);
    button.addEventListener("click", () => mutate(action));
    return button;
  }

  function renderSubjects(isDraft, editable) {
    const container = document.getElementById("subjectRows");
    container.innerHTML = "";
    project.subjects.forEach((subject) => {
      const row = document.createElement("div");
      row.className = "progress-row";
      const input = document.createElement("input");
      input.type = "text";
      input.value = subject.name;
      input.disabled = !editable;
      row.appendChild(input);

      if (isDraft) {
        const updateButton = buildNameUpdateButton(
          input, subject.name, editable,
          async () => {
            await apiFetch(`/api/projects/${projectId}/subjects/${subject.id}`, {
              method: "PATCH",
              body: JSON.stringify({ name: input.value.trim() }),
            });
            showMessage("名前を更新しました");
          }
        );
        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.textContent = "削除";
        deleteButton.className = "danger";
        deleteButton.disabled = !editable;
        deleteButton.addEventListener("click", () => {
          if (!confirm(`被採点者「${subject.name}」を削除しますか?この操作は取り消せません。`)) return;
          mutate(async () => {
            await apiFetch(`/api/projects/${projectId}/subjects/${subject.id}`, { method: "DELETE" });
            showMessage(`「${subject.name}」を削除しました`);
          });
        });
        row.append(updateButton, deleteButton);
      }
      container.appendChild(row);
    });
  }

  function renderCriteria(isDraft, editable) {
    const container = document.getElementById("criterionRows");
    container.innerHTML = "";
    project.criteria.forEach((criterion) => {
      const row = document.createElement("div");
      row.className = "progress-row";
      const input = document.createElement("input");
      input.type = "text";
      input.value = criterion.name;
      input.disabled = !editable;
      row.appendChild(input);

      const maxLabel = document.createElement("span");
      maxLabel.textContent = `満点 ${criterion.max_score}`;
      maxLabel.style.color = "var(--muted)";
      row.appendChild(maxLabel);

      if (isDraft) {
        row.appendChild(
          buildNameUpdateButton(
            input, criterion.name, editable,
            async () => {
              await apiFetch(`/api/projects/${projectId}/criteria/${criterion.id}`, {
                method: "PATCH",
                body: JSON.stringify({ name: input.value.trim() }),
              });
              showMessage("名前を更新しました");
            }
          )
        );
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
  function renderHostScorer(isDraft, editable) {
    const section = document.getElementById("hostScorerSection");
    section.classList.toggle("hidden", !isDraft);

    const select = document.getElementById("hostScorerSelect");
    const saveButton = document.getElementById("saveHostScorerButton");
    // DRAFT以外では編集させない(構成変更はDRAFT限定という既存の制約)。
    // 変更系APIの実行中も一時的に止めるが、解除は mutate() の finally が保証する。
    select.disabled = !editable;
    saveButton.disabled = !editable;
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
    select.disabled = !editable || empty;
    saveButton.disabled = !editable || empty;
    document.getElementById("hostScorerEmptyNote").classList.toggle("hidden", !empty);
  }

  // ここは「保存」のまま。selectを変えただけではDBに確定せず、押して初めて
  // Host role の割当が確定するという、意味のある操作だからである。
  document.getElementById("saveHostScorerButton").addEventListener("click", () => {
    const value = document.getElementById("hostScorerSelect").value;
    mutate(async () => {
      await apiFetch(`/api/projects/${projectId}/host-scorer`, {
        method: "PATCH",
        body: JSON.stringify({ scorer_id: value === "" ? null : Number(value) }),
      });
      showMessage(value === "" ? "ホスト兼任を解除しました" : "ホスト兼任の採点者を保存しました");
    });
  });

  function renderScorers(isDraft, editable) {
    const container = document.getElementById("scorerRows");
    container.innerHTML = "";
    project.scorers.forEach((scorer) => {
      const row = document.createElement("div");
      row.className = "progress-row";
      const input = document.createElement("input");
      input.type = "text";
      input.value = scorer.display_name;
      input.disabled = !editable;
      row.appendChild(input);

      const statusBadge = document.createElement("span");
      statusBadge.className = "badge submitted";
      statusBadge.textContent = scorer.is_host_scorer ? "ホスト兼任・発行済み" : "発行済み";
      row.appendChild(statusBadge);

      if (isDraft) {
        const updateButton = buildNameUpdateButton(
          input, scorer.display_name, editable,
          async () => {
            await apiFetch(`/api/projects/${projectId}/scorers/${scorer.id}`, {
              method: "PATCH",
              body: JSON.stringify({ display_name: input.value.trim() }),
            });
            showMessage("名前を更新しました");
          }
        );
        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.textContent = "削除";
        deleteButton.className = "danger";
        deleteButton.disabled = !editable;
        deleteButton.addEventListener("click", () => {
          if (!confirm(`採点者「${scorer.display_name}」を削除しますか?発行済みの参加コードも使えなくなります。`)) return;
          mutate(async () => {
            await apiFetch(`/api/projects/${projectId}/scorers/${scorer.id}`, { method: "DELETE" });
            showMessage(`「${scorer.display_name}」を削除しました`);
          });
        });
        row.append(updateButton, deleteButton);
      }

      // コード再発行はDRAFT以外でも使える(既存挙動を維持)。
      const regenButton = document.createElement("button");
      regenButton.type = "button";
      regenButton.textContent = "コード再発行";
      regenButton.disabled = isBusy();
      regenButton.addEventListener("click", () => {
        if (!confirm(`${scorer.display_name}の参加コードを再発行しますか?旧コードは無効になります。`)) return;
        mutate(async () => {
          const result = await apiFetch(
            `/api/projects/${projectId}/scorers/${scorer.id}/regenerate-code`,
            { method: "POST" }
          );
          pendingScorerCode = { displayName: scorer.display_name, code: result.code };
        });
      });
      row.appendChild(regenButton);

      container.appendChild(row);
    });

    // コード再発行の結果は render() のたびに消えてしまうので、
    // 直近の1件だけ保持して描画後に差し込む。
    if (pendingScorerCode) {
      showNewScorerCode(pendingScorerCode.displayName, pendingScorerCode.code);
    }
  }

  let pendingScorerCode = null;

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

  document.getElementById("projectNameForm").addEventListener("submit", (event) => {
    event.preventDefault();
    mutate(async () => {
      await apiFetch(`/api/projects/${projectId}`, {
        method: "PATCH",
        body: JSON.stringify({ name: document.getElementById("projectNameInput").value.trim() }),
      });
      showMessage("保存しました");
    });
  });

  document.getElementById("addSubjectForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const name = document.getElementById("newSubjectName").value.trim();
    if (!name) return;
    mutate(async () => {
      await apiFetch(`/api/projects/${projectId}/subjects`, {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      document.getElementById("newSubjectName").value = "";
      showMessage(`「${name}」を追加しました`);
    });
  });

  document.getElementById("addScorerForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const name = document.getElementById("newScorerName").value.trim();
    if (!name) return;
    mutate(async () => {
      const result = await apiFetch(`/api/projects/${projectId}/scorers`, {
        method: "POST",
        body: JSON.stringify({ display_name: name }),
      });
      document.getElementById("newScorerName").value = "";
      // 発行された参加コードは render() 後に差し込む(再描画で消えないように)
      pendingScorerCode = { displayName: name, code: result.code };
      showMessage(`「${name}」を追加しました`);
    });
  });

  document.getElementById("regenerateHostCodeButton").addEventListener("click", () => {
    if (!confirm("ホストコードを再発行しますか?旧コードは無効になります。")) return;
    mutate(async () => {
      const result = await apiFetch(`/api/projects/${projectId}/regenerate-host-code`, {
        method: "POST",
      });
      document.getElementById("newHostCodeText").textContent = result.host_code;
      document.getElementById("newHostCodeBox").classList.remove("hidden");
    });
  });

  document.getElementById("copyNewHostCodeButton").addEventListener("click", () => {
    copyToClipboard(document.getElementById("newHostCodeText").textContent).then(() =>
      showMessage("コピーしました")
    );
  });

  document.getElementById("startScoringButton").addEventListener("click", () => {
    if (!confirm("採点を開始しますか?開始後はプロジェクト構成を変更できません。")) return;
    mutate(async () => {
      await apiFetch(`/api/projects/${projectId}/transition`, {
        method: "POST",
        body: JSON.stringify({ target_status: "SCORING" }),
      });
      showMessage("採点を開始しました");
    });
  });

  load();
})();
