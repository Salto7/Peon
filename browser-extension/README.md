# Peon Traffic Capture (browser extension)

Captures authenticated browser traffic on the active tab via `chrome.debugger`
(Chrome DevTools Protocol Network domain) — no MITM proxy, no certificate to
install. Batches of request/response pairs are posted to a Peon project's
ingest endpoint and land as `Evidence(kind="traffic", source="browser-extension")`,
the same evidence pipeline the rest of Peon's findings workflow reads.

## Install (unpacked, for local use)

Linked from the project Keystore → Browser traffic capture card
(`peon/templates/projects/detail.html`). Supersedes the old “select this
directory” wording that made people try to upload a zip/crx.
User: "unpacked extension requires uploading something to chrome".

Do **not** pack/upload a `.zip` or `.crx` — Chrome's **Load unpacked** needs
this folder on disk, picked via the folder dialog.

1. `chrome://extensions` → enable **Developer mode** → **Load unpacked** →
   choose this `browser-extension/` folder (must contain `manifest.json` + icons).
2. Open the target Peon project → **Keystore** → **Browser traffic capture**.
   Copy Server URL, Project ID, and **Show** the token.
3. Open the extension popup, paste all three → **Save**.
4. On the target site tab: **Start capture on this tab** → browse → **Stop**.
   Batches flush every ~5s (and on stop).

## What gets sent

Per exchange: method, URL, status, request/response headers, request/response
bodies (text only — image/font/audio/video bodies are never read). Bodies are
capped at 200KB each; a batch at 100 exchanges or 8MB, whichever comes first.

## Security notes

- The token (`Project.traffic_capture_token`) is a bearer credential scoped to
  one project's ingest endpoint only — it cannot read or modify anything else
  in Peon. Regenerating it from the Settings card immediately invalidates the
  old one.
- This captures whatever the browser legitimately sees while the operator
  browses — it does not intercept or modify traffic, and it is only active on
  tabs the operator explicitly starts it on.
- Rotate the token (Settings → Browser extension → Regenerate) when done with
  an engagement, or if a laptop with the extension configured on it is lost.
