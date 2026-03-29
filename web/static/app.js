const refreshButton = document.getElementById("refresh-button");
if (refreshButton) {
  refreshButton.addEventListener("click", () => {
    window.location.reload();
  });
}

const questionInput = document.querySelector('textarea[name="question"]');
const quickQuestions = document.querySelectorAll(".quick-question");
const askForm = document.getElementById("ask-form");
const askSubmitButton = document.getElementById("ask-submit-button");
const askLoading = document.getElementById("ask-loading");
quickQuestions.forEach((button) => {
  button.addEventListener("click", () => {
    if (questionInput) {
      questionInput.value = button.dataset.question || "";
      questionInput.focus();
    }
  });
});

if (askForm && askSubmitButton) {
  askForm.addEventListener("submit", () => {
    askSubmitButton.disabled = true;
    askSubmitButton.textContent = "Analyzing...";
    if (askLoading) {
      askLoading.hidden = false;
    }
  });
}

const refreshSeconds = Number(document.body.dataset.autoRefreshSeconds || "0");
if (refreshSeconds > 0) {
  window.setTimeout(() => {
    window.location.reload();
  }, refreshSeconds * 1000);
}
