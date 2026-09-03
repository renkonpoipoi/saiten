(function () {
  const projectId = window.PROJECT_ID;
  const STATE_LABELS = { not_started: "未入力", draft: "入力中", submitted: "提出済み" };
  const STATE_CLASSES = { not_started: "pending", draft: "pending", submitted: "submitted" };

  const POLL_INTERVAL_MS = 5000;
  // SCORING中のみ進捗が変化しうる。LOCKED以降はpollingを止める。
  const POLLING_STATUSES = new Set(["DRAFT", "SCORING"]);

  let pollTimer = null;
  let inFlight = false; // 同じrequestの重複実行を防ぐ
  let consecutiveErrors = 0;
  let pollingStopped = false;
  let lastProgress = null;

  function scheduleNextPoll() {
    clearTimeout(pollTimer);
    if (pollingStopped) return;
    pollTimer = setTimeout(poll, POLL_INTERVAL_MS);
  }

  function stopPolling() {
    pollingStopped = true;
    clearTimeout(pollTimer);
    pollTimer = null;
  }

  async function poll() {
    // タブが非表示の間は通信せず、次の機会に回す(可視化時にも即再開する)
    if (document.hidden || inFlight || pollingStopped) {
      scheduleNextPoll();
      return;
    }
    await fetchProgress({ silent: true });
    scheduleNextPoll();
  }

  async function fetchProgress({ silent = false } = {}) {
    if (inFlight) return null;
    inFlight = true;
    try {
      const data = await apiFetch(`/api/projects/${projectId}/progress`);
      consecutiveErrors = 0;
      setConnectionWarning(false);
      render(data);
      return data;
    } catch (err) {
      if (err.status === 403) {
        // セッション切れ・権限喪失は回復不能なのでpollingを止めてログインへ
        stopPolling();
        window.location.href = "/host/login";
        return null;
      }
      consecutiveErrors += 1;
      // 一時的な通信エラーでHostの主要操作(締切ボタン等)は壊さない。
      // 初回だけ控えめに知らせ、以降はエラーを積み上げない。
      if (!silent) {
        showMessage(err.message, { isError: true });
      } else if (consecutiveErrors === 1) {
        setConnectionWarning(true);
      }
      return null;
    } finally {
      inFlight = false;
    }
  }

  function setConnectionWarning(visible) {
    const el = document.getElementById("connectionWarning");
    if (!el) return;
    el.classList.toggle("hidden", !visible);
  }

  function render(data) {
    lastProgress = data;

    document.getElementById("projectStatusBadge").textContent = data.project_status;
    document.getElementById("settingsLink").href = `/host/${projectId}/settings`;

    // DRAFT中は採点開始・構成編集の導線としてSettingsを明示的に案内する
    const draftNotice = document.getElementById("draftNotice");
    if (draftNotice) {
      const isDraft = data.project_status === "DRAFT";
      draftNotice.classList.toggle("hidden", !isDraft);
      if (isDraft) {
        document.getElementById("draftSettingsLink").href = `/host/${projectId}/settings`;
      }
    }

    document.getElementById("progressSummary").textContent =
      `${data.submitted_count} / ${data.total_count} 件 提出済み`;
    document.getElementById("eligibleCount").textContent = data.eligible_scorer_count;
    document.getElementById("incompleteCount").textContent = data.incomplete_scorer_count;

    renderMatrix(data);

    const closeSection = document.getElementById("closeSection");
    const sequentialSection = document.getElementById("sequentialSection");
    const presentLink = document.getElementById("presentLink");
    const analysisLink = document.getElementById("analysisLink");
    const isSequential = data.presentation_mode === "SEQUENTIAL";

    // SEQUENTIALではBATCH用の締切UI(強制締切を含む)を一切出さない。
    // 同じボタンを流用すると、未提出者を除外して締め切れると誤解されるため。
    if (data.project_status === "SCORING" && isSequential) {
      sequentialSection.classList.remove("hidden");
      renderSequential(data);
    } else {
      sequentialSection.classList.add("hidden");
    }

    if (data.project_status === "SCORING" && !isSequential) {
      closeSection.classList.remove("hidden");
      presentLink.classList.add("hidden");
      analysisLink.classList.add("hidden");
    } else if (data.project_status === "SCORING") {
      closeSection.classList.add("hidden");
      presentLink.classList.add("hidden");
      analysisLink.classList.add("hidden");
    } else {
      closeSection.classList.add("hidden");
      if (["LOCKED", "PRESENTING", "FINISHED"].includes(data.project_status)) {
        presentLink.href = `/host/${projectId}/present`;
        presentLink.classList.remove("hidden");
        // 分析はresult-summaryと同じくLOCKED以降でのみ開ける
        analysisLink.href = `/host/${projectId}/analysis`;
        analysisLink.classList.remove("hidden");
      }
    }

    // 締切済み以降は進捗が変化しないのでpollingを停止する
    if (!POLLING_STATUSES.has(data.project_status)) {
      stopPolling();
      setPollingIndicator(false);
    } else {
      setPollingIndicator(true);
    }
  }

  const SUBJECT_STATUS_LABELS = {
    WAITING: "待機中",
    SCORING: "採点中",
    LOCKED: "締切済み(発表待ち)",
    PRESENTED: "発表済み",
  };

  function renderSequential(data) {
    document.getElementById("sequentialPresentLink").href = `/host/${projectId}/present`;

    const container = document.getElementById("sequentialProgressRows");
    container.innerHTML = "";
    data.subjects.forEach((subject) => {
      const row = document.createElement("div");
      row.className = "progress-row";

      const name = document.createElement("span");
      name.textContent = subject.name;

      const detail = document.createElement("span");
      if (subject.presentation_status === "WAITING") {
        detail.textContent = SUBJECT_STATUS_LABELS.WAITING;
      } else {
        detail.textContent =
          `${SUBJECT_STATUS_LABELS[subject.presentation_status]} ・ ` +
          `提出 ${subject.submitted_count}/${subject.scorer_count}` +
          (subject.pending_count > 0 ? `(未提出 ${subject.pending_count}名)` : "");
      }

      row.append(name, detail);
      container.appendChild(row);
    });

    const current = data.subjects.find((s) => s.id === data.current_subject_id);
    const presentable = data.subjects.find((s) => s.id === data.presentable_subject_id);
    const status = document.getElementById("sequentialCurrentStatus");
    const lockButton = document.getElementById("lockSubjectButton");

    if (presentable) {
      status.textContent = `「${presentable.name}」は締切済みです。結果発表画面から得点を発表してください。`;
      lockButton.disabled = true;
      lockButton.dataset.subjectId = "";
    } else if (current) {
      status.textContent = current.can_lock
        ? `「${current.name}」は採点者全員が提出済みです。締め切れます。`
        : `「${current.name}」を採点中です(未提出 ${current.pending_count}名)。全員の提出を待っています。`;
      lockButton.disabled = !current.can_lock;
      lockButton.dataset.subjectId = String(current.id);
    } else if (data.all_subjects_presented) {
      status.textContent = "全ての被採点者の発表が終わりました。結果発表画面から最終ランキングへ進めます。";
      lockButton.disabled = true;
      lockButton.dataset.subjectId = "";
    }
  }

  document.getElementById("lockSubjectButton").addEventListener("click", async () => {
    const button = document.getElementById("lockSubjectButton");
    const subjectId = button.dataset.subjectId;
    if (!subjectId) return;

    const fresh = await fetchProgress({ silent: true });
    const data = fresh || lastProgress;
    const subject = data
      ? data.subjects.find((s) => String(s.id) === subjectId)
      : null;
    if (!subject || !subject.can_lock) {
      showMessage("まだ全員の提出が揃っていません。", { isError: true });
      return;
    }
    if (!confirm(`「${subject.name}」の採点を締め切りますか?締切後は変更できません。`)) return;

    try {
      await apiFetch(`/api/projects/${projectId}/subjects/${subjectId}/lock`, {
        method: "POST",
      });
      showMessage("締め切りました。結果発表画面から得点を発表してください。");
      await fetchProgress();
    } catch (err) {
      showMessage(err.message, { isError: true });
    }
  });

  function setPollingIndicator(active) {
    const el = document.getElementById("pollingIndicator");
    if (!el) return;
    el.textContent = active ? "自動更新中(5秒ごと)" : "自動更新は停止しています";
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

  // タブが再表示されたら待たずに最新化する
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !pollingStopped) {
      fetchProgress({ silent: true }).then(scheduleNextPoll);
    }
  });

  document.getElementById("closeButton").addEventListener("click", async () => {
    // 締切判断は最新の進捗で行いたいので、まず取得を試みる。
    // 取得に失敗しても直前の値で操作は継続できるようにする。
    const fresh = await fetchProgress({ silent: true });
    const data = fresh || lastProgress;
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
      await fetchProgress();
    } catch (err) {
      showMessage(err.message, { isError: true });
    }
  });

  fetchProgress().then(scheduleNextPoll);
})();
