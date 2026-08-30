(function () {
  document.getElementById("hostLoginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const code = document.getElementById("hostCodeInput").value.trim();
    try {
      const result = await apiFetch("/api/auth/host-login", {
        method: "POST",
        body: JSON.stringify({ code }),
      });
      window.location.href = `/host/${result.project_id}/settings`;
    } catch (err) {
      showMessage(err.message, { isError: true });
    }
  });
})();
