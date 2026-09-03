(function () {
  document.getElementById("joinForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const code = document.getElementById("scorerCodeInput").value.trim();
    try {
      await apiFetch("/api/auth/scorer-login", {
        method: "POST",
        body: JSON.stringify({ code }),
      });
      window.location.href = "/scorer";
    } catch (err) {
      showMessage(err.message, { isError: true });
    }
  });
})();
