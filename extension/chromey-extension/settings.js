import {
  DEFAULT_CHAT_THEME,
  applyChatTheme,
  clearConversation,
  DEFAULT_PERFORMANCE_PROFILE,
  extractErrorMessage,
  extractModelIds,
  loadSettings,
  normalizeChatTheme,
  normalizePerformanceProfile,
  normalizeScreenshotDimension,
  pickPreferredModel,
  proxyFetch,
  saveSettings,
} from "./shared.js";

const proxyUrlInput = document.getElementById("proxyUrlInput");
const modelInput = document.getElementById("modelInput");
const pageBackgroundInput = document.getElementById("pageBackgroundInput");
const panelBackgroundInput = document.getElementById("panelBackgroundInput");
const assistantBubbleInput = document.getElementById("assistantBubbleInput");
const userBubbleInput = document.getElementById("userBubbleInput");
const accentInput = document.getElementById("accentInput");
const modelHint = document.getElementById("modelHint");
const saveButton = document.getElementById("saveButton");
const testButton = document.getElementById("testButton");
const refreshModelsButton = document.getElementById("refreshModelsButton");
const clearConversationButton = document.getElementById("clearConversationButton");
const resetThemeButton = document.getElementById("resetThemeButton");
const performanceBalancedInput = document.getElementById("performanceBalancedInput");
const performanceFastInput = document.getElementById("performanceFastInput");
const useVisionInput = document.getElementById("useVisionInput");
const screenshotWidthInput = document.getElementById("screenshotWidthInput");
const screenshotHeightInput = document.getElementById("screenshotHeightInput");
const savePerformanceButton = document.getElementById("savePerformanceButton");
const saveStatus = document.getElementById("saveStatus");
const proxyStatus = document.getElementById("proxyStatus");
const providerStatus = document.getElementById("providerStatus");
const browserStatus = document.getElementById("browserStatus");
const modelsStatus = document.getElementById("modelsStatus");
const artifactsStatus = document.getElementById("artifactsStatus");
const themePreview = document.getElementById("themePreview");
const tabButtons = Array.from(document.querySelectorAll("[data-tab]"));
const tabPanels = Array.from(document.querySelectorAll("[data-tab-panel]"));

let currentSettings = {
  proxyBaseUrl: "",
  selectedModel: "",
  performanceProfile: DEFAULT_PERFORMANCE_PROFILE,
  useVision: true,
  chatTheme: normalizeChatTheme(DEFAULT_CHAT_THEME),
};
let loadedModels = [];
const colorInputs = [
  pageBackgroundInput,
  panelBackgroundInput,
  assistantBubbleInput,
  userBubbleInput,
  accentInput,
];

function renderSaveStatus(text, isSaved = false) {
  saveStatus.textContent = text;
  saveStatus.style.color = isSaved ? "var(--success)" : "var(--muted)";
}

function renderModelHint(selectedModel = "", models = loadedModels) {
  const normalizedSelection = (selectedModel || "").trim();
  const preferredModel = pickPreferredModel(models, selectedModel);

  if (normalizedSelection) {
    if (models.includes(normalizedSelection)) {
      modelHint.textContent = "That model is loaded in LM Studio and ready to use.";
    } else if (models.length) {
      modelHint.textContent = "The saved model is not loaded in LM Studio right now.";
    } else {
      modelHint.textContent = "Type the exact LM Studio model id. It must already be loaded.";
    }
    return;
  }

  if (preferredModel) {
    modelHint.textContent = `Leave this blank to auto-use ${preferredModel}.`;
    return;
  }

  modelHint.textContent = "Leave this blank to auto-prefer an IQ4 model, or type the exact LM Studio model id.";
}

function updateLoadedModels(models) {
  loadedModels = Array.isArray(models) ? models.slice() : [];
  renderModelHint(modelInput.value, loadedModels);
}

function collectThemeSettings() {
  return normalizeChatTheme({
    pageBackground: pageBackgroundInput.value,
    panelBackground: panelBackgroundInput.value,
    assistantBubble: assistantBubbleInput.value,
    userBubble: userBubbleInput.value,
    accent: accentInput.value,
  });
}

function collectPerformanceSettings() {
  return {
    performanceProfile: performanceFastInput.checked ? "fast" : "balanced",
    useVision: useVisionInput.checked,
    screenshotWidth: normalizeScreenshotDimension(screenshotWidthInput.value),
    screenshotHeight: normalizeScreenshotDimension(screenshotHeightInput.value),
  };
}

function renderPerformanceControls(settings) {
  const performanceProfile = normalizePerformanceProfile(settings?.performanceProfile);
  performanceBalancedInput.checked = performanceProfile !== "fast";
  performanceFastInput.checked = performanceProfile === "fast";
  useVisionInput.checked = settings?.useVision !== false;
  screenshotWidthInput.value = settings?.screenshotWidth ?? "";
  screenshotHeightInput.value = settings?.screenshotHeight ?? "";
}

function setActiveTab(tabName) {
  tabButtons.forEach((button) => {
    const isActive = button.dataset.tab === tabName;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-selected", isActive ? "true" : "false");
  });
  tabPanels.forEach((panel) => {
    const isActive = panel.dataset.tabPanel === tabName;
    panel.classList.toggle("is-active", isActive);
    panel.hidden = !isActive;
    panel.setAttribute("aria-hidden", isActive ? "false" : "true");
  });
}

function renderThemeControls(theme) {
  const normalized = normalizeChatTheme(theme);
  pageBackgroundInput.value = normalized.pageBackground;
  panelBackgroundInput.value = normalized.panelBackground;
  assistantBubbleInput.value = normalized.assistantBubble;
  userBubbleInput.value = normalized.userBubble;
  accentInput.value = normalized.accent;
  applyChatTheme(normalized, themePreview);
}

async function refreshModels() {
  proxyStatus.textContent = "Checking";
  providerStatus.textContent = "Checking";
  browserStatus.textContent = "Checking";
  const selectedModel = modelInput.value.trim();

  try {
    const [modelsPayload, browserPayload, configPayload] = await Promise.all([
      proxyFetch(currentSettings.proxyBaseUrl, "/v1/models"),
      proxyFetch(currentSettings.proxyBaseUrl, "/api/browser"),
      proxyFetch(currentSettings.proxyBaseUrl, "/api/config"),
    ]);

    const modelsError = extractErrorMessage(modelsPayload);
    proxyStatus.textContent = "Reachable";
    const models = extractModelIds(modelsPayload);
    if ((modelsPayload.ok === false || modelsPayload.error) && modelsError) {
      providerStatus.textContent = modelsError;
      browserStatus.textContent = browserPayload.connected ? "Attached" : browserPayload.hint || "Offline";
      updateLoadedModels([]);
      modelsStatus.textContent = "-";
      artifactsStatus.textContent = configPayload?.runtime?.artifacts_dir || "-";
      return;
    }
    const preferredModel = pickPreferredModel(models, selectedModel);
    providerStatus.textContent = models.length ? `Ready${preferredModel ? ` (${preferredModel})` : ""}` : "No loaded models";
    browserStatus.textContent = browserPayload.connected ? "Attached" : browserPayload.hint || "Offline";
    updateLoadedModels(models);
    modelsStatus.textContent = models.length ? models.join(", ") : "No loaded models";
    artifactsStatus.textContent = configPayload?.runtime?.artifacts_dir || "-";
  } catch (_error) {
    proxyStatus.textContent = "Unavailable";
    providerStatus.textContent = extractErrorMessage(_error) || "Unavailable";
    browserStatus.textContent = "Unavailable";
    modelsStatus.textContent = "-";
    artifactsStatus.textContent = "-";
    updateLoadedModels([]);
  }
}

async function saveCurrentSettings() {
  currentSettings = await saveSettings({
    proxyBaseUrl: proxyUrlInput.value,
    selectedModel: modelInput.value,
    ...collectPerformanceSettings(),
    chatTheme: collectThemeSettings(),
  });
  modelInput.value = currentSettings.selectedModel;
  renderModelHint(currentSettings.selectedModel, loadedModels);
  renderThemeControls(currentSettings.chatTheme);
  renderPerformanceControls(currentSettings);
  renderSaveStatus("Saved", true);
}

async function bootstrap() {
  currentSettings = await loadSettings();
  proxyUrlInput.value = currentSettings.proxyBaseUrl;
  modelInput.value = currentSettings.selectedModel;
  renderThemeControls(currentSettings.chatTheme);
  renderPerformanceControls(currentSettings);
  renderModelHint(currentSettings.selectedModel, loadedModels);
  renderSaveStatus("Saved", true);
  setActiveTab("general");
  await refreshModels();
}

proxyUrlInput.addEventListener("input", () => {
  renderSaveStatus("Unsaved");
});
modelInput.addEventListener("input", () => {
  renderSaveStatus("Unsaved");
  renderModelHint(modelInput.value, loadedModels);
});
colorInputs.forEach((input) => {
  input.addEventListener("input", () => {
    renderSaveStatus("Unsaved");
    renderThemeControls(collectThemeSettings());
  });
});
saveButton.addEventListener("click", () => {
  void saveCurrentSettings();
});
testButton.addEventListener("click", () => {
  currentSettings.proxyBaseUrl = proxyUrlInput.value;
  void refreshModels();
});
refreshModelsButton.addEventListener("click", () => {
  currentSettings.proxyBaseUrl = proxyUrlInput.value;
  void refreshModels();
});
clearConversationButton.addEventListener("click", async () => {
  await clearConversation();
  renderSaveStatus("Conversation cleared", true);
});
resetThemeButton.addEventListener("click", () => {
  renderThemeControls(DEFAULT_CHAT_THEME);
  renderSaveStatus("Unsaved", false);
});
performanceBalancedInput.addEventListener("change", () => {
  renderSaveStatus("Unsaved");
});
performanceFastInput.addEventListener("change", () => {
  renderSaveStatus("Unsaved");
});
useVisionInput.addEventListener("change", () => {
  renderSaveStatus("Unsaved");
});
screenshotWidthInput.addEventListener("input", () => {
  renderSaveStatus("Unsaved");
});
screenshotHeightInput.addEventListener("input", () => {
  renderSaveStatus("Unsaved");
});
savePerformanceButton.addEventListener("click", () => {
  void saveCurrentSettings();
});
tabButtons.forEach((button) => {
  button.addEventListener("click", () => {
    setActiveTab(button.dataset.tab || "general");
  });
});

void bootstrap();
