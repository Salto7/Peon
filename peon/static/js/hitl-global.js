/* Global HITL popup: pending inputs across all projects. */
(function () {
  function el(id) {
    return document.getElementById(id);
  }

  function init(opts) {
    var dialog = el("hitl-global-dialog");
    var listUrl = (opts && opts.listUrl) || "/pending-inputs.json";
    var decideUrl = (opts && opts.decideUrl) || "/pending-inputs/decide/";
    if (!dialog || dialog.dataset.bound) return;
    dialog.dataset.bound = "1";

    var questionEl = el("hitl-global-question");
    var projectEl = el("hitl-global-project");
    var replyEl = el("hitl-global-reply");
    var badge = el("hitl-global-badge");
    var current = null;
    var KNOWN_KEY = "peon-hitl-dismissed";
    var known = {};
    try {
      known = JSON.parse(sessionStorage.getItem(KNOWN_KEY) || "{}") || {};
    } catch (_) {
      known = {};
    }
    var ui = window.PeonUI || {};

    function remember(key) {
      if (!key) return;
      known[key] = 1;
      try {
        sessionStorage.setItem(KNOWN_KEY, JSON.stringify(known));
      } catch (_) {}
    }

    function show(item) {
      current = item;
      if (questionEl) {
        var kind = item.kind ? "[" + item.kind + "] " : "";
        questionEl.textContent = kind + (item.question || "");
      }
      if (projectEl) {
        projectEl.textContent = item.project_title || item.project_id || "";
        projectEl.href = item.href || "#";
      }
      if (replyEl) replyEl.value = "";
      dialog.classList.remove("is-closing");
      dialog.classList.add("is-open");
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }

    function hide() {
      if (!dialog.open && !dialog.hasAttribute("open")) {
        current = null;
        return;
      }
      dialog.classList.add("is-closing");
      window.setTimeout(function () {
        dialog.classList.remove("is-open", "is-closing");
        if (dialog.open) dialog.close();
        else dialog.removeAttribute("open");
        current = null;
      }, 220);
    }

    function decide(decision) {
      if (!current || String(current.id || "").indexOf("obj-") === 0) {
        if (current && current.href) window.location.href = current.href;
        return;
      }
      var post = ui.postJson || null;
      if (!post) return;
      post(decideUrl, {
        id: current.id,
        decision: decision,
        reply: (replyEl && replyEl.value) || "",
      })
        .then(function () {
          hide();
          poll();
        })
        .catch(function () {});
    }

    function updateBadge(count) {
      if (!badge) return;
      if (count > 0) {
        badge.hidden = false;
        badge.textContent = String(count);
      } else {
        badge.hidden = true;
      }
    }

    function poll() {
      var get = ui.getJson || null;
      if (!get) return;
      get(listUrl)
        .then(function (pack) {
          if (!pack || !pack.ok || !pack.data) return;
          var pending = pack.data.pending || [];
          updateBadge(pending.length);
          if (!pending.length) return;
          var first = pending[0];
          var key = String(first.id || "");
          if (known[key]) return;
          if (dialog.open) return;
          show(first);
        })
        .catch(function () {});
    }

    var approve = el("hitl-global-approve");
    var deny = el("hitl-global-deny");
    var edit = el("hitl-global-edit");
    var openBtn = el("hitl-global-open");
    var dismiss = el("hitl-global-dismiss");
    if (approve) approve.addEventListener("click", function () {
      decide("approve");
    });
    if (deny) deny.addEventListener("click", function () {
      decide("deny");
    });
    if (edit)
      edit.addEventListener("click", function () {
        if (!replyEl || !replyEl.value.trim()) {
          if (replyEl) replyEl.focus();
          return;
        }
        decide("edit");
      });
    if (openBtn)
      openBtn.addEventListener("click", function () {
        if (current && current.href) window.location.href = current.href;
      });
    if (dismiss)
      dismiss.addEventListener("click", function () {
        if (current) remember(String(current.id || ""));
        hide();
      });

    setInterval(poll, 5000);
    poll();
  }

  window.PeonHitlGlobal = { init: init };
})();
