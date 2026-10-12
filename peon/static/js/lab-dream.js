/* Lab Dream — Cognee catalog pickers + recall / improve */
(function () {
  const cfg = window.PEON_DREAM || {};
  const urls = cfg.urls || {};
  let catalog = cfg.catalog || { datasets: [], sessions: [] };
  let mode = "recall";

  function $(id) {
    return document.getElementById(id);
  }

  function csrfToken() {
    const inp = document.querySelector("#dream-action-form input[name=csrfmiddlewaretoken]");
    if (inp && inp.value) return inp.value;
    return (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || "";
  }

  async function postJson(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
      },
      credentials: "same-origin",
      body: JSON.stringify(body || {}),
    });
    return res.json();
  }

  function showOut(text) {
    const out = $("dream-out");
    if (!out) return;
    out.hidden = false;
    out.textContent = text;
  }

  function fillDatasets() {
    const sel = $("dream-dataset");
    if (!sel) return;
    const prev = sel.value || "peon";
    const rows = catalog.datasets || [];
    sel.innerHTML = "";
    const allOpt = document.createElement("option");
    allOpt.value = "__all__";
    allOpt.textContent = "All datasets";
    sel.appendChild(allOpt);
    if (!rows.length) {
      const opt = document.createElement("option");
      opt.value = "peon";
      opt.textContent = "peon — platform memory";
      sel.appendChild(opt);
    } else {
      rows.forEach((d) => {
        const opt = document.createElement("option");
        opt.value = d.name;
        opt.textContent = d.label || d.name;
        sel.appendChild(opt);
      });
    }
    if ([...sel.options].some((o) => o.value === prev)) sel.value = prev;
    else if ([...sel.options].some((o) => o.value === "peon")) sel.value = "peon";
  }

  function fillSessions() {
    const sel = $("dream-session");
    const ds = ($("dream-dataset") && $("dream-dataset").value) || "peon";
    if (!sel) return;
    const prev = sel.value || "";
    const allDs = ds === "__all__";
    const rows = allDs
      ? []
      : (catalog.sessions || []).filter((s) => !ds || s.dataset === ds);
    sel.innerHTML = "";
    const all = document.createElement("option");
    all.value = "";
    all.textContent = allDs
      ? "All sessions (per dataset)"
      : "All sessions in dataset";
    sel.appendChild(all);
    rows.forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s.session_id;
      opt.textContent = s.label || s.session_id;
      sel.appendChild(opt);
    });
    sel.disabled = allDs;
    if (!allDs && prev && [...sel.options].some((o) => o.value === prev)) {
      sel.value = prev;
    }
    const hint = $("dream-session-hint");
    if (hint) {
      if (allDs) {
        hint.textContent =
          mode === "recall"
            ? "All datasets — recall searches each selected catalog name via peon unless you pick one dataset."
            : "Improve runs once per dataset. Session targeting is disabled for All datasets.";
      } else {
        hint.textContent =
          mode === "recall"
            ? "Optional — leave blank to search the whole dataset."
            : "Optional — leave blank to improve the whole dataset; pick a session to target it.";
      }
    }
  }

  function setMode(next) {
    mode = next === "improve" ? "improve" : "recall";
    document.querySelectorAll("[data-dream-mode]").forEach((btn) => {
      const on = btn.getAttribute("data-dream-mode") === mode;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    const wrap = $("dream-query-wrap");
    if (wrap) wrap.hidden = mode !== "recall";
    const btn = $("dream-action-btn");
    if (btn) btn.textContent = mode === "recall" ? "Recall" : "Improve";
    fillSessions();
  }

  async function refreshStatus() {
    if (!urls.status) return;
    const res = await fetch(urls.status, { headers: { Accept: "application/json" } });
    const data = await res.json();
    const cognee = data.cognee || {};
    const okEl = $("dream-cognee-ok");
    if (okEl) okEl.textContent = cognee.ok ? "healthy" : "unreachable";
    const errEl = $("dream-cognee-error");
    if (errEl) {
      if (cognee.error) {
        errEl.hidden = false;
        errEl.textContent = cognee.error;
      } else {
        errEl.hidden = true;
        errEl.textContent = "";
      }
    }
    const last = $("dream-last-run");
    if (last) last.textContent = data.last_run_at || "—";
    const count = $("dream-session-count");
    if (count) count.textContent = String(data.session_count ?? 0);
  }

  async function refreshCatalog() {
    if (!urls.catalog) return;
    const res = await fetch(urls.catalog, { headers: { Accept: "application/json" } });
    const data = await res.json();
    catalog = {
      datasets: data.datasets || [],
      sessions: data.sessions || [],
    };
    fillDatasets();
    fillSessions();
  }

  $("dream-refresh")?.addEventListener("click", () => {
    Promise.all([refreshStatus(), refreshCatalog()]).catch(console.error);
  });

  $("dream-run-cycle")?.addEventListener("click", () => {
    postJson(urls.run_cycle || urls.run, {})
      .then((data) => {
        showOut(JSON.stringify(data, null, 2));
        return Promise.all([refreshStatus(), refreshCatalog()]);
      })
      .catch(console.error);
  });

  $("dream-dataset")?.addEventListener("change", () => fillSessions());

  document.querySelectorAll("[data-dream-mode]").forEach((btn) => {
    btn.addEventListener("click", () => setMode(btn.getAttribute("data-dream-mode")));
  });

  $("dream-action-form")?.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const dataset = ($("dream-dataset")?.value || "peon").trim() || "peon";
    const all_datasets = dataset === "__all__";
    const session_id = all_datasets
      ? ""
      : ($("dream-session")?.value || "").trim();
    if (mode === "recall") {
      const query = ($("dream-query")?.value || "").trim();
      if (!query) {
        showOut("Query required for recall.");
        return;
      }
      if (all_datasets) {
        showOut("Pick a single dataset for recall (All datasets is for Improve).");
        return;
      }
      postJson(urls.recall, { query, dataset, session_id })
        .then((data) => {
          showOut(data.text || data.error || JSON.stringify(data, null, 2));
        })
        .catch(console.error);
      return;
    }
    postJson(urls.improve, {
      dataset: all_datasets ? "__all__" : dataset,
      all_datasets,
      session_id,
      session_ids: session_id ? [session_id] : [],
    })
      .then((data) => {
        showOut(JSON.stringify(data, null, 2));
        return refreshStatus();
      })
      .catch(console.error);
  });

  fillDatasets();
  setMode("recall");
})();
