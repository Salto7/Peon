/* Shared multi-select + bulk bar helpers. */
(function () {
  function selected(root) {
    return Array.prototype.slice.call(
      (root || document).querySelectorAll("[data-bulk-row]:checked")
    );
  }

  function sync(form) {
    if (!form) return;
    const rows = form.closest(".panel, main, body") || document;
    const boxes = rows.querySelectorAll('[form="' + form.id + '"][data-bulk-row]');
    const n = selected(rows).filter(function (el) {
      return el.getAttribute("form") === form.id;
    }).length;
    const count = form.querySelector("[data-bulk-count]");
    if (count) count.textContent = n + " selected";
    form.querySelectorAll("[data-bulk-btn]").forEach(function (btn) {
      btn.disabled = n === 0;
    });
    const all = rows.querySelector('input[data-select-all]');
    if (all && boxes.length) {
      all.checked = n > 0 && n === boxes.length;
      all.indeterminate = n > 0 && n < boxes.length;
    }
  }

  window.PeonBulk = {
    init: function (form) {
      if (!form) return;
      const scope = form.closest(".panel, main, body") || document;
      scope.addEventListener("change", function (ev) {
        const t = ev.target;
        if (!t) return;
        if (t.matches("[data-select-all]")) {
          scope
            .querySelectorAll('[form="' + form.id + '"][data-bulk-row]')
            .forEach(function (box) {
              box.checked = t.checked;
            });
        }
        if (t.matches("[data-bulk-row], [data-select-all]")) sync(form);
      });
      form.addEventListener("submit", function (ev) {
        const submitter = ev.submitter;
        const confirmMsg = submitter && submitter.getAttribute("data-confirm");
        if (confirmMsg && !window.confirm(confirmMsg)) {
          ev.preventDefault();
          return;
        }
        // Disabled submit buttons are omitted from POST — stash action first.
        if (submitter && submitter.name === "action") {
          var hidden = form.querySelector('input[type="hidden"][name="action"][data-bulk-action]');
          if (!hidden) {
            hidden = document.createElement("input");
            hidden.type = "hidden";
            hidden.name = "action";
            hidden.setAttribute("data-bulk-action", "1");
            form.appendChild(hidden);
          }
          hidden.value = submitter.value || "";
        }
        form.querySelectorAll("[data-bulk-btn]").forEach(function (btn) {
          btn.disabled = true;
        });
      });
      sync(form);
    },
    sync: sync,
    selectedIds: function (form) {
      if (!form) return [];
      const scope = form.closest(".panel, main, body") || document;
      return selected(scope)
        .filter(function (el) {
          return el.getAttribute("form") === form.id;
        })
        .map(function (el) {
          return el.value;
        });
    },
  };
})();
