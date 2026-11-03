/* Catalog page: one context card, type tabs, a single Add item control. */
(function () {
  var STORAGE = "peon-catalog-type";
  var TYPES = ["all", "roles", "skills", "mcp", "tools", "marketplace"];
  var TYPE_TO_KIND = { roles: "role", skills: "skill", mcp: "mcp", tools: "tool", marketplace: "marketplace" };
  var DIALOG_KINDS = { skill: true, mcp: true, tool: true, marketplace: true };

  function root() {
    return document.getElementById("catalog-shell");
  }

  function currentType() {
    var r = root();
    return (r && r.getAttribute("data-catalog-type")) || "all";
  }

  function setType(type) {
    if (TYPES.indexOf(type) < 0) type = "all";
    var r = root();
    if (!r) return;
    r.setAttribute("data-catalog-type", type);
    try {
      localStorage.setItem(STORAGE, type);
    } catch (_) {}

    r.querySelectorAll("[data-catalog-tab]").forEach(function (btn) {
      var on = btn.getAttribute("data-catalog-tab") === type;
      btn.setAttribute("aria-selected", on ? "true" : "false");
      btn.classList.toggle("is-active", on);
    });

    r.querySelectorAll("[data-catalog-pane]").forEach(function (pane) {
      var paneType = pane.getAttribute("data-catalog-pane");
      pane.hidden = !(type === "all" || type === paneType);
    });

    var addKind = document.getElementById("catalog-add-kind");
    if (addKind) {
      addKind.hidden = type !== "all";
      if (TYPE_TO_KIND[type] && TYPE_TO_KIND[type] !== "marketplace") {
        // Keep select on an addable catalog kind when switching tabs.
        var mapped = TYPE_TO_KIND[type];
        if (mapped === "role" || mapped === "skill" || mapped === "mcp" || mapped === "tool") {
          addKind.value = mapped;
        }
      }
    }
  }

  function addKindValue() {
    var type = currentType();
    if (TYPE_TO_KIND[type]) return TYPE_TO_KIND[type];
    var sel = document.getElementById("catalog-add-kind");
    return (sel && sel.value) || "role";
  }

  function openAdd(kind) {
    if (kind === "role") {
      var fake = document.createElement("button");
      fake.type = "button";
      fake.setAttribute("data-catalog-edit", "role");
      fake.setAttribute("data-id", "__new__");
      fake.hidden = true;
      document.body.appendChild(fake);
      fake.click();
      fake.remove();
      return;
    }
    if (DIALOG_KINDS[kind]) {
      window.dispatchEvent(new CustomEvent("peon:catalog-add", { detail: { kind: kind } }));
    }
  }

  function init() {
    var r = root();
    if (!r || r.dataset.boundCatalogShell) return;
    r.dataset.boundCatalogShell = "1";

    var initial = "all";
    try {
      initial = localStorage.getItem(STORAGE) || "all";
    } catch (_) {}
    if (initial === "policies") initial = "all";
    if (location.hash) {
      var h = location.hash.replace(/^#/, "").split("-")[0];
      if (TYPES.indexOf(h) >= 0) initial = h;
    }
    setType(initial);

    r.addEventListener("click", function (ev) {
      var tab = ev.target.closest("[data-catalog-tab]");
      if (tab && r.contains(tab)) {
        ev.preventDefault();
        var t = tab.getAttribute("data-catalog-tab");
        setType(t);
        if (t && t !== "all") history.replaceState(null, "", "#" + t);
        else history.replaceState(null, "", location.pathname + location.search);
        return;
      }
      if (ev.target.closest("#catalog-add-btn")) {
        ev.preventDefault();
        openAdd(addKindValue());
      }
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
