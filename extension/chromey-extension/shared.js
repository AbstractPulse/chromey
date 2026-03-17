export const DEFAULT_PROXY_BASE_URL = "http://127.0.0.1:8089";
export const SETTINGS_STORAGE_KEY = "chromeySettings";
export const HISTORY_STORAGE_KEY = "chromeyConversation";
export const HISTORY_LIMIT = 40;
export const DEFAULT_PERFORMANCE_PROFILE = "balanced";
export const DEFAULT_SCREENSHOT_WIDTH = null;
export const DEFAULT_SCREENSHOT_HEIGHT = null;
export const DEFAULT_CHAT_THEME = Object.freeze({
  pageBackground: "#081018",
  panelBackground: "#0c141f",
  assistantBubble: "#1a2434",
  userBubble: "#3e89e7",
  accent: "#76c2ff",
});

export function normalizeProxyBaseUrl(value) {
  return (value || DEFAULT_PROXY_BASE_URL).trim().replace(/\/+$/, "");
}

export function normalizeModelSelection(value) {
  return (value || "").trim();
}

export function normalizePerformanceProfile(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return normalized === "fast" ? "fast" : DEFAULT_PERFORMANCE_PROFILE;
}

export function normalizeUseVision(value) {
  return value !== false;
}

export function normalizeScreenshotDimension(value) {
  if (value === "" || value === null || value === undefined) {
    return null;
  }
  const normalized = Number.parseInt(String(value), 10);
  if (!Number.isFinite(normalized)) {
    return null;
  }
  if (normalized < 256) {
    return 256;
  }
  if (normalized > 2048) {
    return 2048;
  }
  return normalized;
}

function clampChannel(value) {
  return Math.max(0, Math.min(255, Math.round(Number(value) || 0)));
}

function formatHexColor({ r, g, b }) {
  return `#${[r, g, b]
    .map((channel) => clampChannel(channel).toString(16).padStart(2, "0"))
    .join("")}`;
}

function parseHexColor(value, fallback = "#000000") {
  const normalized = normalizeHexColor(value, fallback);
  return {
    r: Number.parseInt(normalized.slice(1, 3), 16),
    g: Number.parseInt(normalized.slice(3, 5), 16),
    b: Number.parseInt(normalized.slice(5, 7), 16),
  };
}

function shiftHexColor(value, delta) {
  const { r, g, b } = parseHexColor(value);
  return formatHexColor({
    r: r + delta,
    g: g + delta,
    b: b + delta,
  });
}

function hexToRgbTriplet(value) {
  const { r, g, b } = parseHexColor(value);
  return `${r}, ${g}, ${b}`;
}

export function normalizeHexColor(value, fallback) {
  const candidate = String(value || "").trim();
  if (!candidate) {
    return fallback;
  }

  const raw = candidate.startsWith("#") ? candidate.slice(1) : candidate;
  if (/^[0-9a-f]{3}$/i.test(raw)) {
    return `#${raw
      .split("")
      .map((digit) => `${digit}${digit}`)
      .join("")
      .toLowerCase()}`;
  }

  if (/^[0-9a-f]{6}$/i.test(raw)) {
    return `#${raw.toLowerCase()}`;
  }

  return fallback;
}

export function normalizeChatTheme(value) {
  const payload = value && typeof value === "object" ? value : {};
  return {
    pageBackground: normalizeHexColor(payload.pageBackground, DEFAULT_CHAT_THEME.pageBackground),
    panelBackground: normalizeHexColor(payload.panelBackground, DEFAULT_CHAT_THEME.panelBackground),
    assistantBubble: normalizeHexColor(payload.assistantBubble, DEFAULT_CHAT_THEME.assistantBubble),
    userBubble: normalizeHexColor(payload.userBubble, DEFAULT_CHAT_THEME.userBubble),
    accent: normalizeHexColor(payload.accent, DEFAULT_CHAT_THEME.accent),
  };
}

export function sanitizeMessages(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .filter((item) => item && typeof item.role === "string" && typeof item.content === "string")
    .map((item) => ({
      role: item.role,
      content: item.content,
    }));
}

export function trimConversation(messages, limit = HISTORY_LIMIT) {
  return sanitizeMessages(messages).slice(-limit);
}

export function buildConversationHistory(messages, limit = 12) {
  return sanitizeMessages(messages)
    .filter((item) => item.role === "user" || item.role === "assistant")
    .slice(-limit);
}

export async function loadSettings() {
  const stored = await chrome.storage.local.get(SETTINGS_STORAGE_KEY);
  const payload = stored[SETTINGS_STORAGE_KEY] || {};
  return {
    proxyBaseUrl: normalizeProxyBaseUrl(payload.proxyBaseUrl),
    selectedModel: normalizeModelSelection(payload.selectedModel || payload.modelOverride),
    performanceProfile: normalizePerformanceProfile(payload.performanceProfile),
    useVision: normalizeUseVision(payload.useVision),
    screenshotWidth: normalizeScreenshotDimension(payload.screenshotWidth),
    screenshotHeight: normalizeScreenshotDimension(payload.screenshotHeight),
    chatTheme: normalizeChatTheme(payload.chatTheme),
  };
}

export async function saveSettings(settings) {
  const normalized = {
    proxyBaseUrl: normalizeProxyBaseUrl(settings?.proxyBaseUrl),
    selectedModel: normalizeModelSelection(settings?.selectedModel),
    performanceProfile: normalizePerformanceProfile(settings?.performanceProfile),
    useVision: normalizeUseVision(settings?.useVision),
    screenshotWidth: normalizeScreenshotDimension(settings?.screenshotWidth),
    screenshotHeight: normalizeScreenshotDimension(settings?.screenshotHeight),
    chatTheme: normalizeChatTheme(settings?.chatTheme),
  };
  await chrome.storage.local.set({ [SETTINGS_STORAGE_KEY]: normalized });
  return normalized;
}

export function applyChatTheme(theme, target = document.documentElement) {
  const normalized = normalizeChatTheme(theme);
  if (!target?.style) {
    return normalized;
  }

  target.style.setProperty("--theme-page-bg", normalized.pageBackground);
  target.style.setProperty("--theme-page-bg-top", shiftHexColor(normalized.pageBackground, 6));
  target.style.setProperty("--theme-page-bg-bottom", shiftHexColor(normalized.pageBackground, -6));
  target.style.setProperty("--theme-panel", normalized.panelBackground);
  target.style.setProperty("--theme-panel-strong", shiftHexColor(normalized.panelBackground, -8));
  target.style.setProperty("--theme-panel-soft", `rgba(${hexToRgbTriplet(normalized.panelBackground)}, 0.52)`);
  target.style.setProperty("--theme-assistant-bubble", normalized.assistantBubble);
  target.style.setProperty(
    "--theme-assistant-border",
    `rgba(${hexToRgbTriplet(normalized.assistantBubble)}, 0.85)`,
  );
  target.style.setProperty("--theme-user-bubble", normalized.userBubble);
  target.style.setProperty("--theme-user-bubble-strong", shiftHexColor(normalized.userBubble, -22));
  target.style.setProperty("--theme-user-bubble-rgb", hexToRgbTriplet(normalized.userBubble));
  target.style.setProperty("--theme-accent", normalized.accent);
  target.style.setProperty("--theme-accent-strong", shiftHexColor(normalized.accent, -18));
  target.style.setProperty("--theme-accent-rgb", hexToRgbTriplet(normalized.accent));
  return normalized;
}

export async function loadConversation() {
  const stored = await chrome.storage.local.get(HISTORY_STORAGE_KEY);
  return trimConversation(stored[HISTORY_STORAGE_KEY]);
}

export async function saveConversation(messages) {
  const normalized = trimConversation(messages);
  await chrome.storage.local.set({ [HISTORY_STORAGE_KEY]: normalized });
  return normalized;
}

export async function clearConversation() {
  await chrome.storage.local.set({ [HISTORY_STORAGE_KEY]: [] });
}

export async function proxyFetch(proxyBaseUrl, path, options = {}) {
  const response = await fetch(`${normalizeProxyBaseUrl(proxyBaseUrl)}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });

  const rawText = await response.text();
  let payload;
  try {
    payload = rawText ? JSON.parse(rawText) : {};
  } catch (_error) {
    payload = {
      ok: response.ok,
      error: rawText || `HTTP ${response.status}`,
    };
  }

  if (!response.ok && payload.ok !== false) {
    payload.ok = false;
    payload.error = payload.error || `HTTP ${response.status}`;
  }

  return payload;
}

export function extractModelIds(payload) {
  if (!Array.isArray(payload?.data)) {
    return [];
  }
  return payload.data
    .filter((item) => item && typeof item.id === "string" && item.id.trim())
    .map((item) => item.id.trim());
}

export function extractChatCompletionText(payload) {
  const text = payload?.choices?.[0]?.message?.content;
  return typeof text === "string" ? text.trim() : "";
}

export function extractErrorMessage(payload) {
  if (typeof payload?.error === "string" && payload.error.trim()) {
    return payload.error.trim();
  }
  if (typeof payload?.error?.message === "string" && payload.error.message.trim()) {
    return payload.error.message.trim();
  }
  if (typeof payload?.message === "string" && payload.message.trim()) {
    return payload.message.trim();
  }
  return "";
}

export function pickPreferredModel(models, preferredModel = "") {
  const modelIds = Array.isArray(models) ? models.filter((item) => typeof item === "string" && item.trim()) : [];
  const preferred = normalizeModelSelection(preferredModel);

  if (preferred && modelIds.includes(preferred)) {
    return preferred;
  }

  const iq4Match = modelIds.find((item) => /iq4/i.test(item));
  if (iq4Match) {
    return iq4Match;
  }

  return modelIds[0] || "";
}
