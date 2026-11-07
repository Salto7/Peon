const serverUrlEl = document.getElementById("serverUrl");
const projectIdEl = document.getElementById("projectId");
const tokenEl = document.getElementById("token");
const saveBtn = document.getElementById("save");
const toggleBtn = document.getElementById("toggle");
const statusEl = document.getElementById("status");

async function currentTabId() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab?.id;
}

async function loadConfig() {
  const { peonConfig } = await chrome.storage.local.get("peonConfig");
  if (peonConfig) {
    serverUrlEl.value = peonConfig.serverUrl || "";
    projectIdEl.value = peonConfig.projectId || "";
    tokenEl.value = peonConfig.token || "";
  }
}

async function refreshStatus() {
  const tabId = await currentTabId();
  if (!tabId) return;
  const res = await chrome.runtime.sendMessage({ type: "peon-status", tabId });
  const capturing = res?.capturing;
  statusEl.textContent = capturing ? "Capturing on this tab." : "Not capturing.";
  statusEl.className = capturing ? "on" : "";
  toggleBtn.textContent = capturing ? "Stop capture" : "Start capture on this tab";
}

saveBtn.addEventListener("click", async () => {
  await chrome.storage.local.set({
    peonConfig: {
      serverUrl: serverUrlEl.value.trim(),
      projectId: projectIdEl.value.trim(),
      token: tokenEl.value.trim(),
    },
  });
  saveBtn.textContent = "Saved";
  setTimeout(() => (saveBtn.textContent = "Save"), 1200);
});

toggleBtn.addEventListener("click", async () => {
  const tabId = await currentTabId();
  if (!tabId) return;
  const statusRes = await chrome.runtime.sendMessage({ type: "peon-status", tabId });
  const msgType = statusRes?.capturing ? "peon-stop" : "peon-start";
  const res = await chrome.runtime.sendMessage({ type: msgType, tabId });
  if (!res?.ok) {
    statusEl.textContent = `Error: ${res?.error || "unknown"}`;
    return;
  }
  await refreshStatus();
});

loadConfig();
refreshStatus();
