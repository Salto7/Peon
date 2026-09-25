/* Shared UI: toasts + command palette. */
(function () {
  function toastRoot() {
    return document.getElementById("toast-root");
  }

  function showToast(text, kind) {
    const root = toastRoot();
    if (!root || !text) return;
    const el = document.createElement("div");
    el.className = "toast" + (kind ? " toast-" + kind : "");
    el.textContent = String(text);
    root.appendChild(el);
    window.setTimeout(function () {
      el.classList.add("toast-out");
      window.setTimeout(function () {
        el.remove();
      }, 280);
    }, 4200);
    if (
      kind === "error" &&
      typeof Notification !== "undefined" &&
      Notification.permission === "granted" &&
      document.hidden
    ) {
      try {
        new Notification("Peon", { body: String(text) });
      } catch (_) {}
    }
  }

  function promoteFlashMessages() {
    document.querySelectorAll("[data-toast-source]").forEach(function (node) {
      const text = (node.textContent || "").trim();
      if (!text) return;
      let kind = "info";
      if (node.classList.contains("error") || node.classList.contains("err")) kind = "error";
      else if (node.classList.contains("success") || node.classList.contains("ok"))
        kind = "ok";
      else if (node.classList.contains("warning")) kind = "warn";
      showToast(text, kind);
      // Keep flash banners visible — toasts alone are easy to miss.
    });
  }

  function requestNotifyPermission() {
    if (typeof Notification === "undefined") return;
    if (Notification.permission === "default") {
      Notification.requestPermission().catch(function () {});
    }
  }

  function Palette(opts) {
    this.url = opts.searchUrl || opts.paletteUrl || "/search.json";
    this.dialog = document.getElementById("palette-dialog");
    this.input = document.getElementById("palette-input");
    this.results = document.getElementById("palette-results");
    this.items = [];
    this.filtered = [];
    this.active = 0;
    this._bound = false;
  }

  Palette.prototype.bind = function () {
    if (!this.dialog || this._bound) return;
    this._bound = true;
    const self = this;
    const openers = document.querySelectorAll("[data-palette-open], #palette-open");
    openers.forEach(function (btn) {
      btn.addEventListener("click", function () {
        self.open();
      });
    });
    document.addEventListener("keydown", function (ev) {
      if (window.PeonTerminal && window.PeonTerminal.isFocused && window.PeonTerminal.isFocused()) {
        return;
      }
      const tag = (ev.target && ev.target.tagName) || "";
      const typing =
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        tag === "SELECT" ||
        (ev.target && ev.target.isContentEditable);
      // Shift+S opens search (ignore when typing in a field, unless palette open).
      if (
        (ev.key === "S" || ev.key === "s") &&
        ev.shiftKey &&
        !ev.metaKey &&
        !ev.ctrlKey &&
        !ev.altKey
      ) {
        if (!typing || (self.dialog && self.dialog.open)) {
          ev.preventDefault();
          self.open();
          return;
        }
      }
      // Keep Ctrl/⌘K as a secondary shortcut.
      const meta = ev.metaKey || ev.ctrlKey;
      if (meta && (ev.key === "k" || ev.key === "K")) {
        ev.preventDefault();
        self.open();
        return;
      }
      if (!self.dialog.open) return;
      if (ev.key === "Escape") {
        self.dialog.close();
      } else if (ev.key === "ArrowDown") {
        ev.preventDefault();
        self.move(1);
      } else if (ev.key === "ArrowUp") {
        ev.preventDefault();
        self.move(-1);
      } else if (ev.key === "Enter") {
        ev.preventDefault();
        self.activate();
      }
    });
    if (this.input) {
      this.input.addEventListener("input", function () {
        const q = self.input.value || "";
        if (self._debounce) window.clearTimeout(self._debounce);
        self._debounce = window.setTimeout(function () {
          self.load(q).then(function () {
            self.render(q);
          });
        }, 160);
      });
    }
  };

  Palette.prototype.open = function () {
    const self = this;
    this.load("").then(function () {
      self.dialog.showModal();
      self.input.value = "";
      self.render("");
      self.input.focus();
    });
  };

  Palette.prototype.load = function (query) {
    const self = this;
    const q = String(query || "").trim();
    const url =
      this.url + (q ? (this.url.indexOf("?") >= 0 ? "&" : "?") + "q=" + encodeURIComponent(q) : "");
    return fetch(url, { headers: { Accept: "application/json" } })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        self.items = data.items || [];
        const extras = document.querySelectorAll("[data-palette-extra]");
        extras.forEach(function (node) {
          self.items.push({
            id: node.getAttribute("data-palette-id") || node.href,
            label: node.getAttribute("data-palette-label") || node.textContent,
            group: node.getAttribute("data-palette-group") || "Page",
            href: node.getAttribute("href") || node.dataset.href,
            keywords: node.getAttribute("data-palette-keywords") || "",
          });
        });
      })
      .catch(function () {
        self.items = [];
      });
  };

  Palette.prototype.render = function (query) {
    const q = String(query || "")
      .trim()
      .toLowerCase();
    this.filtered = this.items.filter(function (it) {
      if (!q) return true;
      const hay = (
        (it.label || "") +
        " " +
        (it.group || "") +
        " " +
        (it.keywords || "") +
        " " +
        (it.meta || "")
      ).toLowerCase();
      return hay.indexOf(q) >= 0;
    });
    this.active = 0;
    this.results.replaceChildren();
    const self = this;
    this.filtered.slice(0, 40).forEach(function (it, idx) {
      const li = document.createElement("li");
      li.setAttribute("role", "option");
      li.dataset.href = it.href || "";
      if (idx === self.active) li.setAttribute("aria-selected", "true");
      const label = document.createElement("span");
      label.textContent = it.label || "";
      const meta = document.createElement("span");
      meta.className = "meta";
      meta.textContent = [it.group, it.meta].filter(Boolean).join(" · ");
      li.appendChild(label);
      li.appendChild(meta);
      li.addEventListener("click", function () {
        self.go(it.href);
      });
      self.results.appendChild(li);
    });
  };

  Palette.prototype.move = function (delta) {
    if (!this.filtered.length) return;
    this.active =
      (this.active + delta + this.filtered.length) % this.filtered.length;
    const opts = this.results.querySelectorAll("li");
    opts.forEach(function (li, i) {
      if (i === this.active) li.setAttribute("aria-selected", "true");
      else li.removeAttribute("aria-selected");
    }, this);
    const cur = opts[this.active];
    if (cur) cur.scrollIntoView({ block: "nearest" });
  };

  Palette.prototype.activate = function () {
    const it = this.filtered[this.active];
    if (it) this.go(it.href);
  };

  Palette.prototype.go = function (href) {
    if (!href) return;
    this.dialog.close();
    if (href.charAt(0) === "#") {
      const target = document.querySelector(href);
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    window.location.href = href;
  };

  function expandIcon(expanded) {
    // Maximize / minimize glyphs (stroke icons).
    if (expanded) {
      return (
        '<span class="panel-expand-icon" aria-hidden="true">' +
        '<svg viewBox="0 0 24 24"><path d="M8 3v3H5M16 3v3h3M8 21v-3H5M16 21v-3h3M3 8h3v3M21 8h-3v3M3 16h3v-3M21 16h-3v-3"/></svg>' +
        "</span>"
      );
    }
    return (
      '<span class="panel-expand-icon" aria-hidden="true">' +
      '<svg viewBox="0 0 24 24"><path d="M9 3H5a2 2 0 0 0-2 2v4M15 3h4a2 2 0 0 1 2 2v4M9 21H5a2 2 0 0 1-2-2v-4M15 21h4a2 2 0 0 0 2-2v-4"/></svg>' +
      "</span>"
    );
  }

  function setExpandBtnState(btn, expanded) {
    if (!btn) return;
    btn.setAttribute("aria-expanded", expanded ? "true" : "false");
    btn.title = expanded ? "Close" : "Enlarge";
    btn.setAttribute(
      "aria-label",
      expanded ? "Close enlarged panel" : "Enlarge panel"
    );
    btn.innerHTML = expandIcon(expanded);
  }

  function ensureExpandBtn(panel) {
    if (panel.querySelector(".panel-expand-btn")) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "panel-expand-btn secondary";
    setExpandBtnState(btn, false);
    appendPanelHeadControl(panel, btn);
  }

  function appendPanelHeadControl(panel, btn) {
    var wsHead = panel.querySelector(":scope > .workspace-header");
    if (wsHead) {
      var actions = wsHead.querySelector(".war-card-actions");
      if (!actions) {
        actions = document.createElement("div");
        actions.className = "war-card-actions";
        wsHead.appendChild(actions);
      }
      actions.appendChild(btn);
      return;
    }
    var pageHead = panel.querySelector(":scope > .page-header");
    if (pageHead) {
      pageHead.classList.add("panel-head-row");
      pageHead.appendChild(btn);
      return;
    }
    var existing = panel.querySelector(":scope > .panel-head, :scope > .war-card-head");
    if (existing) {
      existing.appendChild(btn);
      return;
    }
    var title = panel.querySelector(":scope > h2, :scope > h4");
    if (title) {
      var wrap = document.createElement("div");
      wrap.className = "panel-head war-card-head";
      title.parentNode.insertBefore(wrap, title);
      wrap.appendChild(title);
      wrap.appendChild(btn);
      return;
    }
    panel.insertBefore(btn, panel.firstChild);
  }

  function collapseChevron(collapsed) {
    if (collapsed) {
      return (
        '<span class="panel-collapse-icon" aria-hidden="true">' +
        '<svg viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg>' +
        "</span>"
      );
    }
    return (
      '<span class="panel-collapse-icon" aria-hidden="true">' +
      '<svg viewBox="0 0 24 24"><path d="M6 15l6-6 6 6"/></svg>' +
      "</span>"
    );
  }

  function setFoldBtnState(btn, collapsed) {
    if (!btn) return;
    btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
    btn.title = collapsed ? "Expand card" : "Collapse card";
    btn.setAttribute("aria-label", collapsed ? "Expand card" : "Collapse card");
    btn.innerHTML = collapseChevron(collapsed);
  }

  function foldStorageKey(panel) {
    return (
      "peon.cardFold3." +
      (panel.id || panel.getAttribute("aria-labelledby") || panel.className || "card")
    );
  }

  function setCardFolded(panel, collapsed) {
    if (!panel) return;
    if (collapsed && panel.classList.contains("is-expanded")) {
      collapsePanel(panel);
    }
    panel.classList.toggle("is-collapsed", !!collapsed);
    setFoldBtnState(panel.querySelector(".panel-collapse-btn"), !!collapsed);
    var title = panel.querySelector(".war-card-title-toggle");
    if (title) {
      title.setAttribute("aria-expanded", collapsed ? "false" : "true");
    }
    try {
      localStorage.setItem(foldStorageKey(panel), collapsed ? "1" : "0");
    } catch (_) {}
    window.dispatchEvent(
      new CustomEvent(collapsed ? "peon:card-fold" : "peon:card-unfold", {
        detail: { panel: panel },
      })
    );
  }

  function ensureCollapseBtn(panel) {
    if (panel.querySelector(".panel-collapse-btn")) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "panel-collapse-btn secondary";
    setFoldBtnState(btn, panel.classList.contains("is-collapsed"));
    appendPanelHeadControl(panel, btn);
  }

  function bindCardTitleToggle(panel) {
    var title =
      panel.querySelector(":scope > .workspace-header > h2") ||
      panel.querySelector(":scope > .panel-head > h2, :scope > .panel-head > h4") ||
      panel.querySelector(":scope > .war-card-head > h2, :scope > .war-card-head > h4");
    if (!title || title.dataset.boundFoldTitle) return;
    title.dataset.boundFoldTitle = "1";
    title.classList.add("war-card-title-toggle");
    title.tabIndex = 0;
    title.setAttribute("role", "button");
    title.setAttribute(
      "aria-expanded",
      panel.classList.contains("is-collapsed") ? "false" : "true"
    );
    function toggle(ev) {
      ev.preventDefault();
      setCardFolded(panel, !panel.classList.contains("is-collapsed"));
    }
    title.addEventListener("click", toggle);
    title.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " ") toggle(ev);
    });
  }

  function initWarCardCollapse() {
    if (!document.body.classList.contains("war-room")) return;
    document.querySelectorAll(".war-zone[data-collapsible]").forEach(function (panel) {
      ensureCollapseBtn(panel);
      bindCardTitleToggle(panel);
      var btn = panel.querySelector(".panel-collapse-btn");
      if (!btn || btn.dataset.boundFold) return;
      btn.dataset.boundFold = "1";
      var saved = "";
      try {
        saved = localStorage.getItem(foldStorageKey(panel)) || "";
      } catch (_) {}
      var defaultCollapsed = !panel.hasAttribute("data-collapse-open");
      if (saved === "1") setCardFolded(panel, true);
      else if (saved === "0") setCardFolded(panel, false);
      else setCardFolded(panel, defaultCollapsed);
      btn.addEventListener("click", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        setCardFolded(panel, !panel.classList.contains("is-collapsed"));
      });
    });
  }

  function collapsePanel(panel) {
    if (!panel || !panel.classList.contains("is-expanded")) return;
    panel.classList.remove("is-expanded");
    setExpandBtnState(panel.querySelector(".panel-expand-btn"), false);
    var backdrop = document.getElementById("panel-backdrop");
    if (backdrop) backdrop.remove();
    document.body.classList.remove("panel-zoomed");
    window.dispatchEvent(
      new CustomEvent("peon:panel-collapse", { detail: { panel: panel } })
    );
  }

  function expandPanel(panel) {
    document.querySelectorAll("[data-expandable].is-expanded, .panel.is-expanded").forEach(collapsePanel);
    panel.classList.add("is-expanded");
    setExpandBtnState(panel.querySelector(".panel-expand-btn"), true);
    var backdrop = document.createElement("button");
    backdrop.type = "button";
    backdrop.id = "panel-backdrop";
    backdrop.className = "panel-backdrop";
    backdrop.setAttribute("aria-label", "Close enlarged panel");
    backdrop.addEventListener("click", function () {
      collapsePanel(panel);
    });
    document.body.appendChild(backdrop);
    document.body.classList.add("panel-zoomed");
    window.dispatchEvent(
      new CustomEvent("peon:panel-expand", { detail: { panel: panel } })
    );
  }

  function initPanelExpand() {
    document.querySelectorAll("[data-expandable]").forEach(function (panel) {
      ensureExpandBtn(panel);
      var btn = panel.querySelector(".panel-expand-btn");
      if (!btn || btn.dataset.boundExpand) return;
      btn.dataset.boundExpand = "1";
      btn.addEventListener("click", function (ev) {
        ev.preventDefault();
        if (panel.classList.contains("is-expanded")) collapsePanel(panel);
        else expandPanel(panel);
      });
    });
    if (!window.__peonPanelEscBound) {
      window.__peonPanelEscBound = true;
      document.addEventListener("keydown", function (ev) {
        if (ev.key !== "Escape") return;
        if (window.PeonTerminal && window.PeonTerminal.isFocused && window.PeonTerminal.isFocused()) {
          return;
        }
        var open = document.querySelector(
          "[data-expandable].is-expanded, .panel.is-expanded"
        );
        if (open) collapsePanel(open);
      });
    }
  }

  function statusPill(status) {
    var pill = document.createElement("span");
    pill.className = "status-pill";
    pill.dataset.status = status || "";
    pill.textContent = status || "";
    return pill;
  }

  function formatIsoTime(iso, opts) {
    opts = opts || {};
    if (!iso) return opts.empty != null ? opts.empty : "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return opts.empty != null ? opts.empty : "";
    if (opts.timeOnly) {
      return d.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    }
    if (opts.compact) {
      var pad = function (n) {
        return n < 10 ? "0" + n : String(n);
      };
      return (
        d.getFullYear() +
        "-" +
        pad(d.getMonth() + 1) +
        "-" +
        pad(d.getDate()) +
        " " +
        pad(d.getHours()) +
        ":" +
        pad(d.getMinutes())
      );
    }
    return d.toLocaleString();
  }

  function csrfToken() {
    var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    var inp = document.querySelector("input[name=csrfmiddlewaretoken]");
    return inp ? inp.value : "";
  }

  var THEME_KEY = "peon-theme";

  function normalizeTheme(value) {
    return value === "light" ? "light" : "dark";
  }

  function getTheme() {
    try {
      return normalizeTheme(localStorage.getItem(THEME_KEY));
    } catch (_) {
      return "dark";
    }
  }

  function applyTheme(theme) {
    var next = normalizeTheme(theme);
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch (_) {}
    document.querySelectorAll("[data-theme-select]").forEach(function (el) {
      if (el.value !== next) el.value = next;
    });
    window.dispatchEvent(
      new CustomEvent("peon:themechange", { detail: { theme: next } })
    );
    return next;
  }

  function bindThemeControls() {
    document.querySelectorAll("[data-theme-select]").forEach(function (el) {
      if (el.dataset.themeBound === "1") return;
      el.dataset.themeBound = "1";
      el.value = getTheme();
      el.addEventListener("change", function () {
        applyTheme(el.value);
      });
    });
  }

  var APP_SIDE_KEY = "peon-app-side-collapsed";

  function initAppSide() {
    var body = document.body;
    if (!body || !body.classList.contains("app-shell")) return;
    if (body.classList.contains("war-room")) return;
    var toggle = document.getElementById("app-side-toggle");
    function setCollapsed(collapsed) {
      body.classList.toggle("is-app-side-collapsed", collapsed);
      document.documentElement.classList.toggle("app-side-collapsed", collapsed);
      try {
        localStorage.setItem(APP_SIDE_KEY, collapsed ? "1" : "0");
      } catch (_) {}
      if (toggle) {
        toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
        toggle.setAttribute(
          "aria-label",
          collapsed ? "Expand sidebar" : "Collapse sidebar"
        );
        toggle.title = collapsed ? "Expand sidebar" : "Collapse sidebar";
      }
    }
    var saved = false;
    try {
      saved = localStorage.getItem(APP_SIDE_KEY) === "1";
    } catch (_) {}
    setCollapsed(saved);
    if (toggle) {
      toggle.addEventListener("click", function () {
        setCollapsed(!body.classList.contains("is-app-side-collapsed"));
      });
    }
  }

  window.PeonUI = {
    init: function (opts) {
      applyTheme(getTheme());
      bindThemeControls();
      promoteFlashMessages();
      requestNotifyPermission();
      initAppSide();
      initWarCardCollapse();
      initPanelExpand();
      const palette = new Palette(opts || {});
      palette.bind();
      return { toast: showToast, palette: palette };
    },
    toast: showToast,
    statusPill: statusPill,
    formatIsoTime: formatIsoTime,
    csrfToken: csrfToken,
    getTheme: getTheme,
    setTheme: applyTheme,
  };
})();
