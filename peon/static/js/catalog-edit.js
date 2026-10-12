/* Shared catalog — edit roles YAML / skill SKILL.md / tool YAML */
(function () {
  var cfg = window.PEON_CATALOG_EDIT || {};
  var urls = cfg.urls || {};
  var state = { kind: "", id: "", files: {} };

  function $(id) {
    return document.getElementById(id);
  }

  function csrfToken() {
    var inp = document.querySelector("#catalog-edit-form input[name=csrfmiddlewaretoken]");
    if (inp && inp.value) return inp.value;
    return (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || "";
  }

  function toast(msg, kind) {
    if (window.PeonUI && typeof window.PeonUI.toast === "function") {
      window.PeonUI.toast(msg, kind);
    }
  }

  function openDialog() {
    var dlg = $("catalog-edit-dialog");
    if (!dlg) return;
    dlg.classList.remove("is-closing");
    dlg.classList.add("is-open");
    if (typeof dlg.showModal === "function") dlg.showModal();
    else dlg.setAttribute("open", "");
  }

  function closeDialog() {
    var dlg = $("catalog-edit-dialog");
    if (!dlg) return;
    dlg.classList.add("is-closing");
    window.setTimeout(function () {
      dlg.classList.remove("is-open", "is-closing");
      if (dlg.open) dlg.close();
      else dlg.removeAttribute("open");
    }, 200);
  }

  function setErr(msg) {
    var el = $("catalog-edit-error");
    if (!el) return;
    if (msg) {
      el.hidden = false;
      el.textContent = msg;
    } else {
      el.hidden = true;
      el.textContent = "";
    }
  }

  function showSkillFiles(files) {
    var wrap = $("catalog-edit-files");
    if (!wrap) return;
    wrap.innerHTML = "";
    state.files = files || {};
    var keys = Object.keys(state.files);
    if (!keys.length) {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    keys.forEach(function (rel) {
      var label = document.createElement("label");
      label.className = "meta";
      label.textContent = rel;
      label.setAttribute("for", "catalog-file-" + rel.replace(/[^\w.-]+/g, "_"));
      var ta = document.createElement("textarea");
      ta.id = "catalog-file-" + rel.replace(/[^\w.-]+/g, "_");
      ta.className = "learn-edit catalog-edit-ta";
      ta.rows = 8;
      ta.dataset.rel = rel;
      ta.value = state.files[rel] || "";
      ta.spellcheck = false;
      wrap.appendChild(label);
      wrap.appendChild(ta);
    });
  }

  function collectSkillFiles() {
    var wrap = $("catalog-edit-files");
    var out = {};
    if (!wrap) return out;
    wrap.querySelectorAll("textarea[data-rel]").forEach(function (ta) {
      out[ta.dataset.rel] = ta.value;
    });
    return out;
  }

  function configureForm(kind, id, data) {
    state.kind = kind;
    state.id = id || "";
    var title = $("catalog-edit-title");
    var pathEl = $("catalog-edit-path");
    var mainLabel = $("catalog-edit-main-label");
    var main = $("catalog-edit-main");
    var scriptWrap = $("catalog-edit-script-wrap");
    var script = $("catalog-edit-script");
    var filesWrap = $("catalog-edit-files");
    setErr("");
    if (pathEl) pathEl.textContent = data.path || "";
    if (kind === "skill") {
      if (title) title.textContent = "Edit skill · " + id;
      if (mainLabel) mainLabel.textContent = "SKILL.md";
      if (main) {
        main.value = data.skill_md || "";
        main.rows = 18;
      }
      if (scriptWrap) scriptWrap.hidden = true;
      showSkillFiles(data.files || {});
      return;
    }
    if (title) title.textContent = "Edit tool · " + id;
    if (mainLabel) mainLabel.textContent = "Catalog YAML";
    if (main) {
      main.value = data.yaml || "";
      main.rows = 18;
    }
    if (scriptWrap) scriptWrap.hidden = false;
    if (script) script.value = data.install_script || "";
    if (filesWrap) {
      filesWrap.hidden = true;
      filesWrap.innerHTML = "";
    }
  }

  function fetchJson(url, opts) {
    opts = opts || {};
    var headers = {
      Accept: "application/json",
      "X-Requested-With": "XMLHttpRequest",
    };
    if (opts.method && opts.method !== "GET") {
      headers["Content-Type"] = "application/json";
      headers["X-CSRFToken"] = csrfToken();
    }
    return fetch(url, {
      method: opts.method || "GET",
      headers: headers,
      credentials: "same-origin",
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    }).then(function (res) {
      return res.json().then(function (data) {
        return { ok: res.ok, status: res.status, data: data };
      });
    });
  }

  function openSkillEditor(id) {
    var tmpl = urls.skill_source || "";
    var url = tmpl.replace("{id}", encodeURIComponent(id));
    fetchJson(url)
      .then(function (pack) {
        if (!pack.ok || !pack.data.ok) {
          toast((pack.data && pack.data.error) || "Could not load skill", "error");
          return;
        }
        configureForm("skill", id, pack.data);
        openDialog();
      })
      .catch(function () {
        toast("Could not load skill", "error");
      });
  }

  function openToolEditor(id) {
    var tmpl = urls.tool_source || "";
    var url = tmpl.replace("{id}", encodeURIComponent(id));
    fetchJson(url)
      .then(function (pack) {
        if (!pack.ok || !pack.data.ok) {
          toast((pack.data && pack.data.error) || "Could not load tool", "error");
          return;
        }
        configureForm("tool", id, pack.data);
        openDialog();
      })
      .catch(function () {
        toast("Could not load tool", "error");
      });
  }

  function save() {
    var main = $("catalog-edit-main");
    var btn = $("catalog-edit-save");
    if (!main) return;
    if (btn) btn.disabled = true;
    setErr("");
    var req;
    if (state.kind === "skill") {
      var skillUrl = (urls.skill_source || "").replace(
        "{id}",
        encodeURIComponent(state.id)
      );
      req = fetchJson(skillUrl, {
        method: "POST",
        body: { skill_md: main.value, files: collectSkillFiles() },
      });
    } else {
      var toolUrl = (urls.tool_source || "").replace(
        "{id}",
        encodeURIComponent(state.id)
      );
      var script = $("catalog-edit-script");
      req = fetchJson(toolUrl, {
        method: "POST",
        body: {
          yaml: main.value,
          install_script: (script && script.value) || "",
        },
      });
    }
    req
      .then(function (pack) {
        if (btn) btn.disabled = false;
        if (!pack.ok || (pack.data && pack.data.ok === false)) {
          var err =
            (pack.data && pack.data.error) ||
            (pack.data &&
              pack.data.lint &&
              (pack.data.lint.errors || []).join("; ")) ||
            "Save failed";
          setErr(err);
          toast(err, "error");
          return;
        }
        toast("Saved", "ok");
        closeDialog();
        window.setTimeout(function () {
          window.location.reload();
        }, 350);
      })
      .catch(function () {
        if (btn) btn.disabled = false;
        setErr("Network error while saving");
        toast("Save failed", "error");
      });
  }

  document.querySelectorAll("[data-catalog-edit]").forEach(function (el) {
    el.addEventListener("click", function (ev) {
      ev.preventDefault();
      var kind = el.getAttribute("data-catalog-edit");
      var id = el.getAttribute("data-id") || "";
      if (kind === "skill") openSkillEditor(id);
      else if (kind === "tool") openToolEditor(id);
    });
  });

  $("catalog-edit-save")?.addEventListener("click", function (ev) {
    ev.preventDefault();
    save();
  });
  $("catalog-edit-cancel")?.addEventListener("click", function (ev) {
    ev.preventDefault();
    closeDialog();
  });
  $("catalog-edit-dialog")?.addEventListener("cancel", function (ev) {
    ev.preventDefault();
    closeDialog();
  });
})();
