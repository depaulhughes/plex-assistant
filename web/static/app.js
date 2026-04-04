const themeToggle = document.getElementById("theme-toggle");
const themeStorageKey = "plex-assistant-theme";

function getCurrentTheme() {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function updateThemeToggle() {
  if (!themeToggle) {
    return;
  }

  const theme = getCurrentTheme();
  themeToggle.textContent = theme === "dark" ? "Light mode" : "Dark mode";
  themeToggle.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
}

if (themeToggle) {
  updateThemeToggle();
  themeToggle.addEventListener("click", () => {
    const nextTheme = getCurrentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = nextTheme;
    try {
      window.localStorage.setItem(themeStorageKey, nextTheme);
    } catch (error) {
      console.warn("Could not persist theme", error);
    }
    updateThemeToggle();
  });
}

const refreshButton = document.getElementById("refresh-button");
if (refreshButton) {
  refreshButton.addEventListener("click", () => {
    window.location.reload();
  });
}

const questionInput = document.querySelector('textarea[name="question"]');
const quickQuestions = document.querySelectorAll(".quick-question");
const inlineAskButtons = document.querySelectorAll(".inline-ask");
const askForm = document.getElementById("ask-form");
const askSubmitButton = document.getElementById("ask-submit-button");
const askLoading = document.getElementById("ask-loading");
const askSourceInput = askForm ? askForm.querySelector('input[name="ask_source"]') : null;
const askSectionInput = askForm ? askForm.querySelector('input[name="ask_section"]') : null;
const askPromptKeyInput = askForm ? askForm.querySelector('input[name="ask_prompt_key"]') : null;

function updateAskContext({ source = "", section = "", promptKey = "" } = {}) {
  if (askSourceInput) {
    askSourceInput.value = source;
  }
  if (askSectionInput) {
    askSectionInput.value = section;
  }
  if (askPromptKeyInput) {
    askPromptKeyInput.value = promptKey;
  }
}

function showAskLoadingState() {
  if (askSubmitButton) {
    askSubmitButton.textContent = "Analyzing...";
  }
  if (askLoading) {
    askLoading.hidden = false;
  }
}

function submitAskQuestion(question, submitImmediately = false, context = {}) {
  if (!questionInput) {
    return;
  }

  updateAskContext(context);
  questionInput.value = question || "";
  questionInput.focus({ preventScroll: false });

  if (submitImmediately && askForm) {
    showAskLoadingState();
    askForm.submit();
  }
}

quickQuestions.forEach((button) => {
  button.addEventListener("click", () => {
    submitAskQuestion(button.dataset.question || "", button.dataset.submit === "true", {
      source: button.dataset.source || "quick_question",
      section: button.dataset.section || "copilot_panel",
      promptKey: button.dataset.promptKey || "",
    });
  });
});

inlineAskButtons.forEach((button) => {
  button.addEventListener("click", () => {
    submitAskQuestion(button.dataset.question || "", button.dataset.submit === "true", {
      source: button.dataset.source || "inline_card",
      section: button.dataset.section || "",
      promptKey: button.dataset.promptKey || "",
    });
  });
});

if (askForm && askSubmitButton) {
  askForm.addEventListener("submit", () => {
    showAskLoadingState();
  });
}

const refreshSeconds = Number(document.body.dataset.autoRefreshSeconds || "0");
if (refreshSeconds > 0) {
  window.setTimeout(() => {
    window.location.reload();
  }, refreshSeconds * 1000);
}
