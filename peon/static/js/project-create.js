/* New-project create flow: status dialog while planning, then redirect. */
(function () {
  function csrfToken() {
    return (window.PeonUI && window.PeonUI.csrfToken && window.PeonUI.csrfToken()) || "";
  }

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

    if (closeBtn) {
      closeBtn.addEventListener("click", function () {
        dialog.close();
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
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");

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
          "X-CSRFToken": csrfToken(),
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
            sub.textContent = data.plan_ok
              ? "Project is ready — opening war room…"
              : (data.message || "Project created — opening war room…");
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
