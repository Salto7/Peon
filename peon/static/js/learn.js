/* Learn page — Tool / Skill switch, edit, save, lab test */
(function () {
  var app = document.getElementById("learn-app");
  if (!app) return;

  var mode = "tool";
  var labBusy = false;
  var lastInstallError = "";

  function csrfToken() {
    var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
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
    var el = document.getElementById("learn-thinking");
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
    app.querySelectorAll(".learn-mode-btn").forEach(function (btn) {
      var on = btn.getAttribute("data-mode") === mode;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    app.querySelectorAll(".learn-tool-only").forEach(function (el) {
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
    app.querySelectorAll(".learn-skill-only").forEach(function (el) {
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
    var prompt = document.getElementById("learn-prompt");
    if (prompt) {
      prompt.placeholder =
        mode === "tool"
          ? "e.g. Add ProjectDiscovery nuclei for authorized vulnerability scanning"
          : "e.g. Skill that runs httpx against in-scope URLs and records findings";
    }
    var gen = document.getElementById("learn-generate-btn");
    if (gen) gen.textContent = mode === "tool" ? "Suggest tool" : "Draft skill";
  }

  function updateLabUi(lab) {
    if (!lab) return;
    var nameEl = document.getElementById("tool-lab-name");
    var status = document.getElementById("tool-lab-status");
    var toggle = document.getElementById("tool-lab-toggle");
    var label = document.getElementById("tool-lab-switch-label");
    var name = lab.name || app.getAttribute("data-lab-name") || "peon-learn-lab";

    if (nameEl) nameEl.textContent = name;
    app.setAttribute("data-lab-name", name);
    app.setAttribute("data-lab-exists", lab.exists ? "1" : "0");
    app.setAttribute("data-lab-running", lab.running ? "1" : "0");

    if (toggle && !labBusy) {
      toggle.checked = !!lab.running;
      toggle.disabled = lab.docker === false;
    }
    if (label) {
      if (lab.docker === false) {
        label.textContent = "Docker unavailable";
      } else if (lab.running) {
        label.textContent = "Running — turn off to delete";
      } else {
        label.textContent = "Off — turn on to create";
      }
    }
    if (status) {
      if (lab.error) {
        status.textContent = lab.error;
      } else if (lab.running) {
        status.innerHTML =
          "Running on image <code>" + (lab.image || "") + "</code>";
      } else if (lab.exists) {
        status.innerHTML =
          "Exists but stopped · image <code>" + (lab.image || "") + "</code>";
      } else if (lab.docker === false) {
        status.textContent = "Docker unavailable from web — remount docker.sock and recreate web";
      } else {
        status.innerHTML =
          "Not created · image <code>" + (lab.image || "") + "</code>";
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
      label.className = "meta";
      label.textContent = rel;
      label.setAttribute("for", "skill-file-" + rel.replace(/[^\w.-]+/g, "-"));
      var ta = document.createElement("textarea");
      ta.id = label.getAttribute("for");
      ta.className = "learn-edit";
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
    app.querySelectorAll(".learn-mode-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        applyMode(btn.getAttribute("data-mode"));
      });
    });
  }

  function bindGenerate() {
    var form = document.getElementById("learn-generate-form");
    if (!form) return;
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var prompt = (document.getElementById("learn-prompt") || {}).value || "";
      var btn = document.getElementById("learn-generate-btn");
      setBusy(btn, true);
      setThinking(true);
      if (mode === "tool") {
        postJson(app.getAttribute("data-suggest-tool"), { prompt: prompt })
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

  function bindLabSwitch() {
    var toggle = document.getElementById("tool-lab-toggle");
    if (!toggle) return;

    toggle.addEventListener("change", function () {
      if (labBusy) return;
      var wantOn = !!toggle.checked;
      // Already running — just refresh status (no second container).
      if (wantOn && app.getAttribute("data-lab-running") === "1") {
        getJson(app.getAttribute("data-lab-status")).then(function (st) {
          if (st.ok && st.data && st.data.lab) updateLabUi(st.data.lab);
        });
        toast("Already connected to " + (app.getAttribute("data-lab-name") || "lab"), "ok");
        return;
      }
      labBusy = true;
      toggle.disabled = true;
      var label = document.getElementById("tool-lab-switch-label");
      if (label) {
        label.textContent = wantOn ? "Creating…" : "Deleting…";
      }

      var req = wantOn
        ? postJson(app.getAttribute("data-lab-create"), {})
        : postJson(app.getAttribute("data-lab-delete"), {});

      req
        .then(function (out) {
          if (wantOn) {
            if (!out.ok || !out.data || !out.data.ok) {
              toast((out.data && out.data.error) || "Create failed", "error");
              if (out.data && out.data.lab) updateLabUi(out.data.lab);
              else toggle.checked = false;
              return null;
            }
            if (out.data.lab) updateLabUi(out.data.lab);
            toast("Test docker ready: " + (out.data.name || ""), "ok");
            return null;
          }

          // Delete is queued on the worker — poll until the lab is gone.
          if (!out.ok || !out.data || !out.data.ok) {
            // Network flap after docker rm: verify via status before failing.
            return pollLabGone(8, 400).then(function (lab) {
              if (lab && !lab.exists) {
                toast("Test docker deleted", "ok");
                return;
              }
              toast((out.data && out.data.error) || "Delete failed", "error");
              toggle.checked = true;
            });
          }

          if (out.data.action === "queued") {
            toast("Deleting test docker…", "ok");
            return pollLabGone(24, 500).then(function (lab) {
              if (lab && !lab.exists) {
                toast("Test docker deleted", "ok");
              } else {
                toast("Delete still pending — refresh if it stays on", "error");
                toggle.checked = !!(lab && lab.running);
              }
            });
          }

          if (out.data.lab) updateLabUi(out.data.lab);
          toast(
            out.data.removed ? "Test docker deleted" : "No lab container to remove",
            "ok"
          );
          return null;
        })
        .catch(function () {
          if (wantOn) {
            toast("Create failed", "error");
            toggle.checked = false;
            return null;
          }
          // Request aborted by docker network flap — confirm via status.
          return pollLabGone(8, 400).then(function (lab) {
            if (lab && !lab.exists) {
              toast("Test docker deleted", "ok");
            } else {
              toast("Delete failed", "error");
              toggle.checked = true;
            }
          });
        })
        .finally(function () {
          labBusy = false;
          getJson(app.getAttribute("data-lab-status")).then(function (st) {
            if (st.ok && st.data && st.data.lab) updateLabUi(st.data.lab);
            else {
              var t = document.getElementById("tool-lab-toggle");
              if (t) t.disabled = false;
            }
          });
        });
    });
  }

  function bindToolActions() {
    var saveBtn = document.getElementById("tool-save-btn");
    var testBtn = document.getElementById("tool-test-btn");
    var replanBtn = document.getElementById("tool-replan-btn");

    if (saveBtn) {
      saveBtn.addEventListener("click", function () {
        setBusy(saveBtn, true);
        postJson(app.getAttribute("data-save-tool"), {
          yaml: (document.getElementById("tool-yaml") || {}).value || "",
          install_script: (document.getElementById("tool-script") || {}).value || "",
        })
          .then(function (out) {
            if (!out.ok || !out.data || !out.data.ok) {
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
        if (app.getAttribute("data-lab-running") !== "1") {
          toast("Turn on the test docker switch first", "error");
          showTestLog("Test docker is not running — use the switch above to create it.");
          return;
        }
        var yaml = (document.getElementById("tool-yaml") || {}).value || "";
        var script = (document.getElementById("tool-script") || {}).value || "";
        setBusy(testBtn, true);
        showReplanPanel(false);
        showTestLog("Running install test in " + (app.getAttribute("data-lab-name") || "lab") + "…");
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
              (document.getElementById("learn-prompt") || {}).value ||
              "Fix the catalog install recipe for debian:bookworm-slim.";
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
          (document.getElementById("learn-prompt") || {}).value ||
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
            toast("Saved " + (out.data.path || out.data.name || "skill"), "ok");
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
  bindLabSwitch();
  bindToolActions();
  bindSkillActions();

  getJson(app.getAttribute("data-lab-status")).then(function (st) {
    if (st.ok && st.data && st.data.lab) updateLabUi(st.data.lab);
  });
})();
