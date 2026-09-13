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
    this.openBtn = document.getElementById("palette-open");
    this.items = [];
    this.filtered = [];
    this.active = 0;
    this._bound = false;
  }

  Palette.prototype.bind = function () {
    if (!this.dialog || this._bound) return;
    this._bound = true;
    const self = this;
    if (this.openBtn) {
      this.openBtn.addEventListener("click", function () {
        self.open();
      });
    }
    document.addEventListener("keydown", function (ev) {
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

    var pageHead = panel.querySelector(":scope > .page-header");
    if (pageHead) {
      pageHead.classList.add("panel-head-row");
      pageHead.appendChild(btn);
      return;
    }
    var h2 = panel.querySelector(":scope > h2");
    if (h2) {
      var wrap = document.createElement("div");
      wrap.className = "panel-head";
      h2.parentNode.insertBefore(wrap, h2);
      wrap.appendChild(h2);
      wrap.appendChild(btn);
      return;
    }
    panel.insertBefore(btn, panel.firstChild);
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

  window.PeonUI = {
    init: function (opts) {
      promoteFlashMessages();
      requestNotifyPermission();
      initPanelExpand();
      const palette = new Palette(opts || {});
      palette.bind();
      return { toast: showToast, palette: palette };
    },
    toast: showToast,
    statusPill: statusPill,
    formatIsoTime: formatIsoTime,
  };
})();
