const form = document.querySelector("#loginForm");
const input = document.querySelector("#accessCodeInput");
const messageBox = document.querySelector("#messageBox");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  messageBox.classList.add("hidden");
  const accessCode = input.value.trim();
  if (!accessCode) return;

  try {
    const response = await fetch("/api/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accessCode }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "ログインできませんでした。");
    location.href = "/admin";
  } catch (error) {
    messageBox.textContent = error.message;
    messageBox.classList.remove("hidden");
  }
});
