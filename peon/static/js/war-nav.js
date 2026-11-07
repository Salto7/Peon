/* War-room left nav: hash → pane switcher. */
(function () {
  // Loaded by peon/templates/projects/detail.html (scripts block). Sole war-room
  // hash→pane switcher; reads location.hash / data-war-nav, toggles data-war-pane.
  var VALID = {
    dashboard: true,
    assets: true,
    results: true,
    attack: true,
    memory: true,
    reports: true,
    keystore: true,
  };

  function mcpAvailable() {
    return !!document.querySelector('[data-war-nav="mcp"]');
  }

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
    // Legacy #settings → MCP only when that pane exists for this project.
    if (h === "settings") return mcpAvailable() ? "mcp" : "keystore";
    if (h === "mcp") return mcpAvailable() ? "mcp" : "dashboard";
    if (VALID[h]) return h;
    return "dashboard";
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
    if (!document.getElementById("war-layout")) return;

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
