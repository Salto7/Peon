/* Collapse / expand / resize war-room cards — Context & activity, Chat, Sandbox shell, Agents.
 *
 * Collapsed cards shrink to their header (chat to a narrow rail); neighbors grow into the freed
 * space (see console.css). Drag the edge handles to change size; content fills via flex + CSS vars.
 * Choices are remembered per browser.
 */
(function () {
  var STORAGE = "peon-console-panels";
  var SIZE_STORAGE = "peon-console-sizes";
  var PANELS = ["ops", "chat", "shell"];
  var LABELS = { ops: "Context & activity", chat: "Chat", shell: "Sandbox shell" };

  // Defaults match the previous Tailwind fixed sizes (rem → px at 16).
  var SIZE_DEFAULTS = {
    agentsH: 416, // 26rem
    opsH: 416, // 26rem
    chatH: 848, // 53rem
    chatW: 416, // 26rem
    shellH: 352, // 22rem
  };
  var SIZE_MIN = {
    agentsH: 160,
    opsH: 160,
    chatH: 280,
    chatW: 240,
    shellH: 120,
  };
  var SIZE_MAX = {
    agentsH: 1200,
    opsH: 1200,
    chatH: 1600,
    chatW: 720,
    shellH: 900,
  };

  function layout() {
    return document.getElementById("war-layout");
  }

  function shellCard() {
    return document.getElementById("console-workspace");
  }

  function load() {
    try {
      var raw = JSON.parse(localStorage.getItem(STORAGE) || "{}");
      return raw && typeof raw === "object" ? raw : {};
    } catch (_) {
      return {};
    }
  }

  function save(state) {
    try {
      localStorage.setItem(STORAGE, JSON.stringify(state));
    } catch (_) {
      /* private mode */
    }
  }

  function loadSizes() {
    try {
      var raw = JSON.parse(localStorage.getItem(SIZE_STORAGE) || "{}");
      if (!raw || typeof raw !== "object") return Object.assign({}, SIZE_DEFAULTS);
      var out = Object.assign({}, SIZE_DEFAULTS);
      Object.keys(SIZE_DEFAULTS).forEach(function (k) {
        if (typeof raw[k] === "number" && isFinite(raw[k])) {
          out[k] = clamp(k, Math.round(raw[k]));
        }
      });
      return out;
    } catch (_) {
      return Object.assign({}, SIZE_DEFAULTS);
    }
  }

  function saveSizes(sizes) {
    try {
      localStorage.setItem(SIZE_STORAGE, JSON.stringify(sizes));
    } catch (_) {}
  }

  function clamp(key, value) {
    return Math.min(SIZE_MAX[key], Math.max(SIZE_MIN[key], value));
  }

  var sizes = loadSizes();

  function applySizes() {
    var root = layout();
    if (!root) return;
    root.style.setProperty("--war-agents-h", sizes.agentsH + "px");
    root.style.setProperty("--war-ops-h", sizes.opsH + "px");
    root.style.setProperty("--war-chat-h", sizes.chatH + "px");
    root.style.setProperty("--war-chat-w", sizes.chatW + "px");
    root.style.setProperty("--war-shell-h", sizes.shellH + "px");
    window.dispatchEvent(new Event("resize"));
  }

  function isExpanded(name) {
    var root = layout();
    return !!root && !root.hasAttribute("data-collapse-" + name);
  }

  function apply(name, expanded, persist) {
    var root = layout();
    if (!root || PANELS.indexOf(name) < 0) return;
    if (expanded) root.removeAttribute("data-collapse-" + name);
    else root.setAttribute("data-collapse-" + name, "");

    if (name === "shell") {
      var card = shellCard();
      if (card) {
        if (expanded) card.classList.remove("is-collapsed");
        else card.classList.add("is-collapsed");
        try {
          localStorage.setItem("peon.cardFold3.console-workspace", expanded ? "0" : "1");
        } catch (_) {}
        if (expanded) {
          window.dispatchEvent(
            new CustomEvent("peon:card-unfold", { detail: { panel: card } })
          );
        }
      }
    }

    document.querySelectorAll('[data-panel-toggle="' + name + '"]').forEach(function (btn) {
      var label = (expanded ? "Collapse " : "Expand ") + LABELS[name];
      btn.setAttribute("aria-expanded", expanded ? "true" : "false");
      btn.title = label;
      btn.setAttribute("aria-label", label);
    });
    if (persist !== false) {
      var state = load();
      state[name] = expanded;
      save(state);
    }
    window.dispatchEvent(new Event("resize"));
  }

  function show(name) {
    if (!isExpanded(name)) apply(name, true);
  }

  function bindResizers() {
    var root = layout();
    if (!root) return;
    root.querySelectorAll("[data-war-resize]").forEach(function (handle) {
      if (handle.dataset.boundResize) return;
      handle.dataset.boundResize = "1";
      handle.addEventListener("pointerdown", function (ev) {
        if (ev.button != null && ev.button !== 0) return;
        var key = handle.getAttribute("data-war-resize");
        var axis = handle.getAttribute("data-axis") || "y";
        if (!key || !(key in sizes)) return;
        // Don't resize when the owning card is collapsed.
        var panel = handle.getAttribute("data-panel");
        if (panel && !isExpanded(panel)) return;
        ev.preventDefault();
        var start = axis === "x" ? ev.clientX : ev.clientY;
        var origin = sizes[key];
        var invert = handle.getAttribute("data-invert") === "1";
        handle.setPointerCapture(ev.pointerId);
        document.body.style.cursor = axis === "x" ? "ew-resize" : "ns-resize";
        document.body.style.userSelect = "none";

        function onMove(e) {
          var cur = axis === "x" ? e.clientX : e.clientY;
          var delta = cur - start;
          if (invert) delta = -delta;
          sizes[key] = clamp(key, Math.round(origin + delta));
          applySizes();
        }
        function onUp() {
          handle.removeEventListener("pointermove", onMove);
          handle.removeEventListener("pointerup", onUp);
          handle.removeEventListener("pointercancel", onUp);
          document.body.style.cursor = "";
          document.body.style.userSelect = "";
          saveSizes(sizes);
          applySizes();
        }
        handle.addEventListener("pointermove", onMove);
        handle.addEventListener("pointerup", onUp);
        handle.addEventListener("pointercancel", onUp);
      });
    });
  }

  function init() {
    if (!layout()) return;
    var state = load();
    if (state.shell === undefined) {
      try {
        if (localStorage.getItem("peon.cardFold3.console-workspace") === "1") state.shell = false;
      } catch (_) {}
    }
    PANELS.forEach(function (name) {
      apply(name, state[name] !== false, false);
    });
    applySizes();
    bindResizers();
    document.querySelectorAll("[data-panel-toggle]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var name = btn.getAttribute("data-panel-toggle");
        apply(name, !isExpanded(name));
      });
    });
  }

  window.PeonConsolePanels = {
    show: show,
    isExpanded: isExpanded,
    set: apply,
    sizes: function () {
      return Object.assign({}, sizes);
    },
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
