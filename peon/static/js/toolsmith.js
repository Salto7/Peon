/* Toolsmith page — Tool / Skill switch, engine select, edit, save, lab test */
(function () {
  var app = document.getElementById("toolsmith-app");
  if (!app) return;

  var mode = "tool";
  var labBusy = false;
  var lastInstallError = "";

  function csrfToken() {
    return (window.PeonUI && window.PeonUI.csrfToken && window.PeonUI.csrfToken()) || "";
  }

  function currentEngine() {
    var sel = document.getElementById("toolsmith-engine");
    return (sel && sel.value) || "peon";
  }

  function selectedTools(sel) {
    if (!sel) return [];
    return Array.prototype.slice
      .call(sel.selectedOptions || [])
      .map(function (o) {
        return o.value;
      })
      .filter(Boolean);
  }

  function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": csrfToken(),
      },
      body: JSON.stringify(body || {}),
    }).then(function (res) {
      return res.json().then(function (data) {
        return { ok: res.ok, status: res.status, data: data };
      });
    });
  }

  function getJson(url) {
    return fetch(url, {
      method: "GET",
      headers: {
        Accept: "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
    }).then(function (res) {
      return res.json().then(function (data) {
        return { ok: res.ok, status: res.status, data: data };
      });
    });
  }

  function toast(msg, kind) {
    if (window.PeonUI && typeof window.PeonUI.toast === "function") {
      window.PeonUI.toast(msg, kind);
    }
  }

  function setBusy(btn, busy) {
    if (!btn) return;
    btn.disabled = !!busy;
  }

  function setThinking(on) {
    var el = document.getElementById("toolsmith-thinking");
    if (!el) return;
    el.hidden = !on;
  }

  function showReplanPanel(show, seedPrompt) {
    var panel = document.getElementById("tool-replan-panel");
    var ta = document.getElementById("tool-replan-prompt");
    if (!panel) return;
    panel.hidden = !show;
    if (show && ta && seedPrompt != null && !(ta.value || "").trim()) {
      ta.value = seedPrompt;
    }
  }

  function pollLabUntil(predicate, attempts, delayMs) {
    attempts = attempts || 20;
    delayMs = delayMs || 500;
    var url = app.getAttribute("data-lab-status");

    function once(left) {
      return getJson(url).then(function (st) {
        var lab = st.ok && st.data && st.data.lab ? st.data.lab : null;
        if (lab) updateLabUi(lab);
        if (lab && predicate(lab)) return lab;
        if (left <= 1) return lab;
        return new Promise(function (resolve) {
          setTimeout(function () {
            resolve(once(left - 1));
          }, delayMs);
        });
      });
    }
    return once(attempts);
  }

  function pollLabRunning(attempts, delayMs) {
    return pollLabUntil(
      function (lab) {
        return !!lab.running;
      },
      attempts,
      delayMs
    );
  }

  function pollLabGone(attempts, delayMs) {
    return pollLabUntil(
      function (lab) {
        return !lab.exists && !lab.running;
      },
      attempts,
      delayMs
    );
  }

  function applyMode(next) {
    mode = next === "skill" ? "skill" : "tool";
    app.querySelectorAll(".toolsmith-mode-btn").forEach(function (btn) {
      var on = btn.getAttribute("data-mode") === mode;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    app.querySelectorAll(".toolsmith-tool-only").forEach(function (el) {
      if (mode !== "tool") {
        el.hidden = true;
        return;
      }
      if (el.id === "tool-result") {
        el.hidden = !((document.getElementById("tool-yaml") || {}).value || "").trim();
        return;
      }
      el.hidden = false;
    });
    app.querySelectorAll(".toolsmith-skill-only").forEach(function (el) {
      if (mode !== "skill") {
        el.hidden = true;
        return;
      }
      if (el.id === "skill-result") {
        el.hidden = !((document.getElementById("skill-md") || {}).value || "").trim();
        return;
      }
      el.hidden = false;
    });
    var prompt = document.getElementById("toolsmith-prompt");
    if (prompt) {
      prompt.placeholder =
        mode === "tool"
          ? "e.g. Add ProjectDiscovery nuclei for authorized vulnerability scanning"
          : "e.g. Skill that runs httpx against in-scope URLs and records findings";
    }
    var gen = document.getElementById("toolsmith-generate-btn");
    if (gen) gen.textContent = mode === "tool" ? "Suggest tool" : "Draft skill";
  }

  function updateLabUi(lab) {
    if (!lab) return;
    var nameEl = document.getElementById("tool-lab-name");
    var status = document.getElementById("tool-lab-status");
    var name = lab.name || app.getAttribute("data-lab-name") || "peon-toolsmith-lab";

    if (nameEl) nameEl.textContent = name;
    app.setAttribute("data-lab-name", name);
    app.setAttribute("data-lab-exists", lab.exists ? "1" : "0");
    app.setAttribute("data-lab-running", lab.running ? "1" : "0");

    if (!labBusy) {
      ["tool-lab-setup-btn", "tool-lab-decommission-btn", "tool-lab-reset-btn"].forEach(
        function (id) {
          var btn = document.getElementById(id);
          if (btn) btn.disabled = lab.docker === false;
        }
      );
    }
    if (status) {
      if (lab.error) {
        status.textContent = lab.error;
      } else if (lab.docker === false) {
        status.textContent = "Docker unavailable from web — remount docker.sock and recreate web";
      } else {
        var code = document.createElement("code");
        code.className = "ui-code";
        code.textContent = lab.image || "";
        var lead = lab.running
          ? "Running on image "
          : lab.exists
            ? "Exists but stopped · image "
            : "Not created · image ";
        status.replaceChildren(document.createTextNode(lead), code);
      }
    }
  }

  function showTestLog(text) {
    var log = document.getElementById("tool-test-log");
    if (!log) return;
    if (text) {
      log.hidden = false;
      log.textContent = text;
    } else {
      log.hidden = true;
      log.textContent = "";
    }
  }

  function fillTool(data) {
    var box = document.getElementById("tool-result");
    var yaml = document.getElementById("tool-yaml");
    var script = document.getElementById("tool-script");
    var notes = document.getElementById("tool-notes");
    if (box) box.hidden = false;
    if (yaml) yaml.value = data.yaml || "";
    if (script) script.value = data.install_script || "";
    if (notes) notes.textContent = data.notes || "";
  }

  function collectSkillFiles() {
    var files = {};
    var root = document.getElementById("skill-files");
    if (!root) return files;
    root.querySelectorAll("textarea[data-rel]").forEach(function (ta) {
      var rel = ta.getAttribute("data-rel");
      if (rel) files[rel] = ta.value || "";
    });
    return files;
  }

  function renderSkillFiles(map) {
    var root = document.getElementById("skill-files");
    if (!root) return;
    root.replaceChildren();
    Object.keys(map || {}).forEach(function (rel) {
      var label = document.createElement("label");
      label.className = "ui-label mt-4 block font-mono text-xs";
      label.textContent = rel;
      label.setAttribute("for", "skill-file-" + rel.replace(/[^\w.-]+/g, "-"));
      var ta = document.createElement("textarea");
      ta.id = label.getAttribute("for");
      ta.className = "ui-textarea mt-1.5 font-mono";
      ta.rows = 10;
      ta.spellcheck = false;
      ta.setAttribute("data-rel", rel);
      ta.value = map[rel] || "";
      root.appendChild(label);
      root.appendChild(ta);
    });
  }

  function fillSkill(data) {
    var box = document.getElementById("skill-result");
    var name = document.getElementById("skill-name");
    var lint = document.getElementById("skill-lint");
    var md = document.getElementById("skill-md");
    var notes = document.getElementById("skill-notes");
    if (box) box.hidden = false;
    if (name) name.value = data.name || "";
    if (md) md.value = data.skill_md || "";
    renderSkillFiles(data.files || {});
    if (notes) notes.textContent = data.notes || "";
    showLint(lint, data.lint || {});
  }

  function showLint(el, L) {
    if (!el) return;
    if (!L || (L.compatible === undefined && !(L.errors || []).length)) {
      el.textContent = "";
      return;
    }
    if (L.compatible) {
      var warns = (L.warnings || [])
        .map(function (e) {
          return e.message || e.code || JSON.stringify(e);
        })
        .filter(Boolean);
      el.textContent =
        "Lint: compatible" + (warns.length ? " (warnings: " + warns.join("; ") + ")" : "");
    } else {
      el.textContent =
        "Lint: incompatible — " +
        ((L.errors || [])
          .map(function (e) {
            return e.message || e.code || JSON.stringify(e);
          })
          .join("; ") || "errors");
    }
  }

  function bindModeSwitch() {
    app.querySelectorAll(".toolsmith-mode-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        applyMode(btn.getAttribute("data-mode"));
      });
    });
  }

  function bindGenerate() {
    var form = document.getElementById("toolsmith-generate-form");
    if (!form) return;
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      if (app.getAttribute("data-authoring-ready") !== "1") {
        toast(
          "Enable LiteLLM proxy in Settings — required to create skills/tools with OpenCode.",
          "error"
        );
        return;
      }
      var prompt = (document.getElementById("toolsmith-prompt") || {}).value || "";
      var btn = document.getElementById("toolsmith-generate-btn");
      setBusy(btn, true);
      setThinking(true);
      if (mode === "tool") {
        postJson(app.getAttribute("data-suggest-tool"), { prompt: prompt, engine: currentEngine() })
          .then(function (out) {
            if (!out.ok || !out.data || !out.data.ok) {
              toast((out.data && out.data.error) || "Suggest failed", "error");
              return;
            }
            fillTool(out.data);
            toast("Tool suggested: " + (out.data.id || ""), "ok");
          })
          .catch(function () {
            toast("Suggest failed", "error");
          })
          .finally(function () {
            setThinking(false);
            setBusy(btn, false);
          });
      } else {
        postJson(app.getAttribute("data-suggest-skill"), {
          prompt: prompt,
          tools: selectedTools(document.getElementById("skill-tools")),
          engine: currentEngine(),
        })
          .then(function (out) {
            if (!out.ok || !out.data || !out.data.ok) {
              toast((out.data && out.data.error) || "Draft failed", "error");
              return;
            }
            fillSkill(out.data);
            var L = out.data.lint || {};
            toast(
              L.compatible ? "Skill drafted (lint ok)" : "Skill drafted (lint issues)",
              L.compatible ? "ok" : "error"
            );
          })
          .catch(function () {
            toast("Draft failed", "error");
          })
          .finally(function () {
            setThinking(false);
            setBusy(btn, false);
          });
      }
    });
  }

  // All three lab lifecycle actions share this shape: POST, then (when the
  // worker queues the real docker op, to dodge the web-side docker-rm
  // network flap) poll status until it settles. One function, three thin
  // bindings, instead of three near-copies of the same request/poll dance.
  function runLabAction(opts) {
    var btn = document.getElementById(opts.btnId);
    if (!btn) return;
    btn.addEventListener("click", function () {
      if (labBusy) return;
      labBusy = true;
      ["tool-lab-setup-btn", "tool-lab-decommission-btn", "tool-lab-reset-btn"].forEach(
        function (id) {
          var b = document.getElementById(id);
          if (b) b.disabled = true;
        }
      );
      var status = document.getElementById("tool-lab-status");
      if (status) status.textContent = opts.busyLabel;

      postJson(app.getAttribute(opts.urlAttr), {})
        .then(function (out) {
          if (!out.ok || !out.data || !out.data.ok) {
            toast((out.data && out.data.error) || opts.failLabel, "error");
            return opts.poll ? opts.poll(8, 400) : null;
          }
          if (out.data.action === "queued") {
            toast(opts.queuedLabel, "ok");
            return opts.poll ? opts.poll(24, 500) : null;
          }
          if (out.data.lab) updateLabUi(out.data.lab);
          toast(opts.doneLabel(out.data), "ok");
          return null;
        })
        .catch(function () {
          toast(opts.failLabel, "error");
          return opts.poll ? opts.poll(8, 400) : null;
        })
        .finally(function () {
          labBusy = false;
          getJson(app.getAttribute("data-lab-status")).then(function (st) {
            if (st.ok && st.data && st.data.lab) updateLabUi(st.data.lab);
          });
        });
    });
  }

  function bindLabControls() {
    runLabAction({
      btnId: "tool-lab-setup-btn",
      urlAttr: "data-lab-setup",
      busyLabel: "Setting up…",
      failLabel: "Setup failed",
      queuedLabel: "Setting up…",
      doneLabel: function (data) {
        return "Lab ready: " + (data.name || "");
      },
    });
    runLabAction({
      btnId: "tool-lab-decommission-btn",
      urlAttr: "data-lab-decommission",
      busyLabel: "Decommissioning…",
      failLabel: "Decommission failed",
      queuedLabel: "Decommissioning…",
      doneLabel: function (data) {
        return data.removed ? "Lab decommissioned" : "No lab to remove";
      },
      poll: pollLabGone,
    });
    runLabAction({
      btnId: "tool-lab-reset-btn",
      urlAttr: "data-lab-reset",
      busyLabel: "Resetting…",
      failLabel: "Reset failed",
      queuedLabel: "Resetting…",
      doneLabel: function () {
        return "Lab reset";
      },
      poll: pollLabRunning,
    });
  }

  function bindToolActions() {
    var saveBtn = document.getElementById("tool-save-btn");
    var testBtn = document.getElementById("tool-test-btn");
    var replanBtn = document.getElementById("tool-replan-btn");

    if (saveBtn) {
      saveBtn.addEventListener("click", function () {
        setBusy(saveBtn, true);
        var yamlText = (document.getElementById("tool-yaml") || {}).value || "";
        var scriptText = (document.getElementById("tool-script") || {}).value || "";
        var doSave = function (overwrite) {
          return postJson(app.getAttribute("data-save-tool"), {
            yaml: yamlText,
            install_script: scriptText,
            overwrite: !!overwrite,
          }).then(function (out) {
            if (out.status === 409 && out.data && out.data.exists) {
              var confirmed = window.confirm(
                (out.data.error || "That tool already exists.") + " Overwrite it?"
              );
              return confirmed ? doSave(true) : out;
            }
            return out;
          });
        };
        doSave(false)
          .then(function (out) {
            if (!out.ok || !out.data || !out.data.ok) {
              if (out.data && out.data.exists) return; // user declined overwrite
              toast((out.data && out.data.error) || "Save failed", "error");
              return;
            }
            toast("Saved " + (out.data.path || out.data.id || "tool"), "ok");
          })
          .catch(function () {
            toast("Save failed", "error");
          })
          .finally(function () {
            setBusy(saveBtn, false);
          });
      });
    }

    if (testBtn) {
      testBtn.addEventListener("click", function () {
        var yaml = (document.getElementById("tool-yaml") || {}).value || "";
        var script = (document.getElementById("tool-script") || {}).value || "";
        setBusy(testBtn, true);
        showReplanPanel(false);
        showTestLog("Resetting a clean test container and running the install…");
        postJson(app.getAttribute("data-test-tool"), {
          yaml: yaml,
          install_script: script,
        })
          .then(function (out) {
            if (!out.ok || !out.data || !out.data.ok) {
              showTestLog((out.data && out.data.error) || "Test request failed");
              toast((out.data && out.data.error) || "Test failed", "error");
              if (out.data && out.data.lab) updateLabUi(out.data.lab);
              return;
            }
            updateLabUi(out.data.lab);
            var parts = [];
            if (out.data.message) parts.push(out.data.message);
            if (out.data.verify_output) {
              parts.push("--- verify command output ---");
              parts.push(out.data.verify_output);
            } else if (out.data.log) {
              parts.push(out.data.log);
            }
            var logText = parts.join("\n\n");
            showTestLog(logText);
            lastInstallError = logText;
            if (out.data.install_ok || out.data.verified) {
              showReplanPanel(false);
              toast(
                "Install finished" + (out.data.tool_id ? ": " + out.data.tool_id : "") +
                  " — see verify output",
                "ok"
              );
              return;
            }
            var seed =
              (document.getElementById("toolsmith-prompt") || {}).value ||
              "Fix the catalog install recipe for the Peon sandbox (Kali).";
            showReplanPanel(true, seed);
            toast("Install finished — check verify output below", "error");
          })
          .catch(function () {
            toast("Test failed", "error");
            showTestLog("Test failed");
          })
          .finally(function () {
            setBusy(testBtn, false);
          });
      });
    }

    if (replanBtn) {
      replanBtn.addEventListener("click", function () {
        var promptEl = document.getElementById("tool-replan-prompt");
        var prompt =
          (promptEl && promptEl.value) ||
          (document.getElementById("toolsmith-prompt") || {}).value ||
          "";
        var yaml = (document.getElementById("tool-yaml") || {}).value || "";
        var script = (document.getElementById("tool-script") || {}).value || "";
        if (!(prompt || "").trim()) {
          toast("Add a replan prompt first", "error");
          return;
        }
        setBusy(replanBtn, true);
        setThinking(true);
        postJson(app.getAttribute("data-replan-tool"), {
          prompt: prompt,
          yaml: yaml,
          install_script: script,
          error: lastInstallError,
          engine: currentEngine(),
        })
          .then(function (rep) {
            if (!rep.ok || !rep.data || !rep.data.ok) {
              toast((rep.data && rep.data.error) || "Replan failed", "error");
              return;
            }
            fillTool(rep.data);
            showTestLog(
              (document.getElementById("tool-test-log") || {}).textContent +
                "\n\n--- replan ---\n" +
                (rep.data.notes || "Recipe updated; review and re-test.")
            );
            toast("Install recipe updated — review and re-test", "ok");
          })
          .catch(function () {
            toast("Replan failed", "error");
          })
          .finally(function () {
            setThinking(false);
            setBusy(replanBtn, false);
          });
      });
    }
  }

  function bindSkillActions() {
    var lintBtn = document.getElementById("skill-lint-btn");
    var saveBtn = document.getElementById("skill-save-btn");

    function payload() {
      return {
        name: (document.getElementById("skill-name") || {}).value || "",
        skill_md: (document.getElementById("skill-md") || {}).value || "",
        files: collectSkillFiles(),
      };
    }

    if (lintBtn) {
      lintBtn.addEventListener("click", function () {
        setBusy(lintBtn, true);
        postJson(app.getAttribute("data-lint-skill"), payload())
          .then(function (out) {
            if (!out.ok || !out.data || !out.data.ok) {
              toast((out.data && out.data.error) || "Lint failed", "error");
              if (out.data && out.data.lint) {
                showLint(document.getElementById("skill-lint"), out.data.lint);
              }
              return;
            }
            showLint(document.getElementById("skill-lint"), out.data.lint || {});
            if (out.data.skill_md) fillSkill(out.data);
            toast(
              out.data.compatible ? "Lint ok" : "Lint found errors",
              out.data.compatible ? "ok" : "error"
            );
          })
          .catch(function () {
            toast("Lint failed", "error");
          })
          .finally(function () {
            setBusy(lintBtn, false);
          });
      });
    }

    if (saveBtn) {
      saveBtn.addEventListener("click", function () {
        setBusy(saveBtn, true);
        postJson(app.getAttribute("data-save-skill"), payload())
          .then(function (out) {
            if (!out.ok || !out.data || !out.data.ok) {
              toast((out.data && out.data.error) || "Save failed", "error");
              if (out.data && out.data.lint) {
                showLint(document.getElementById("skill-lint"), out.data.lint);
              }
              return;
            }
            showLint(document.getElementById("skill-lint"), out.data.lint || {});
            if (out.data.skill_md) {
              fillSkill({
                name: out.data.name,
                skill_md: out.data.skill_md,
                files: out.data.files || {},
                lint: out.data.lint || {},
              });
            }
            toast("Saved " + (out.data.id || out.data.path || out.data.name || "skill"), "ok");
          })
          .catch(function () {
            toast("Save failed", "error");
          })
          .finally(function () {
            setBusy(saveBtn, false);
          });
      });
    }
  }

  bindModeSwitch();
  applyMode("tool");
  bindGenerate();
  bindLabControls();
  bindToolActions();
  bindSkillActions();

  getJson(app.getAttribute("data-lab-status")).then(function (st) {
    if (st.ok && st.data && st.data.lab) updateLabUi(st.data.lab);
  });
})();
