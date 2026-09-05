(function () {
  const subjectList = document.getElementById("subjectList");
  const scorerList = document.getElementById("scorerList");
  const criterionList = document.getElementById("criterionList");
  const form = document.getElementById("createForm");
  const formPanel = document.getElementById("formPanel");
  const createdPanel = document.getElementById("createdPanel");

  function addRepeatableRow(container, placeholder, { removable = true, onChange } = {}) {
    const row = document.createElement("div");
    row.className = "repeatable-row";
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = placeholder;
    if (onChange) input.addEventListener("input", onChange);
    row.appendChild(input);
    if (removable) {
      const removeButton = document.createElement("button");
      removeButton.type = "button";
      removeButton.textContent = "削除";
      removeButton.className = "ghost";
      removeButton.addEventListener("click", () => {
        row.remove();
        if (onChange) onChange();
      });
      row.appendChild(removeButton);
    }
    container.appendChild(row);
    return input;
  }

  // 初期表示: 被採点者2件・採点者2件・採点軸5件(固定、削除不可)
  addRepeatableRow(subjectList, "例: チームA");
  addRepeatableRow(subjectList, "例: チームB");
  addRepeatableRow(scorerList, "例: 採点者1", { onChange: () => refreshHostScorerOptions() });
  addRepeatableRow(scorerList, "例: 採点者2", { onChange: () => refreshHostScorerOptions() });

  const defaultCriteria = ["独創性", "実用性", "デザイン", "技術力", "拡張性"];
  defaultCriteria.forEach((label) => {
    addRepeatableRow(criterionList, label, { removable: false });
  });

  document.getElementById("addSubjectButton").addEventListener("click", () => {
    addRepeatableRow(subjectList, "被採点者名");
  });
  document.getElementById("addScorerButton").addEventListener("click", () => {
    addRepeatableRow(scorerList, "採点者名", { onChange: () => refreshHostScorerOptions() });
    refreshHostScorerOptions();
  });

  function collectValues(container) {
    return Array.from(container.querySelectorAll("input"))
      .map((input) => input.value.trim())
      .filter((v) => v);
  }

  // ---------------------------------------------------------------------------
  // ホスト兼任の採点者
  //
  // Host roleはScorerの属性であって別人格ではない。「ホスト自身も採点する」を
  // ONにしても採点者は増えず、入力済みの採点者から1人を選ぶだけ。
  // 選択値はindexで持つ。採点者名は重複しうるため名前では一意に指せず、
  // サーバー側の検証も同じ「空文字除去後のscorers配列」を基準にしている。
  // ---------------------------------------------------------------------------

  const allowHostScoring = document.getElementById("allowHostScoring");
  const hostScorerField = document.getElementById("hostScorerField");
  const hostScorerSelect = document.getElementById("hostScorerSelect");

  function refreshHostScorerOptions() {
    const enabled = allowHostScoring.checked;
    hostScorerField.classList.toggle("hidden", !enabled);
    if (!enabled) return;

    const names = collectValues(scorerList);
    const previous = hostScorerSelect.value;
    hostScorerSelect.innerHTML = "";
    names.forEach((name, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = name;
      hostScorerSelect.appendChild(option);
    });
    // 採点者を編集しても選択が飛ばないよう、可能なら元のindexを維持する
    if (previous !== "" && Number(previous) < names.length) {
      hostScorerSelect.value = previous;
    }
  }

  function hostScorerIndex() {
    if (!allowHostScoring.checked) return null;
    const value = hostScorerSelect.value;
    if (value === "") return null;
    return Number(value);
  }

  allowHostScoring.addEventListener("change", refreshHostScorerOptions);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      name: document.getElementById("projectName").value.trim(),
      subjects: collectValues(subjectList),
      scorers: collectValues(scorerList),
      criteria: collectValues(criterionList),
      allow_host_scoring: allowHostScoring.checked,
      host_scorer_index: hostScorerIndex(),
      presentation_mode: document.querySelector(
        'input[name="presentationMode"]:checked'
      ).value,
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

  const BULK_COPY_FAILED_MESSAGE =
    "コピーできませんでした。表示されているコードを手動で控えてください。";

  function scorerLabel(scorer) {
    return scorer.display_name + (scorer.is_host_scorer ? "(ホスト兼任)" : "");
  }

  function findHostScorer(scorers) {
    return scorers.find((scorer) => scorer.is_host_scorer) || null;
  }

  /** 参加者へ配布する採点者コードだけを1行ずつ並べたテキスト。
   *
   * 除外するのは2つ。
   *  - ホストコード(参加者向けSlack/LINE等へ誤って配ってしまうのを防ぐ)
   *  - ホスト兼任の採点者のコード(本人が使うものであり、配布対象ではない)
   *
   * ホスト兼任者もDB上は通常のScorerでCodeを持つが、Host本人はHost
   * Dashboardから自分の採点画面へ入れるため、通常運用ではCode入力が要らない。
   */
  function buildScorerCodeText(scorers) {
    return scorers
      .filter((scorer) => !scorer.is_host_scorer)
      .map((scorer) => `${scorer.display_name}: ${scorer.code}`)
      .join("\n");
  }

  /** 何が除外されるかを明示する説明文。ホスト兼任がいない場合は触れない。 */
  function buildBulkCopyNote(scorers) {
    const host = findHostScorer(scorers);
    if (!host) {
      return "参加者へ配布する採点者コードだけを1行ずつまとめてコピーします。"
        + "ホストコードは含まれません。";
    }
    return "参加者へ配布する採点者コードだけを1行ずつまとめてコピーします。"
      + `ホストコードと、ホスト兼任の採点者(${host.display_name})のコードは含まれません。`;
  }

  function showCreated(result) {
    formPanel.classList.add("hidden");
    createdPanel.classList.remove("hidden");

    // 表示上のURLだけ変える。実ページ遷移・再読み込み時のGET再取得は発生しない。
    // reload後は(サーバーにコードが残っていないため)このコードを再表示できないが、
    // このURL自体は実ルートなので404にはならず、Host Dashboardへ案内される。
    if (window.history && window.history.pushState) {
      window.history.pushState({}, "", `/projects/${result.project_id}/created`);
    }

    document.getElementById("hostCodeText").textContent = result.host_code;

    const tbody = document.getElementById("scorerCodeTableBody");
    tbody.innerHTML = "";
    result.scorers.forEach((scorer) => {
      const tr = document.createElement("tr");
      const nameTd = document.createElement("td");
      nameTd.textContent = scorerLabel(scorer);
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
      if (scorer.is_host_scorer) {
        // 個別に確認・コピーはできるが、参加者への配布対象ではない
        tr.dataset.hostScorer = "true";
        const note = document.createElement("span");
        note.className = "mode-note";
        note.textContent = "配布不要";
        copyTd.appendChild(note);
      }
      tr.append(nameTd, codeTd, copyTd);
      tbody.appendChild(tr);
    });

    document.getElementById("bulkCopyNote").textContent = buildBulkCopyNote(result.scorers);

    // 一括コピーの中身は作成レスポンスからその場で組み立てるだけ。
    // サーバーへコードを送り直すことも、再取得することもしない。
    const scorerCodeText = buildScorerCodeText(result.scorers);
    document
      .getElementById("copyAllScorerCodesButton")
      .addEventListener("click", () => {
        // clipboardが使えない環境ではhelperがfallbackする。それも失敗した場合に
        // 画面が壊れないよう、同期例外とrejectionの両方を握りつぶして通知に変える。
        try {
          copyToClipboard(scorerCodeText)
            .then(() => showMessage("参加者コードをコピーしました"))
            .catch(() => showMessage(BULK_COPY_FAILED_MESSAGE, { isError: true }));
        } catch (err) {
          showMessage(BULK_COPY_FAILED_MESSAGE, { isError: true });
        }
      });

    // 作成APIがHost sessionを張っているため、host codeの再入力なしで移動できる。
    document.getElementById("goToHostDashboardLink").href = `/host/${result.project_id}`;

    document.querySelectorAll("[data-copy-target]").forEach((button) => {
      button.addEventListener("click", () => {
        const text = document.getElementById(button.dataset.copyTarget).textContent;
        copyToClipboard(text).then(() => showMessage("コピーしました"));
      });
    });
  }
})();
