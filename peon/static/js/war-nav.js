/* War-room left nav: pane switch + collapsible icon rail. */
(function () {
  var STORAGE = "peon-war-side-collapsed";
  var VALID = {
    dashboard: true,
    assets: true,
    results: true,
    attack: true,
    reports: true,
    settings: true,
  };

  function normalize(hash) {
    var h = String(hash || "")
      .replace(/^#/, "")
      .split(/[/?]/)[0]
      .toLowerCase();
    if (h === "roe" || h === "candidates") return "assets";
    if (h === "files" || h === "deliverables") return "reports";
    if (
      h === "findings" ||
      h === "objectives" ||
      h === "objectives-panel" ||
      h === "results-card"
    )
      return "results";
    if (VALID[h]) return h;
    return "dashboard";
  }

  function setCollapsed(layout, collapsed) {
    layout.dataset.collapsed = collapsed ? "1" : "0";
    layout.classList.toggle("is-side-collapsed", !!collapsed);
    var btn = document.getElementById("war-side-toggle");
    if (btn) {
      btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
      btn.setAttribute(
        "aria-label",
        collapsed ? "Expand sidebar" : "Collapse sidebar"
      );
      btn.title = collapsed ? "Expand sidebar" : "Collapse sidebar";
    }
    try {
      localStorage.setItem(STORAGE, collapsed ? "1" : "0");
    } catch (_) {}
  }

  function showPane(name) {
    var pane = normalize(name);
    document.querySelectorAll("[data-war-pane]").forEach(function (el) {
      var on = el.getAttribute("data-war-pane") === pane;
      el.hidden = !on;
      el.classList.toggle("is-active", on);
    });
    document.querySelectorAll("[data-war-nav]").forEach(function (el) {
      var on = el.getAttribute("data-war-nav") === pane;
      el.classList.toggle("is-active", on);
      el.setAttribute("aria-current", on ? "page" : "false");
    });
    try {
      if (location.hash.replace(/^#/, "") !== pane) {
        history.replaceState(null, "", "#" + pane);
      }
    } catch (_) {}
    window.dispatchEvent(
      new CustomEvent("peon:war-pane", { detail: { pane: pane } })
    );
    if (pane === "attack" && window.PeonAttackSurface) {
      window.PeonAttackSurface.fit();
    }
    if (pane === "dashboard" && window.PeonTerminal && window.PeonTerminal.fit) {
      setTimeout(function () {
        window.PeonTerminal.fit();
      }, 40);
    }
  }

  function init() {
    var layout = document.getElementById("war-layout");
    if (!layout) return;

    var collapsed = false;
    try {
      collapsed = localStorage.getItem(STORAGE) === "1";
    } catch (_) {}
    setCollapsed(layout, collapsed);

    var toggle = document.getElementById("war-side-toggle");
    if (toggle) {
      toggle.addEventListener("click", function () {
        setCollapsed(layout, !layout.classList.contains("is-side-collapsed"));
      });
    }

    document.querySelectorAll("[data-war-nav]").forEach(function (el) {
      el.addEventListener("click", function () {
        showPane(el.getAttribute("data-war-nav") || "dashboard");
      });
    });

    window.addEventListener("hashchange", function () {
      showPane(location.hash);
    });

    showPane(location.hash || "dashboard");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.PeonWarNav = { show: showPane };
})();
