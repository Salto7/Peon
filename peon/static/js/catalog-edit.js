/* Shared catalog — edit platform roles, skills, MCP servers, and tool YAML. */
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
      var ta = document.createElement("textarea");
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

  function formatBindings(rows) {
    return (rows || [])
      .map(function (row) {
        return (
          row.role_id +
          " " +
          (row.score == null ? "1" : row.score) +
          (row.lead ? " lead" : "")
        );
      })
      .join("\n");
  }

  function parseBindings(text) {
    var out = [];
    String(text || "")
      .split("\n")
      .forEach(function (line) {
        var parts = line.trim().split(/\s+/);
        if (!parts[0]) return;
        var score = parts.length > 1 ? Number(parts[1]) : 1;
        if (!isFinite(score)) score = 1;
        out.push({
          role_id: parts[0],
          score: score,
          lead: parts.slice(2).indexOf("lead") !== -1 || parts[1] === "lead",
        });
      });
    return out;
  }

  function setBindingsVisible(show, rows) {
    var label = $("catalog-edit-bindings-label");
    var box = $("catalog-edit-bindings");
    if (label) label.hidden = !show;
    if (!box) return;
    box.hidden = !show;
    if (show) box.value = formatBindings(rows);
  }

  function setRoleFields(show, data) {
    var wrap = $("catalog-edit-role");
    if (wrap) wrap.hidden = !show;
    if (!show) return;
    var id = $("catalog-edit-role-id");
    var kind = $("catalog-edit-role-kind");
    var aliases = $("catalog-edit-role-aliases");
    if (id) {
      id.value = (data && data.id) || "";
      id.readOnly = !!(data && data.id && data.id !== "__new__");
    }
    if (kind) kind.value = (data && data.kind) || "technical";
    if (aliases) aliases.value = ((data && data.aliases) || []).join(", ");
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
    setRoleFields(false);
    setBindingsVisible(false);
    if (pathEl) pathEl.textContent = data.path || (data.id ? "platform:" + data.id : "");
    if (scriptWrap) scriptWrap.hidden = true;
    if (filesWrap) {
      filesWrap.hidden = true;
      filesWrap.innerHTML = "";
    }
    if (kind === "role") {
      if (title) title.textContent = id === "__new__" ? "New role" : "Edit role · " + id;
      if (mainLabel) mainLabel.hidden = true;
      if (main) main.hidden = true;
      setRoleFields(true, data);
      return;
    }
    if (mainLabel) mainLabel.hidden = false;
    if (main) main.hidden = false;
    if (kind === "skill") {
      if (title) title.textContent = id === "__new__" ? "New skill" : "Edit skill · " + id;
      if (mainLabel) mainLabel.textContent = "SKILL.md";
      if (main) {
        main.value = data.skill_md || "---\nname: \ndescription: \n---\n\n";
        main.rows = 18;
      }
      showSkillFiles(data.files || {});
      setBindingsVisible(true, data.bindings || []);
      return;
    }
    if (kind === "mcp") {
      if (title) title.textContent = id === "__new__" ? "New MCP server" : "Edit MCP · " + id;
      if (mainLabel) mainLabel.textContent = "Server JSON (no secret values)";
      if (main) {
        main.value = JSON.stringify(data.document || {}, null, 2);
        main.rows = 16;
      }
      if (pathEl) {
        pathEl.textContent =
          "Env names only. Set values per project. The server runs inside the project sandbox.";
      }
      showSkillFiles(data.files || {});
      setBindingsVisible(true, data.bindings || []);
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

  function fill(url, kind, id) {
    fetchJson(url)
      .then(function (pack) {
        if (!pack.ok || !pack.data.ok) {
          toast((pack.data && pack.data.error) || "Could not load", "error");
          return;
        }
        configureForm(kind, id, pack.data);
        openDialog();
      })
      .catch(function () {
        toast("Could not load", "error");
      });
  }

  function openEditor(kind, id) {
    if (kind === "skill") {
      if (id === "__new__") {
        configureForm("skill", id, { skill_md: "", files: {}, bindings: [] });
        openDialog();
        return;
      }
      fill((urls.skill_source || "").replace("{id}", encodeURIComponent(id)), "skill", id);
      return;
    }
    if (kind === "tool") {
      fill((urls.tool_source || "").replace("{id}", encodeURIComponent(id)), "tool", id);
      return;
    }
    if (kind === "mcp") {
      fill((urls.mcp_source || "").replace("{id}", encodeURIComponent(id || "__new__")), "mcp", id);
      return;
    }
    if (kind === "role") {
      if (id === "__new__") {
        configureForm("role", id, { id: "", kind: "technical", aliases: [], bindings: [] });
        openDialog();
        return;
      }
      fill((urls.role_source || "").replace("{id}", encodeURIComponent(id)) + "edit/", "role", id);
    }
  }

  function removeEntry(kind, id) {
    if (!id || !window.confirm("Delete " + kind + " " + id + "?")) return;
    var url = "";
    if (kind === "skill") url = "/catalog/api/skills/" + encodeURIComponent(id) + "/delete/";
    else if (kind === "mcp") url = (urls.mcp_source || "").replace("{id}", encodeURIComponent(id));
    else if (kind === "role") url = (urls.role_source || "").replace("{id}", encodeURIComponent(id)) + "edit/";
    if (!url) return;
    fetchJson(url, { method: "DELETE", body: {} })
      .then(function (pack) {
        if (!pack.ok || (pack.data && pack.data.ok === false)) {
          toast((pack.data && pack.data.error) || "Delete failed", "error");
          return;
        }
        toast("Deleted", "ok");
        window.setTimeout(function () {
          window.location.reload();
        }, 300);
      })
      .catch(function () {
        toast("Delete failed", "error");
      });
  }

  function save() {
    var main = $("catalog-edit-main");
    var btn = $("catalog-edit-save");
    if (btn) btn.disabled = true;
    setErr("");
    var bindings = parseBindings(($("catalog-edit-bindings") || {}).value || "");
    var req;
    if (state.kind === "skill") {
      var skillUrl = (urls.skill_source || "").replace(
        "{id}",
        encodeURIComponent(state.id || "__new__")
      );
      req = fetchJson(skillUrl, {
        method: "POST",
        body: {
          skill_md: main ? main.value : "",
          files: collectSkillFiles(),
          bindings: bindings,
        },
      });
    } else if (state.kind === "mcp") {
      var document;
      try {
        document = JSON.parse(main ? main.value : "{}");
      } catch (err) {
        if (btn) btn.disabled = false;
        setErr("Server JSON is invalid");
        return;
      }
      var mcpUrl = (urls.mcp_source || "").replace(
        "{id}",
        encodeURIComponent(state.id || "__new__")
      );
      req = fetchJson(mcpUrl, {
        method: "POST",
        body: { document: document, files: collectSkillFiles(), bindings: bindings },
      });
    } else if (state.kind === "role") {
      var roleId = ($("catalog-edit-role-id") || {}).value || "";
      var payload = {
        role_id: roleId.trim(),
        kind: ($("catalog-edit-role-kind") || {}).value || "technical",
        aliases: (($("catalog-edit-role-aliases") || {}).value || "")
          .split(",")
          .map(function (part) {
            return part.trim();
          })
          .filter(Boolean),
      };
      var roleUrl =
        state.id === "__new__"
          ? "/catalog/api/roles/create/"
          : (urls.role_source || "").replace("{id}", encodeURIComponent(state.id)) + "edit/";
      req = fetchJson(roleUrl, { method: "POST", body: payload });
    } else {
      var toolUrl = (urls.tool_source || "").replace("{id}", encodeURIComponent(state.id));
      var script = $("catalog-edit-script");
      req = fetchJson(toolUrl, {
        method: "POST",
        body: {
          yaml: main ? main.value : "",
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
      openEditor(el.getAttribute("data-catalog-edit"), el.getAttribute("data-id") || "");
    });
  });
  document.querySelectorAll("[data-catalog-delete]").forEach(function (el) {
    el.addEventListener("click", function (ev) {
      ev.preventDefault();
      removeEntry(el.getAttribute("data-catalog-delete"), el.getAttribute("data-id") || "");
    });
  });

  $("catalog-edit-save") &&
    $("catalog-edit-save").addEventListener("click", function (ev) {
      ev.preventDefault();
      save();
    });
  $("catalog-edit-cancel") &&
    $("catalog-edit-cancel").addEventListener("click", function (ev) {
      ev.preventDefault();
      closeDialog();
    });
  $("catalog-edit-dialog") &&
    $("catalog-edit-dialog").addEventListener("cancel", function (ev) {
      ev.preventDefault();
      closeDialog();
    });
})();
