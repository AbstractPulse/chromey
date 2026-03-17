import {
  applyChatTheme,
  buildConversationHistory,
  clearConversation,
  extractChatCompletionText,
  extractErrorMessage,
  extractModelIds,
  loadConversation,
  loadSettings,
  pickPreferredModel,
  proxyFetch,
  saveConversation,
} from "./shared.js";

const emptyState = document.getElementById("emptyState");
const messages = document.getElementById("messages");
const modelChip = document.getElementById("modelChip");
const runtimeChip = document.getElementById("runtimeChip");
const panel = document.querySelector(".panel");
const composerForm = document.getElementById("composerForm");
const messageInput = document.getElementById("messageInput");
const sendButton = document.getElementById("sendButton");
const stopButton = document.getElementById("stopButton");
const newChatButton = document.getElementById("newChatButton");
const settingsButton = document.getElementById("settingsButton");

let currentSettings = {
  proxyBaseUrl: "",
  selectedModel: "",
  performanceProfile: "balanced",
  useVision: true,
  screenshotWidth: null,
  screenshotHeight: null,
  chatTheme: null,
};
let conversation = [];
let currentSnapshotKey = "";
let lastResultKey = "";
let isSending = false;
let pollTimer = null;
let currentModelLabel = "Auto (prefer IQ4)";
let currentRuntimeLabel = "Connecting";
let currentRuntimeHint = "Connecting to the local proxy.";
let currentSessionState = "idle";
let currentSnapshot = null;

function truncateText(value, maxLength = 72) {
  const text = String(value || "").trim().replace(/\s+/g, " ");
  if (!text || text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, Math.max(0, maxLength - 1)).trimEnd()}…`;
}

function normalizeSnapshotNote(value) {
  return truncateText(
    String(value || "")
      .replace(/\s*Saved \d+ screenshots?\./gi, "")
      .replace(/^Browser task is running\.\s*/i, "")
      .trim(),
    76,
  );
}

function isOperationalReply(text) {
  const normalized = String(text || "").trim();
  if (!normalized) {
    return false;
  }
  return (
    normalized === "Working on it in Chrome." ||
    normalized === "Redirecting the task in Chrome." ||
    /Started the browser task\./i.test(normalized) ||
    /Redirecting the browser task/i.test(normalized) ||
    /Saving screenshots to/i.test(normalized)
  );
}

function pruneConversation(messagesList) {
  if (!Array.isArray(messagesList)) {
    return [];
  }
  return messagesList.filter(
    (item) => !(item?.role === "assistant" && isOperationalReply(item?.content)),
  );
}

function createMessageNode(role, text) {
  const wrapper = document.createElement("article");
  wrapper.className = `message message--${role}`;

  const meta = document.createElement("span");
  meta.className = "message__meta";
  meta.textContent =
    role === "user" ? "You" : role === "assistant" ? "Chromey" : "Status";
  wrapper.appendChild(meta);

  const body = document.createElement("div");
  body.className = "message__body";
  body.textContent = text;
  wrapper.appendChild(body);
  return wrapper;
}

function renderStatusChips() {
  modelChip.textContent = currentModelLabel;
  modelChip.title = currentModelLabel;
  runtimeChip.textContent = currentRuntimeLabel;
  runtimeChip.title = currentRuntimeHint || currentRuntimeLabel;
  runtimeChip.dataset.state = currentSessionState;
  if (panel) {
    panel.dataset.state = currentSessionState;
  }
}

function renderConversation() {
  emptyState.hidden = conversation.length > 0;
  messages.hidden = conversation.length === 0;
  messages.replaceChildren(...conversation.map((item) => createMessageNode(item.role, item.content)));
  messages.scrollTop = messages.scrollHeight;
}

async function persistConversation() {
  conversation = await saveConversation(conversation);
  renderConversation();
}

async function appendMessage(role, content) {
  const text = (content || "").trim();
  if (!text) {
    return;
  }
  conversation.push({ role, content: text });
  await persistConversation();
}

function setBusyState(nextBusy) {
  isSending = nextBusy;
  sendButton.disabled = nextBusy;
}

function autosizeComposer() {
  messageInput.style.height = "0px";
  messageInput.style.height = `${Math.min(messageInput.scrollHeight, 180)}px`;
}

function updateModelChip(providerPayload = null) {
  currentModelLabel =
    providerPayload?.selected_model ||
    currentSettings.selectedModel ||
    providerPayload?.requested_model ||
    "Auto (prefer IQ4)";
  renderStatusChips();
}

function syncRuntimeState(browserPayload = null, sessionPayload = null) {
  const snapshot = sessionPayload?.snapshot || null;
  const snapshotState = snapshot?.state || "idle";

  if (snapshotState === "running") {
    currentSessionState = "running";
    currentRuntimeLabel =
      snapshot?.step > 0
        ? truncateText(`Step ${snapshot.step} · ${normalizeSnapshotNote(snapshot.note) || "Working in Chrome"}`, 64)
        : "Working in Chrome";
    currentRuntimeHint = snapshot?.note || sessionPayload?.status_text || "The browser task is running.";
  } else if (snapshotState === "completed") {
    currentSessionState = "completed";
    currentRuntimeLabel = "Task complete";
    currentRuntimeHint = snapshot?.last_result || snapshot?.note || "The browser task completed.";
  } else if (snapshotState === "failed") {
    currentSessionState = "failed";
    currentRuntimeLabel = "Task failed";
    currentRuntimeHint = snapshot?.last_result || snapshot?.note || "The browser task failed.";
  } else if (snapshotState === "stopped") {
    currentSessionState = "stopped";
    currentRuntimeLabel = "Stopped";
    currentRuntimeHint = snapshot?.note || "The browser task is stopped.";
  } else if (!browserPayload?.connected) {
    currentSessionState = "offline";
    currentRuntimeLabel = "Chrome offline";
    currentRuntimeHint = browserPayload?.hint || "Chrome is not attached yet.";
  } else {
    currentSessionState = "idle";
    currentRuntimeLabel = "Ready";
    currentRuntimeHint = "Chrome is connected and ready.";
  }

  renderStatusChips();
}

function updateSessionView(sessionPayload = null) {
  const snapshot = sessionPayload?.snapshot || null;
  currentSnapshot = snapshot;
  const state = snapshot?.state || "idle";
  const allowStop = state === "running";
  stopButton.hidden = !allowStop;
  stopButton.disabled = !allowStop;

  if (!snapshot) {
    return;
  }

  const snapshotKey = JSON.stringify(snapshot);
  if (snapshotKey === currentSnapshotKey) {
    return;
  }
  currentSnapshotKey = snapshotKey;

  const resultText = (snapshot.last_result || "").trim();
  const resultKey = `${snapshot.state}:${resultText}`;
  if (!resultText || resultKey === lastResultKey) {
    return;
  }

  if (snapshot.state === "completed") {
    const latestMessage = conversation[conversation.length - 1];
    if (latestMessage?.role === "assistant" && latestMessage.content === resultText) {
      lastResultKey = resultKey;
      return;
    }
    lastResultKey = resultKey;
    void appendMessage("assistant", resultText);
  } else if (snapshot.state === "failed") {
    const latestMessage = conversation[conversation.length - 1];
    if (latestMessage?.role === "notice" && latestMessage.content === resultText) {
      lastResultKey = resultKey;
      return;
    }
    lastResultKey = resultKey;
    void appendMessage("notice", resultText);
  }
}

async function refreshProvider() {
  const payload = await proxyFetch(currentSettings.proxyBaseUrl, "/v1/models");
  const errorMessage = extractErrorMessage(payload);
  if ((payload.ok === false || payload.error) && errorMessage) {
    throw new Error(errorMessage);
  }
  const modelIds = extractModelIds(payload);
  updateModelChip({
    selected_model: currentSettings.selectedModel || pickPreferredModel(modelIds),
  });
  return payload;
}

async function refreshRuntime() {
  try {
    const [browserPayload, sessionPayload] = await Promise.all([
      proxyFetch(currentSettings.proxyBaseUrl, "/api/browser"),
      proxyFetch(currentSettings.proxyBaseUrl, "/api/session"),
    ]);

    updateSessionView(sessionPayload);
    syncRuntimeState(browserPayload, sessionPayload);
  } catch (_error) {
    currentSessionState = "offline";
    currentRuntimeLabel = "Proxy offline";
    currentRuntimeHint = "Could not reach the local proxy.";
    stopButton.hidden = true;
    renderStatusChips();
  }
}

async function refreshAll({ includeProvider = true } = {}) {
  if (includeProvider) {
    try {
      await refreshProvider();
    } catch (_error) {
      updateModelChip(null);
    }
  }
  await refreshRuntime();
}

function schedulePoll(delayMs = 2500) {
  if (pollTimer) {
    window.clearTimeout(pollTimer);
  }

  pollTimer = window.setTimeout(async () => {
    await refreshRuntime();
    const nextDelay = currentSessionState === "running" ? 1200 : 3500;
    schedulePoll(nextDelay);
  }, delayMs);
}

async function sendMessage(event) {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message || isSending) {
    return;
  }

  await appendMessage("user", message);
  messageInput.value = "";
  autosizeComposer();
  setBusyState(true);

  try {
    const payload = await proxyFetch(currentSettings.proxyBaseUrl, "/v1/chat/completions", {
      method: "POST",
      body: JSON.stringify({
        model: currentSettings.selectedModel || undefined,
        performance_profile: currentSettings.performanceProfile,
        use_vision: currentSettings.useVision,
        screenshot_width: currentSettings.screenshotWidth,
        screenshot_height: currentSettings.screenshotHeight,
        messages: buildConversationHistory(conversation),
        stream: false,
      }),
    });

    if (payload.ok === false || payload.error) {
      await appendMessage("notice", extractErrorMessage(payload) || "The proxy returned an error.");
    } else {
      const replyText = extractChatCompletionText(payload);
      if (replyText && !isOperationalReply(replyText)) {
        await appendMessage("assistant", replyText);
      }
    }

    await refreshRuntime();
  } catch (_error) {
    await appendMessage("notice", "Could not reach the local proxy.");
    currentSessionState = "offline";
    currentRuntimeLabel = "Proxy offline";
    currentRuntimeHint = "Could not reach the local proxy.";
    renderStatusChips();
  } finally {
    setBusyState(false);
    messageInput.focus();
  }
}

async function stopRun() {
  stopButton.disabled = true;
  try {
    const payload = await proxyFetch(currentSettings.proxyBaseUrl, "/api/session/stop", {
      method: "POST",
      body: JSON.stringify({}),
    });
    updateSessionView(payload);
    syncRuntimeState({ connected: true }, payload);
    await appendMessage("notice", payload.reply || "Stopped the browser task.");
    await refreshRuntime();
  } catch (_error) {
    await appendMessage("notice", "Could not reach the local proxy to stop the task.");
  }
}

async function startNewChat() {
  newChatButton.disabled = true;
  try {
    await proxyFetch(currentSettings.proxyBaseUrl, "/api/session/reset", {
        method: "POST",
        body: JSON.stringify({}),
      }).catch(() => null);

    await clearConversation();
    conversation = [];
    currentSnapshot = null;
    currentSnapshotKey = "";
    lastResultKey = "";
    currentSessionState = "idle";
    currentRuntimeLabel = "Ready";
    currentRuntimeHint = "Chrome is connected and ready.";
    renderStatusChips();
    renderConversation();
    await refreshRuntime();
    messageInput.focus();
  } finally {
    newChatButton.disabled = false;
  }
}

async function handleStorageChange(changes, areaName) {
  if (areaName !== "local") {
    return;
  }

  if (changes.chromeySettings) {
    currentSettings = await loadSettings();
    applyChatTheme(currentSettings.chatTheme);
    await refreshAll();
  }

  if (changes.chromeyConversation) {
    conversation = pruneConversation(await loadConversation());
    renderConversation();
  }
}

async function bootstrap() {
  currentSettings = await loadSettings();
  applyChatTheme(currentSettings.chatTheme);
  const loadedConversation = await loadConversation();
  conversation = pruneConversation(loadedConversation);
  if (conversation.length !== loadedConversation.length) {
    conversation = await saveConversation(conversation);
  }
  renderConversation();
  autosizeComposer();
  renderStatusChips();
  updateModelChip(null);
  await refreshProvider().catch(() => {
    updateModelChip(null);
  });
  await refreshRuntime();
  schedulePoll();
}

composerForm.addEventListener("submit", (event) => {
  void sendMessage(event);
});
messageInput.addEventListener("input", autosizeComposer);
messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    composerForm.requestSubmit();
  }
});
stopButton.addEventListener("click", () => {
  void stopRun();
});
newChatButton.addEventListener("click", () => {
  void startNewChat();
});
settingsButton.addEventListener("click", () => {
  void chrome.runtime.openOptionsPage();
});
chrome.storage.local.onChanged.addListener((changes, areaName) => {
  void handleStorageChange(changes, areaName);
});
window.addEventListener("beforeunload", () => {
  if (pollTimer) {
    window.clearTimeout(pollTimer);
  }
});

void bootstrap();
