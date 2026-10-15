/* Sandbox terminal (xterm) — captures shell hotkeys when focused. */
(function (global) {
  "use strict";

  var _focused = false;
  var _session = null;
  var _term = null;
  var _fit = null;
  var _es = null;
  var _host = null;
  var _cfg = null;
  var _sending = false;
  var _queue = [];
  var _retryTimer = null;
  var _retries = 0;
  var _userClosed = false;

  function csrfToken() {
    return window.PeonUI.csrfToken();
  }

  function isFocused() {
    // Capture page shortcuts whenever the terminal panel has focus
    // (connected or ready), so Peon/browser chords do not steal keys.
    return !!_focused;
  }

  function b64ToUint8(b64) {
    var bin = atob(b64);
    var out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  function uint8ToB64(u8) {
    var s = "";
    for (var i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]);
    return btoa(s);
  }

  var _probe = null;

  // Tokens are oklch(); xterm only parses hex/rgb, so rasterise one pixel.
  function toRgb(color, fallback) {
    try {
      if (!_probe) {
        var c = document.createElement("canvas");
        c.width = c.height = 1;
        _probe = c.getContext("2d", { willReadFrequently: true });
      }
      _probe.clearRect(0, 0, 1, 1);
      _probe.fillStyle = fallback;
      _probe.fillStyle = color;
      _probe.fillRect(0, 0, 1, 1);
      var d = _probe.getImageData(0, 0, 1, 1).data;
      return "rgb(" + d[0] + ", " + d[1] + ", " + d[2] + ")";
    } catch (_) {
      return fallback;
    }
  }

  function cssVar(name, fallback) {
    try {
      var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v ? toRgb(v, fallback) : fallback;
    } catch (_) {
      return fallback;
    }
  }

  function terminalTheme() {
    // Design tokens (frontend/src/styles/tokens.css).
    return {
      background: cssVar("--canvas", "#0a1018"),
      foreground: cssVar("--foreground", "#eef6fb"),
      cursor: cssVar("--primary", "#5b6cf0"),
      selectionBackground: "rgba(120, 130, 240, 0.35)",
    };
  }

  function applyTerminalTheme() {
    if (_term && typeof _term.options !== "undefined") {
      _term.options.theme = terminalTheme();
    }
  }

  function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": csrfToken(),
      },
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      return r.text().then(function (text) {
        var data = null;
        try {
          data = text ? JSON.parse(text) : null;
        } catch (_) {
          data = {
            ok: false,
            error: text
              ? "Server error (" + r.status + ")"
              : "Empty response (" + r.status + ")",
          };
        }
        return { ok: r.ok, status: r.status, data: data };
      });
    });
  }

  function flushInput() {
    if (_sending || !_session || !_queue.length) return;
    _sending = true;
    var chunk = _queue.splice(0, _queue.length).join("");
    var bytes = new TextEncoder().encode(chunk);
    postJson(_cfg.inputUrl(_session.id), {
      data: uint8ToB64(bytes),
      encoding: "base64",
    })
      .catch(function () {
        return null;
      })
      .then(function (res) {
        _sending = false;
        if (res && (res.status === 404 || res.status === 410)) {
          // PTY gone (server restart / shell exited): reattach, keep keystrokes.
          _queue.unshift(chunk);
          scheduleReconnect("Session lost — reconnecting…");
          return;
        }
        if (_queue.length) flushInput();
      });
  }

  function sendData(data) {
    if (!_session) return;
    _queue.push(data);
    flushInput();
  }

  /**
   * When terminal is focused, keep shell semantics:
   * - Ctrl+C → interrupt (unless selection → copy)
   * - Ctrl+Shift+C / Ctrl+Shift+V → copy / paste
   * - Ctrl+R → reverse-i-search (to PTY)
   * - Ctrl/Cmd+K, Shift+S → do NOT open Peon palette
   * Note: some browser/OS chords (Ctrl+W/T/N, Ctrl+Shift+T) cannot be
   * cancelled from a web page.
   */
  function attachKeyHandler(term) {
    term.attachCustomKeyEventHandler(function (ev) {
      if (ev.type !== "keydown") return true;
      var key = ev.key || "";
      var lower = key.length === 1 ? key.toLowerCase() : key;
      var ctrl = !!ev.ctrlKey;
      var meta = !!ev.metaKey;
      var shift = !!ev.shiftKey;
      var alt = !!ev.altKey;

      // Copy: Ctrl+Shift+C, or Ctrl/Cmd+C with an active selection
      if (
        (ctrl && shift && lower === "c") ||
        ((ctrl || meta) && !shift && lower === "c" && term.hasSelection())
      ) {
        var sel = term.getSelection();
        if (sel && navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(sel).catch(function () {});
        }
        ev.preventDefault();
        ev.stopPropagation();
        return false;
      }

      // Paste: Ctrl+Shift+V (and Cmd+V on mac — paste into terminal)
      if ((ctrl && shift && lower === "v") || (meta && !shift && lower === "v")) {
        ev.preventDefault();
        ev.stopPropagation();
        if (navigator.clipboard && navigator.clipboard.readText) {
          navigator.clipboard.readText().then(function (t) {
            if (t) term.paste(t);
          });
        }
        return false;
      }

      // Everything else: let xterm/PTY handle it; page capture is in
      // stopGlobalShortcuts (capture phase).
      return true;
    });
  }

  function bindFocusGuards(host) {
    host.addEventListener("focusin", function () {
      _focused = true;
      document.body.classList.add("terminal-focused");
    });
    host.addEventListener("focusout", function (ev) {
      var next = ev.relatedTarget;
      if (next && host.contains(next)) return;
      // Defer: click inside xterm textarea may briefly blur
      setTimeout(function () {
        if (!_host) return;
        if (_host.contains(document.activeElement)) {
          _focused = true;
          document.body.classList.add("terminal-focused");
        } else {
          _focused = false;
          document.body.classList.remove("terminal-focused");
        }
      }, 0);
    });
    host.addEventListener(
      "mousedown",
      function () {
        _focused = true;
        document.body.classList.add("terminal-focused");
      },
      true
    );
  }

  function stopGlobalShortcuts(ev) {
    if (!isFocused()) return;
    if (ev.type !== "keydown") return;

    var key = ev.key || "";
    var lower = key.length === 1 ? key.toLowerCase() : key;
    var ctrl = !!ev.ctrlKey;
    var meta = !!ev.metaKey;
    var shift = !!ev.shiftKey;
    var alt = !!ev.altKey;

    // Do NOT stopPropagation in the capture phase — that would block xterm's
    // textarea from receiving the event. Peon UI already skips when focused.
    // Only cancel browser defaults for chords we can override.

    // Peon palette: Shift+S / Ctrl|Cmd+K
    if (shift && !ctrl && !meta && !alt && (lower === "s" || key === "S")) {
      ev.preventDefault();
      return;
    }
    if ((ctrl || meta) && !alt && (lower === "k" || lower === "p")) {
      ev.preventDefault();
      return;
    }

    // Browser chords that steal from the shell (best-effort).
    // Ctrl+W / Ctrl+T / Ctrl+N are often reserved by the browser and cannot
    // be cancelled from a web page.
    if ((ctrl || meta) && !alt) {
      // refresh, find, location, history, downloads, bookmarks, print, save, …
      if ("rflhogjsu".indexOf(lower) >= 0) {
        ev.preventDefault();
        return;
      }
      // Ctrl+Shift+I/J/C (devtools) — best effort; may be ignored
      if (shift && "ijc".indexOf(lower) >= 0) {
        ev.preventDefault();
        return;
      }
      // Zoom
      if (key === "-" || key === "=" || key === "+" || lower === "0") {
        ev.preventDefault();
        return;
      }
    }
  }

  function ensureGlobalCapture() {
    if (window.__peonTerminalKeysBound) return;
    window.__peonTerminalKeysBound = true;
    document.addEventListener("keydown", stopGlobalShortcuts, true);
  }

  function disposeStream() {
    if (_es) {
      try {
        _es.close();
      } catch (_) {}
      _es = null;
    }
  }

  function fit() {
    if (!_term || !_host) return;
    // Skip while the host is hidden (collapsed card / inactive tab) — xterm
    // would compute 0×0 cols/rows and break the PTY.
    if (!_host.offsetParent && _host.getClientRects().length === 0) return;
    if (_host.clientWidth < 8 || _host.clientHeight < 8) return;
    if (_fit) {
      try {
        _fit.fit();
      } catch (_) {}
    }
    if (_session && _cfg && _term.cols > 1 && _term.rows > 1) {
      postJson(_cfg.resizeUrl(_session.id), {
        cols: _term.cols,
        rows: _term.rows,
      }).catch(function () {});
    }
  }

  function revealShellCard() {
    var card = document.getElementById("console-workspace");
    if (card && card.classList.contains("is-collapsed")) {
      var foldBtn = card.querySelector(".panel-collapse-btn");
      if (foldBtn) {
        foldBtn.click();
      } else {
        card.classList.remove("is-collapsed");
        try {
          localStorage.setItem("peon.cardFold3.console-workspace", "0");
        } catch (_) {}
        window.dispatchEvent(
          new CustomEvent("peon:card-unfold", { detail: { panel: card } })
        );
      }
    }
    if (window.PeonProjectOps && window.PeonProjectOps.showWorkspaceTab) {
      window.PeonProjectOps.showWorkspaceTab("terminal");
    }
  }

  function setStatus(text, kind) {
    var el = document.getElementById("terminal-status");
    if (!el) return;
    el.textContent = text || "";
    el.dataset.kind = kind || "";
  }

  function scheduleReconnect(msg) {
    disposeStream();
    _session = null;
    if (_userClosed || _retryTimer) return;
    if (_retries >= 5) {
      setStatus("Disconnected — click Open shell to reconnect", "err");
      return;
    }
    var delay = Math.min(8000, 500 * Math.pow(2, _retries));
    _retries += 1;
    setStatus(msg || "Reconnecting…", "warn");
    _retryTimer = setTimeout(function () {
      _retryTimer = null;
      openSession();
    }, delay);
  }

  function connectStream(sessionId) {
    disposeStream();
    var url = _cfg.streamUrl(sessionId);
    var es = new EventSource(url);
    _es = es;
    es.addEventListener("ready", function () {
      // Server replays scrollback after "ready"; start from a clean screen
      // so a reconnect does not print everything twice.
      _retries = 0;
      if (_term) _term.reset();
      setTimeout(fit, 20);
    });
    es.addEventListener("out", function (ev) {
      if (!_term || !ev.data) return;
      try {
        _term.write(b64ToUint8(ev.data));
      } catch (_) {}
    });
    es.addEventListener("exit", function () {
      setStatus("Session ended", "warn");
      if (_term) _term.writeln("\r\n\x1b[33m[session closed]\x1b[0m");
      _session = null;
      _focused = false;
      document.body.classList.remove("terminal-focused");
      disposeStream();
    });
    es.addEventListener("fail", function (ev) {
      scheduleReconnect("Stream failed: " + (ev.data || "") + " — reconnecting…");
    });
    es.addEventListener("error", function () {
      if (es !== _es) return;
      // CONNECTING: the browser retries on its own (network blip, proxy idle).
      if (es.readyState === 0) {
        setStatus("Connection interrupted — retrying…", "warn");
        return;
      }
      // CLOSED: non-200 (e.g. 404 after a dev-server reload dropped the
      // in-memory PTY). Reopen; the server reuses or starts a shell.
      scheduleReconnect("Session lost — reconnecting…");
    });
  }

  function openSession() {
    if (!_cfg || !_term) return Promise.resolve();
    _userClosed = false;
    revealShellCard();
    setStatus("Starting sandbox shell…", "");
    return new Promise(function (resolve) {
      // Wait for unfold layout before measuring / opening the PTY.
      setTimeout(function () {
        fit();
        resolve(
          postJson(_cfg.openUrl, {
            cols: Math.max(40, _term.cols || 80),
            rows: Math.max(12, _term.rows || 24),
          })
            .then(function (res) {
              if (!res.ok || !res.data || !res.data.ok) {
                var err =
                  (res.data && res.data.error) || "Failed to open terminal";
                _session = null;
                setStatus(err, "err");
                if (_term) _term.writeln("\r\n\x1b[31m" + err + "\x1b[0m");
                if (window.PeonUI) window.PeonUI.toast(err, "error");
                return;
              }
              _session = {
                id: res.data.session_id,
                container: res.data.container,
              };
              var label = res.data.container || "sandbox";
              setStatus(
                (res.data.reused ? "Reconnected · " : "Connected · ") + label,
                "ok"
              );
              connectStream(_session.id);
              setTimeout(fit, 40);
              flushInput();
              _term.focus();
              _focused = true;
              document.body.classList.add("terminal-focused");
            })
            .catch(function (err) {
              var msg =
                (err && err.message) || "Network error opening terminal";
              _session = null;
              setStatus(msg, "err");
              if (_term) _term.writeln("\r\n\x1b[31m" + msg + "\x1b[0m");
              if (window.PeonUI) window.PeonUI.toast(msg, "error");
            })
        );
      }, 60);
    });
  }

  function closeSession() {
    _userClosed = true;
    _queue = [];
    if (_retryTimer) {
      clearTimeout(_retryTimer);
      _retryTimer = null;
    }
    disposeStream();
    var sid = _session && _session.id;
    _session = null;
    _focused = false;
    document.body.classList.remove("terminal-focused");
    if (sid && _cfg) {
      postJson(_cfg.closeUrl(sid), {}).catch(function () {});
    }
    setStatus("Disconnected", "");
  }

  function mount(cfg) {
    _cfg = cfg;
    _host = document.getElementById(cfg.hostId || "terminal-host");
    if (!_host) return;
    if (typeof window.Terminal !== "function") {
      setStatus("xterm failed to load", "err");
      return;
    }
    ensureGlobalCapture();
    bindFocusGuards(_host);

    _term = new window.Terminal({
      cursorBlink: true,
      fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
      fontSize: 13,
      theme: terminalTheme(),
      allowProposedApi: true,
      macOptionIsMeta: true,
    });
    if (window.FitAddon) {
      var FitCtor = window.FitAddon.FitAddon || window.FitAddon;
      if (typeof FitCtor === "function") {
        _fit = new FitCtor();
        _term.loadAddon(_fit);
      }
    }
    _term.open(_host);
    attachKeyHandler(_term);
    _term.onData(function (data) {
      sendData(data);
    });
    window.addEventListener("peon:themechange", applyTerminalTheme);
    var ro = new ResizeObserver(function () {
      fit();
    });
    ro.observe(_host);
    window.addEventListener("peon:panel-expand", function () {
      setTimeout(fit, 50);
    });
    window.addEventListener("peon:card-unfold", function (ev) {
      var panel = ev.detail && ev.detail.panel;
      if (panel && panel.id === "console-workspace") setTimeout(fit, 40);
    });
    window.addEventListener("peon:workspace-tab", function (ev) {
      if (ev.detail && ev.detail.tab === "terminal") setTimeout(fit, 40);
    });

    var openBtn = document.getElementById("terminal-open");
    var closeBtn = document.getElementById("terminal-close");
    if (openBtn) {
      openBtn.addEventListener("click", function () {
        if (_session && _es && _es.readyState === 1) {
          revealShellCard();
          setTimeout(function () {
            fit();
            if (_term) _term.focus();
            _focused = true;
            document.body.classList.add("terminal-focused");
          }, 40);
          return;
        }
        // Stale session id (dead SSE / server reload) → reopen (reuses if alive).
        _session = null;
        _retries = 0;
        if (_retryTimer) {
          clearTimeout(_retryTimer);
          _retryTimer = null;
        }
        disposeStream();
        openSession();
      });
    }
    if (closeBtn) {
      closeBtn.addEventListener("click", function () {
        closeSession();
        if (_term) {
          _term.clear();
          _term.writeln("\x1b[90mDisconnected. Click Open shell to reconnect.\x1b[0m");
        }
      });
    }

    setStatus("Ready — Open shell to attach", "");
    _term.writeln(
      "\x1b[90mSandbox terminal — Ctrl+C interrupt · Ctrl+Shift+C/V copy/paste · Ctrl+R history\x1b[0m"
    );
    setTimeout(fit, 80);
    // Auto-connect only once the terminal host has a real layout (collapsed
    // card / hidden tab yields 0×0 and flaky docker exec PTYs).
    setTimeout(function () {
      if (_session) return;
      if (!_host || _host.clientWidth < 40 || _host.clientHeight < 40) return;
      openSession();
    }, 400);
  }

  global.PeonTerminal = {
    mount: mount,
    isFocused: isFocused,
    fit: fit,
  };
})(window);
