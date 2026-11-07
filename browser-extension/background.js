// Peon Traffic Capture — background service worker.
//
// Uses chrome.debugger (Chrome DevTools Protocol) on the active tab's
// Network domain, not a MITM proxy — no certificate to install, works on
// any HTTPS site the operator can already browse to. Text bodies only
// (image/font/audio/video are skipped before ever reading the body, so a
// page full of assets never bloats a batch). Batches are flushed on a timer
// or when they cross a size cap, POSTed to the configured Peon project's
// ingest endpoint with a bearer token — never the operator's Peon session
// cookie, since this worker has no cookie jar for Peon's own origin anyway.

const FLUSH_INTERVAL_MS = 5000;
const MAX_BATCH_EXCHANGES = 100;
const MAX_BODY_CHARS = 200000; // matches the server's per-exchange cap
const SKIP_MIME_PREFIXES = ["image/", "font/", "audio/", "video/"];

// tabId -> { pending: Map<requestId, partialExchange>, batch: [exchange...] }
const state = new Map();

function tabState(tabId) {
  let s = state.get(tabId);
  if (!s) {
    s = { pending: new Map(), batch: [], flushTimer: null };
    state.set(tabId, s);
  }
  return s;
}

async function getConfig() {
  const { peonConfig } = await chrome.storage.local.get("peonConfig");
  return peonConfig || null;
}

async function flush(tabId) {
  const s = state.get(tabId);
  if (!s || s.batch.length === 0) return;
  const config = await getConfig();
  if (!config || !config.serverUrl || !config.projectId || !config.token) return;
  const exchanges = s.batch.splice(0, s.batch.length);
  const url = `${config.serverUrl.replace(/\/+$/, "")}/projects/${config.projectId}/traffic/ingest/`;
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${config.token}`,
      },
      body: JSON.stringify({ exchanges }),
    });
    if (!res.ok) {
      console.warn("Peon traffic capture: ingest failed", res.status, await res.text());
    }
  } catch (err) {
    console.warn("Peon traffic capture: ingest request failed", err);
  }
}

function queueExchange(tabId, exchange) {
  const s = tabState(tabId);
  s.batch.push(exchange);
  if (s.batch.length >= MAX_BATCH_EXCHANGES) flush(tabId);
}

function isSkippableMime(mimeType) {
  const mt = (mimeType || "").toLowerCase();
  return SKIP_MIME_PREFIXES.some((p) => mt.startsWith(p));
}

async function onDebuggerEvent(source, method, params) {
  const { tabId } = source;
  const s = tabState(tabId);

  if (method === "Network.requestWillBeSent") {
    const req = params.request || {};
    s.pending.set(params.requestId, {
      method: req.method,
      url: req.url,
      request_headers: req.headers || {},
      request_body:
        req.postData !== undefined ? String(req.postData).slice(0, MAX_BODY_CHARS) : "",
      timestamp: new Date().toISOString(),
      status: 0,
      response_headers: {},
      response_body: "",
      _mimeType: "",
    });
    return;
  }

  if (method === "Network.responseReceived") {
    const entry = s.pending.get(params.requestId);
    if (!entry) return;
    const resp = params.response || {};
    entry.status = resp.status || 0;
    entry.response_headers = resp.headers || {};
    entry._mimeType = resp.mimeType || "";
    return;
  }

  if (method === "Network.loadingFinished") {
    const entry = s.pending.get(params.requestId);
    s.pending.delete(params.requestId);
    if (!entry) return;
    if (isSkippableMime(entry._mimeType)) {
      delete entry._mimeType;
      queueExchange(tabId, entry);
      return;
    }
    try {
      const body = await chrome.debugger.sendCommand(
        { tabId },
        "Network.getResponseBody",
        { requestId: params.requestId },
      );
      if (body && !body.base64Encoded) {
        entry.response_body = String(body.body || "").slice(0, MAX_BODY_CHARS);
      }
    } catch (_err) {
      // Body already evicted (CDP does this for large/old responses) — ship
      // the exchange without it rather than dropping it entirely.
    }
    delete entry._mimeType;
    queueExchange(tabId, entry);
    return;
  }

  if (method === "Network.loadingFailed") {
    s.pending.delete(params.requestId);
  }
}

chrome.debugger.onEvent.addListener(onDebuggerEvent);

chrome.debugger.onDetach.addListener((source) => {
  const s = state.get(source.tabId);
  if (s && s.flushTimer) clearInterval(s.flushTimer);
  state.delete(source.tabId);
});

async function startCapture(tabId) {
  await chrome.debugger.attach({ tabId }, "1.3");
  await chrome.debugger.sendCommand({ tabId }, "Network.enable", {});
  const s = tabState(tabId);
  s.flushTimer = setInterval(() => flush(tabId), FLUSH_INTERVAL_MS);
}

async function stopCapture(tabId) {
  await flush(tabId);
  try {
    await chrome.debugger.detach({ tabId });
  } catch (_err) {
    // already detached (e.g. the tab navigated away or closed)
  }
  const s = state.get(tabId);
  if (s && s.flushTimer) clearInterval(s.flushTimer);
  state.delete(tabId);
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "peon-start") {
    startCapture(msg.tabId).then(() => sendResponse({ ok: true })).catch((err) =>
      sendResponse({ ok: false, error: String(err) }),
    );
    return true;
  }
  if (msg.type === "peon-stop") {
    stopCapture(msg.tabId).then(() => sendResponse({ ok: true })).catch((err) =>
      sendResponse({ ok: false, error: String(err) }),
    );
    return true;
  }
  if (msg.type === "peon-status") {
    sendResponse({ ok: true, capturing: state.has(msg.tabId) });
    return true;
  }
});
