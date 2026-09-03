(function () {
  document.getElementById("hostLoginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const code = document.getElementById("hostCodeInput").value.trim();
    try {
      const result = await apiFetch("/api/auth/host-login", {
        method: "POST",
        body: JSON.stringify({ code }),
      });
      // 通常運用の起点はHost Dashboard。DRAFT中の構成編集や採点開始へは
      // Dashboard上の導線からSettingsへ移動する。
      window.location.href = `/host/${result.project_id}`;
    } catch (err) {
      showMessage(err.message, { isError: true });
    }
  });
})();
