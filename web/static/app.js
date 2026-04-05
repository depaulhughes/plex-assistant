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
const askPanel = document.getElementById("ask-plex");
const askResult = document.getElementById("ask-result");
const askMobileLoadingPill = document.getElementById("ask-mobile-loading-pill");
const askClearForms = document.querySelectorAll(".ask-clear-form");
const askActiveStorageKey = "plex-assistant-ask-active-until";
const askScrollTargetStorageKey = "plex-assistant-ask-scroll-target";
const askScrollPendingStorageKey = "plex-assistant-ask-scroll-pending";
const askMobileLoadingStorageKey = "plex-assistant-ask-mobile-loading";
const clearScrollPendingStorageKey = "plex-assistant-clear-scroll-pending";
const clearScrollYStorageKey = "plex-assistant-clear-scroll-y";
const isMobileTouch = window.matchMedia("(hover: none) and (pointer: coarse)").matches || navigator.maxTouchPoints > 0;

function markAskActive(durationMs = 5 * 60 * 1000) {
  try {
    window.sessionStorage.setItem(askActiveStorageKey, String(Date.now() + durationMs));
  } catch (error) {
    console.warn("Could not persist Ask activity window", error);
  }
}

function isAskActiveWindow() {
  try {
    const raw = window.sessionStorage.getItem(askActiveStorageKey);
    return raw ? Number(raw) > Date.now() : false;
  } catch (error) {
    return false;
  }
}

function isAskInteractionActive() {
  const bodyHasAsk = document.body.dataset.askActive === "true";
  const focusInsideAsk = askPanel ? askPanel.contains(document.activeElement) : false;
  return bodyHasAsk || focusInsideAsk || isAskActiveWindow();
}

function setPendingAskScrollTarget(targetId) {
  if (!targetId) {
    return;
  }
  try {
    window.sessionStorage.setItem(askScrollTargetStorageKey, targetId);
    window.sessionStorage.setItem(askScrollPendingStorageKey, "true");
  } catch (error) {
    console.warn("Could not persist Ask scroll target", error);
  }
}

function clearPendingAskScrollTarget() {
  try {
    window.sessionStorage.removeItem(askScrollPendingStorageKey);
    window.sessionStorage.removeItem(askScrollTargetStorageKey);
  } catch (error) {
    console.warn("Could not clear Ask scroll target", error);
  }
}

function setPendingClearScrollPosition(scrollY) {
  try {
    window.sessionStorage.setItem(clearScrollPendingStorageKey, "true");
    window.sessionStorage.setItem(clearScrollYStorageKey, String(Math.max(0, Math.round(scrollY || 0))));
  } catch (error) {
    console.warn("Could not persist Clear scroll position", error);
  }
}

function consumePendingClearScrollPosition() {
  try {
    const pending = window.sessionStorage.getItem(clearScrollPendingStorageKey) === "true";
    const rawY = window.sessionStorage.getItem(clearScrollYStorageKey);
    if (!pending || rawY === null) {
      return null;
    }
    window.sessionStorage.removeItem(clearScrollPendingStorageKey);
    window.sessionStorage.removeItem(clearScrollYStorageKey);
    return Number(rawY);
  } catch (error) {
    return null;
  }
}

function setAskMobileLoadingPending(isPending) {
  try {
    if (isPending) {
      window.sessionStorage.setItem(askMobileLoadingStorageKey, "true");
    } else {
      window.sessionStorage.removeItem(askMobileLoadingStorageKey);
    }
  } catch (error) {
    console.warn("Could not persist mobile Ask loading state", error);
  }
}

function isAskMobileLoadingPending() {
  try {
    return window.sessionStorage.getItem(askMobileLoadingStorageKey) === "true";
  } catch (error) {
    return false;
  }
}

function showAskMobileLoadingPill() {
  if (!isMobileTouch || !askMobileLoadingPill) {
    return;
  }
  askMobileLoadingPill.hidden = false;
}

function clearAskRestorePendingState() {
  delete document.documentElement.dataset.askRestorePending;
}

function clearPendingClearRestoreState() {
  delete document.documentElement.dataset.clearRestorePending;
}

function hideAskMobileLoadingPill() {
  if (!askMobileLoadingPill) {
    return;
  }
  askMobileLoadingPill.hidden = true;
  setAskMobileLoadingPending(false);
  clearAskRestorePendingState();
}

function submitAskFormWithPaint(form) {
  if (!form) {
    return;
  }
  if (isMobileTouch) {
    window.setTimeout(() => {
      form.submit();
    }, 48);
    return;
  }
  form.submit();
}

function consumePendingAskScrollTarget() {
  try {
    const pending = window.sessionStorage.getItem(askScrollPendingStorageKey) === "true";
    const targetId = window.sessionStorage.getItem(askScrollTargetStorageKey) || "";
    if (!pending || !targetId) {
      return "";
    }
    window.sessionStorage.removeItem(askScrollPendingStorageKey);
    window.sessionStorage.removeItem(askScrollTargetStorageKey);
    return targetId;
  } catch (error) {
    return "";
  }
}

function scrollToAskTarget(targetId, { behavior = "smooth" } = {}) {
  const target = document.getElementById(targetId) || askPanel;
  if (!target) {
    return;
  }

  const tryScroll = () => {
    if (target.offsetHeight <= 0) {
      return false;
    }
    target.scrollIntoView({ behavior, block: "start", inline: "nearest" });
    return true;
  };

  if (tryScroll()) {
    return;
  }

  window.requestAnimationFrame(() => {
    window.requestAnimationFrame(() => {
      if (tryScroll()) {
        return;
      }
      window.setTimeout(() => {
        tryScroll();
      }, 180);
    });
  });
}

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
    askSubmitButton.disabled = true;
    askSubmitButton.classList.add("is-loading");
    askSubmitButton.setAttribute("aria-busy", "true");
  }
  if (askLoading) {
    askLoading.hidden = false;
  }
  if (isMobileTouch) {
    setAskMobileLoadingPending(true);
    showAskMobileLoadingPill();
  }
}

function submitAskQuestion(question, submitImmediately = false, context = {}) {
  if (!questionInput) {
    return;
  }

  updateAskContext(context);
  questionInput.value = question || "";

  if (!(submitImmediately && isMobileTouch)) {
    questionInput.focus({ preventScroll: false });
  }

  if (submitImmediately && askForm) {
    if (isMobileTouch && document.activeElement && typeof document.activeElement.blur === "function") {
      document.activeElement.blur();
    }
    setPendingAskScrollTarget(askForm.dataset.scrollTarget || "ask-result");
    markAskActive();
    showAskLoadingState();
    submitAskFormWithPaint(askForm);
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
  askForm.addEventListener("submit", (event) => {
    if (askForm.dataset.submitting === "true") {
      return;
    }
    askForm.dataset.submitting = "true";
    if (isMobileTouch && document.activeElement && typeof document.activeElement.blur === "function") {
      document.activeElement.blur();
    }
    setPendingAskScrollTarget(askForm.dataset.scrollTarget || "ask-result");
    markAskActive();
    showAskLoadingState();
    if (isMobileTouch) {
      event.preventDefault();
      submitAskFormWithPaint(askForm);
    }
  });
}

document.querySelectorAll('form[data-restore-on-submit="true"][data-scroll-target]').forEach((form) => {
  form.addEventListener("submit", () => {
    setPendingAskScrollTarget(form.dataset.scrollTarget || "ask-plex");
  });
});

askClearForms.forEach((form) => {
  form.addEventListener("submit", () => {
    setPendingClearScrollPosition(window.scrollY || window.pageYOffset || 0);
    clearPendingAskScrollTarget();
    setAskMobileLoadingPending(false);
    if (askMobileLoadingPill) {
      askMobileLoadingPill.hidden = true;
    }
    clearAskRestorePendingState();
  });
});

if (questionInput) {
  questionInput.addEventListener("focus", () => {
    markAskActive();
  });
  questionInput.addEventListener("input", () => {
    markAskActive();
  });
}

const pendingClearScrollY = consumePendingClearScrollPosition();
if (pendingClearScrollY !== null) {
  window.scrollTo(0, pendingClearScrollY);
  window.requestAnimationFrame(() => {
    window.scrollTo(0, pendingClearScrollY);
    clearPendingClearRestoreState();
  });
}

const pendingAskScrollTarget = consumePendingAskScrollTarget();
if (isMobileTouch && isAskMobileLoadingPending()) {
  showAskMobileLoadingPill();
}

if (pendingAskScrollTarget) {
  const restoreDelay = isMobileTouch ? 24 : 80;
  const restoreBehavior = isMobileTouch ? "auto" : "auto";
  window.setTimeout(() => {
    scrollToAskTarget(pendingAskScrollTarget, { behavior: restoreBehavior });
    if (isMobileTouch) {
      window.setTimeout(() => {
        hideAskMobileLoadingPill();
      }, 120);
    }
  }, restoreDelay);
} else if (isMobileTouch && askMobileLoadingPill) {
  hideAskMobileLoadingPill();
} else {
  clearAskRestorePendingState();
  clearPendingClearRestoreState();
}

const refreshSeconds = Number(document.body.dataset.autoRefreshSeconds || "0");
if (refreshSeconds > 0) {
  window.setTimeout(() => {
    if (isAskInteractionActive()) {
      return;
    }
    window.location.reload();
  }, refreshSeconds * 1000);
}
