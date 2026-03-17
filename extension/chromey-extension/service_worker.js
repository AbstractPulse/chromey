async function configureSidePanel() {
  try {
    await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  } catch (error) {
    console.warn("Could not configure side panel behavior", error);
  }
}

chrome.runtime.onInstalled.addListener(() => {
  void configureSidePanel();
});

chrome.runtime.onStartup.addListener(() => {
  void configureSidePanel();
});

chrome.action.onClicked.addListener((tab) => {
  const windowId = tab.windowId;
  if (typeof windowId !== "number" || windowId < 0) {
    return;
  }

  void chrome.sidePanel.open({ windowId }).catch((error) => {
    console.warn("Could not open side panel", error);
  });
});
