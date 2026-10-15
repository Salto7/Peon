/* New-project create flow: kind choice + status dialog while planning. */
(function () {
  function setStep(list, key, state, detail) {
    if (!list) return;
    var li = list.querySelector('[data-step="' + key + '"]');
    if (!li) return;
    li.classList.remove("is-pending", "is-active", "is-done", "is-error");
    li.classList.add(
      state === "active"
        ? "is-active"
        : state === "error"
          ? "is-error"
          : state === "done"
            ? "is-done"
            : "is-pending"
    );
    li.setAttribute("data-state", state || "pending");
    var det = li.querySelector(".create-step-detail");
    if (det) det.textContent = detail || "";
  }

  function applyStages(list, stages) {
    var rows = Array.isArray(stages) ? stages : [];
    rows.forEach(function (s) {
      if (!s || !s.key) return;
      setStep(list, s.key, s.status || "done", s.detail || "");
    });
  }

  function advanceOptimistic(list, idx) {
    var order = ["create", "roe", "plan", "start"];
    order.forEach(function (key, i) {
      if (i < idx) setStep(list, key, "done", "");
      else if (i === idx) setStep(list, key, "active", "working…");
      else setStep(list, key, "pending", "");
    });
  }

  function bindKindChoice() {
    var startStep = document.querySelector(
      '#create-status-steps [data-step="start"] .create-step-body strong'
    );
    function apply() {
      var picked = document.querySelector('input[name="kind"]:checked');
      if (startStep) {
        startStep.textContent =
          picked && picked.value === "durable" ? "Review plan graph" : "Start agents";
      }
    }
    document.querySelectorAll('input[name="kind"]').forEach(function (radio) {
      radio.addEventListener("change", apply);
    });
    apply();
  }

  function openDialog(dialog) {
    dialog.classList.remove("is-closing");
    dialog.classList.add("is-open");
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }

  function closeDialog(dialog) {
    if (!dialog.open && !dialog.hasAttribute("open")) return;
    dialog.classList.add("is-closing");
    window.setTimeout(function () {
      dialog.classList.remove("is-open", "is-closing");
      if (dialog.open) dialog.close();
      else dialog.removeAttribute("open");
    }, 220);
  }

  function init(opts) {
    var form = document.getElementById((opts && opts.formId) || "project-create-form");
    var dialog = document.getElementById("project-create-dialog");
    var steps = document.getElementById("create-status-steps");
    var sub = document.getElementById("create-status-sub");
    var err = document.getElementById("create-status-error");
    var actions = document.getElementById("create-status-actions");
    var openBtn = document.getElementById("create-status-open");
    var closeBtn = document.getElementById("create-status-close");
    var submitBtn = document.getElementById("project-create-submit");
    if (!form || !dialog || form.dataset.boundCreate) return;
    form.dataset.boundCreate = "1";
    bindKindChoice();

    if (closeBtn) {
      closeBtn.addEventListener("click", function () {
        closeDialog(dialog);
      });
    }

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      if (form.dataset.creating === "1") return;
      form.dataset.creating = "1";
      if (submitBtn) submitBtn.disabled = true;

      if (err) {
        err.hidden = true;
        err.textContent = "";
      }
      if (actions) actions.hidden = true;
      if (sub) sub.textContent = "Hang on while Peon sets things up…";
      advanceOptimistic(steps, 0);
      openDialog(dialog);

      var tick = 0;
      var timer = window.setInterval(function () {
        tick += 1;
        if (tick === 1) advanceOptimistic(steps, 1);
        else if (tick === 2) advanceOptimistic(steps, 2);
        else if (tick >= 3) advanceOptimistic(steps, 2);
      }, 900);

      var url = form.getAttribute("action") || "/projects/new/";
      fetch(url, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": window.PeonUI.csrfToken(),
        },
        body: new FormData(form),
      })
        .then(function (res) {
          return res.json().then(function (data) {
            return { ok: res.ok, status: res.status, data: data };
          });
        })
        .then(function (pack) {
          window.clearInterval(timer);
          var data = pack.data || {};
          applyStages(steps, data.stages);
          if (!pack.ok || !data.ok || !data.redirect) {
            if (sub) sub.textContent = "Could not finish creating the project.";
            if (err) {
              err.hidden = false;
              err.textContent =
                (data && data.message) ||
                (data && data.error) ||
                "Create failed.";
            }
            if (actions) actions.hidden = false;
            if (openBtn && data.redirect) {
              openBtn.href = data.redirect;
              openBtn.hidden = false;
            } else if (openBtn) {
              openBtn.hidden = true;
            }
            form.dataset.creating = "";
            if (submitBtn) submitBtn.disabled = false;
            return;
          }
          if (sub) {
            if (data.needs_plan_review) {
              sub.textContent = "Plan ready — opening agent graph editor…";
            } else {
              sub.textContent = data.plan_ok
                ? "Project is ready — opening war room…"
                : (data.message || "Project created — opening war room…");
            }
          }
          if (openBtn) openBtn.href = data.redirect;
          window.setTimeout(function () {
            window.location.href = data.redirect;
          }, 650);
        })
        .catch(function () {
          window.clearInterval(timer);
          if (sub) sub.textContent = "Network error while creating the project.";
          if (err) {
            err.hidden = false;
            err.textContent = "Could not reach the server. Try again.";
          }
          if (actions) actions.hidden = false;
          if (openBtn) openBtn.hidden = true;
          form.dataset.creating = "";
          if (submitBtn) submitBtn.disabled = false;
        });
    });
  }

  window.PeonProjectCreate = { init: init };
})();
