/* Project ops console: agent graph, context, skills cards, live feed. */
(function () {
  function el(id) {
    return document.getElementById(id);
  }

  function csrfToken() {
    const m = document.querySelector("input[name=csrfmiddlewaretoken]");
    return m ? m.value : "";
  }

  function postForm(action, fields) {
    const form = document.createElement("form");
    form.method = "post";
    form.action = action;
    form.style.display = "inline";
    const csrf = document.createElement("input");
    csrf.type = "hidden";
    csrf.name = "csrfmiddlewaretoken";
    csrf.value = csrfToken();
    form.appendChild(csrf);
    Object.keys(fields || {}).forEach(function (key) {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = key;
      input.value = fields[key];
      form.appendChild(input);
    });
    return form;
  }

  function actionButton(label, className, onSubmit) {
    const btn = document.createElement("button");
    btn.type = "submit";
    if (className) btn.className = className;
    btn.textContent = label;
    if (onSubmit) {
      btn.addEventListener("click", function (ev) {
        if (!onSubmit()) ev.preventDefault();
      });
    }
    return btn;
  }

  function statusPill(status) {
    if (window.PeonUI && window.PeonUI.statusPill) {
      return window.PeonUI.statusPill(status);
    }
    const pill = document.createElement("span");
    pill.className = "status-pill";
    pill.dataset.status = status || "";
    pill.textContent = status || "";
    return pill;
  }

  function badgeForType(typ) {
    const t = String(typ || "log").toLowerCase();
    if (t === "tool") return "TOOL";
    if (t === "error" || t === "stderr") return "ERR";
    if (t === "status") return "STATUS";
    if (t === "result") return "RESULT";
    if (t === "steer") return "STEER";
    return "LOG";
  }

  function formatTime(iso) {
    if (window.PeonUI && window.PeonUI.formatIsoTime) {
      return window.PeonUI.formatIsoTime(iso, { timeOnly: true, empty: "" });
    }
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function flattenAgents(agents) {
    const out = [];
    (agents || []).forEach(function (a) {
      out.push(a);
      (a.subagents || []).forEach(function (s) {
        out.push(s);
      });
    });
    return out;
  }

  function pickDefaultAgent(agents) {
    const flat = flattenAgents(agents);
    const running = flat.find(function (a) {
      return a.status === "running" || a.status === "paused";
    });
    if (running) return running;
    if (flat.length) return flat[0];
    return null;
  }

  function rerunJob(projectPk, agent, command) {
    const id = agent && agent.id;
    if (!id) return Promise.resolve(null);
    const url = "/projects/" + projectPk + "/jobs/" + id + "/start/";
    return fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": csrfToken(),
      },
      body: JSON.stringify({ command: (command || "").trim() }),
    })
      .then(function (res) {
        return res.json().then(function (data) {
          return { ok: res.ok, data: data };
        });
      })
      .then(function (out) {
        if (!out.ok || !out.data || !out.data.ok) {
          if (window.PeonUI) {
            window.PeonUI.toast(
              (out.data && out.data.error) || "Could not re-run job",
              "error"
            );
          }
          return null;
        }
        if (window.PeonUI) {
          window.PeonUI.toast("Re-run queued: " + (agent.title || id), "ok");
        }
        return out.data;
      })
      .catch(function () {
        if (window.PeonUI) window.PeonUI.toast("Could not re-run job", "error");
        return null;
      });
  }

  function commandEditorValue() {
    const ta = el("agent-command-input");
    return ta ? (ta.value || "").trim() : "";
  }

  function looksLikeAgentCommand(text) {
    const raw = String(text || "").trim();
    if (!raw) return false;
    const low = raw.toLowerCase();
    if (
      low.indexOf("execute project") === 0 ||
      low.indexOf("operator ") === 0 ||
      low.indexOf("obj-") === 0
    ) {
      return false;
    }
    if (raw.indexOf("\n") >= 0 && low.indexOf("acceptance criteria") >= 0) {
      return false;
    }
    if (low.indexOf("run_skill_script") >= 0 || low.indexOf("sandbox_setup") >= 0) {
      return true;
    }
    const first = (low.split(/\s+/)[0] || "").replace(/[^a-z0-9._/-]/g, "");
    return (
      first === "nmap" ||
      first === "httpx" ||
      first === "curl" ||
      first === "dig" ||
      first.indexOf("/") >= 0 ||
      /\.py$/.test(first)
    );
  }

  function unwrapSkillCommand(text) {
    const raw = String(text || "").trim();
    const m = raw.match(
      /run_skill_script\s*\([^)]*command\s*=\s*(['"])([\s\S]*?)\1/i
    );
    if (m && m[2]) return String(m[2]).trim();
    return raw;
  }

  function agentCommandSeed(agent) {
    if (!agent) return "";
    const candidates = []
      .concat(agent.operator_command || "")
      .concat(agent.last_command || "")
      .concat(agent.commands || []);
    for (let i = 0; i < candidates.length; i++) {
      const c = candidates[i];
      if (looksLikeAgentCommand(c)) return unwrapSkillCommand(c);
    }
    return "";
  }

  function focusCommandEditor(seed) {
    const panel = el("agent-context");
    if (panel) {
      panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
      panel.classList.add("context-editing");
    }
    const ta = el("agent-command-input");
    if (!ta) return;
    if (seed != null && String(seed).trim()) ta.value = String(seed).trim();
    ta.focus();
    ta.select();
  }

  function hideCommandEditDialog() {
    const dlg = el("agent-command-dialog");
    if (dlg) dlg.hidden = true;
  }

  function openCommandEditDialog(projectPk, agent, onSelect) {
    let dlg = el("agent-command-dialog");
    if (!dlg) {
      dlg = document.createElement("div");
      dlg.id = "agent-command-dialog";
      dlg.className = "agent-command-dialog";
      dlg.setAttribute("role", "dialog");
      dlg.setAttribute("aria-modal", "true");
      dlg.setAttribute("aria-labelledby", "agent-command-dialog-title");
      dlg.innerHTML =
        '<div class="agent-command-dialog-card">' +
        '<h3 id="agent-command-dialog-title">Edit agent command</h3>' +
        '<p class="meta agent-command-dialog-sub"></p>' +
        '<label class="meta" for="agent-command-dialog-input">Emitted command (e.g. nmap …)</label>' +
        '<textarea id="agent-command-dialog-input" rows="5" ' +
        'placeholder="e.g. nmap -sT --top-ports 100 8.8.8.8"></textarea>' +
        '<div class="agent-command-dialog-actions">' +
        '<button type="button" class="secondary" data-act="cancel">Cancel</button>' +
        '<button type="button" class="secondary" data-act="apply">Apply to context</button>' +
        '<button type="button" data-act="rerun">Save &amp; Re-run</button>' +
        "</div></div>";
      document.body.appendChild(dlg);
      dlg.addEventListener("click", function (ev) {
        if (ev.target === dlg) hideCommandEditDialog();
      });
      document.addEventListener("keydown", function (ev) {
        if (ev.key === "Escape" && dlg && !dlg.hidden) {
          hideCommandEditDialog();
        }
      });
    }
    const sub = dlg.querySelector(".agent-command-dialog-sub");
    const input = dlg.querySelector("#agent-command-dialog-input");
    if (sub) sub.textContent = agent.title || String(agent.id || "");
    if (input) {
      input.value = agentCommandSeed(agent);
    }
    const bind = function (act, fn) {
      const btn = dlg.querySelector('[data-act="' + act + '"]');
      if (!btn) return;
      btn.onclick = function (ev) {
        ev.preventDefault();
        fn();
      };
    };
    bind("cancel", hideCommandEditDialog);
    bind("apply", function () {
      const cmd = (input && input.value) || "";
      if (onSelect) onSelect(agent);
      window.requestAnimationFrame(function () {
        focusCommandEditor(cmd);
        hideCommandEditDialog();
      });
    });
    bind("rerun", function () {
      const cmd = ((input && input.value) || "").trim();
      hideCommandEditDialog();
      if (onSelect) onSelect(agent);
      window.requestAnimationFrame(function () {
        focusCommandEditor(cmd);
        rerunJob(projectPk, agent, cmd);
      });
    });
    dlg.hidden = false;
    if (input) {
      input.focus();
      input.select();
    }
  }

  function fillContextActions(container, agent, projectPk) {
    if (!container) return;
    container.replaceChildren();
    if (!agent) return;
    const base = "/projects/" + projectPk + "/jobs/" + agent.id;
    const status = String(agent.status || "");
    const terminal =
      !!agent.terminal ||
      ["completed", "failed", "cancelled"].indexOf(status) >= 0;

    if (status === "pending" || terminal) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "secondary";
      btn.textContent = "Re-run";
      btn.addEventListener("click", function () {
        rerunJob(projectPk, agent, commandEditorValue());
      });
      container.appendChild(btn);
    }
    if (status === "running" || status === "pending") {
      const pause = postForm(base + "/steer/", { action: "pause", next: "project" });
      pause.appendChild(actionButton("Pause", "secondary"));
      container.appendChild(pause);
    }
    if (status === "paused") {
      const resume = postForm(base + "/steer/", { action: "resume", next: "project" });
      resume.appendChild(actionButton("Resume", "secondary"));
      container.appendChild(resume);
    }
    if (!terminal) {
      const kill = postForm(base + "/steer/", { action: "kill", next: "project" });
      kill.appendChild(
        actionButton("Kill", "", function () {
          return confirm("Kill agent " + (agent.title || agent.id) + "?");
        })
      );
      container.appendChild(kill);
    }
    const live = document.createElement("a");
    live.className = "btn secondary";
    live.href = base + "/";
    live.textContent = "Open live";
    container.appendChild(live);
  }

  function renderContext(agent, latestByJob) {
    const body = el("context-body");
    const actions = el("context-actions");
    if (!body) return;
    body.replaceChildren();
    if (!agent) {
      const p = document.createElement("p");
      p.className = "meta";
      p.textContent = "Select an agent in the graph.";
      body.appendChild(p);
      if (actions) actions.replaceChildren();
      return;
    }

    const head = document.createElement("div");
    head.className = "context-head";
    const name = document.createElement("strong");
    name.textContent = agent.title || agent.id;
    head.appendChild(name);
    head.appendChild(statusPill(agent.status));
    const role = document.createElement("span");
    role.className = "meta context-role";
    role.textContent = agent.role || "ROOT";
    head.appendChild(role);
    body.appendChild(head);

    if (agent.skill_names && agent.skill_names.length) {
      const skills = document.createElement("div");
      skills.className = "chip-row context-skills";
      agent.skill_names.forEach(function (s) {
        const chip = document.createElement("span");
        chip.className = "chip";
        chip.textContent = s;
        skills.appendChild(chip);
      });
      body.appendChild(skills);
    }

    if (agent.objective_title) {
      const obj = document.createElement("div");
      obj.className = "context-objective";
      const label = document.createElement("div");
      label.className = "meta";
      label.textContent =
        "OBJ-" +
        (agent.objective_seq != null ? agent.objective_seq : "?") +
        " · " +
        (agent.objective_phase || "");
      obj.appendChild(label);
      const title = document.createElement("div");
      title.className = "context-obj-title";
      title.textContent = agent.objective_title;
      obj.appendChild(title);
      if (agent.objective_description) {
        const desc = document.createElement("p");
        desc.className = "context-action-text";
        desc.textContent = agent.objective_description;
        obj.appendChild(desc);
      }
      if (agent.objective_acceptance) {
        const acc = document.createElement("p");
        acc.className = "meta";
        acc.textContent = "Accept: " + agent.objective_acceptance;
        obj.appendChild(acc);
      }
      body.appendChild(obj);
    }

    const editBlock = document.createElement("div");
    editBlock.className = "context-command-edit";
    const editLabel = document.createElement("label");
    editLabel.className = "meta";
    editLabel.setAttribute("for", "agent-command-input");
    editLabel.textContent =
      "Agent command (edit then Re-run — no replan; not the objective brief)";
    editBlock.appendChild(editLabel);
    const ta = document.createElement("textarea");
    ta.id = "agent-command-input";
    ta.rows = 3;
    ta.placeholder = "e.g. nmap -sT --top-ports 100 8.8.8.8";
    ta.value = agentCommandSeed(agent);
    editBlock.appendChild(ta);
    body.appendChild(editBlock);

    const actionBlock = document.createElement("div");
    actionBlock.className = "context-current";
    const actionLabel = document.createElement("div");
    actionLabel.className = "meta context-current-label";
    actionLabel.textContent = "CURRENT ACTION";
    actionBlock.appendChild(actionLabel);
    const actionText = document.createElement("pre");
    actionText.className = "context-action-text";
    const latest = (latestByJob && latestByJob[agent.id]) || "";
    actionText.textContent =
      latest ||
      agent.objective_description ||
      agent.error ||
      "(waiting for stream…)";
    actionBlock.appendChild(actionText);
    body.appendChild(actionBlock);
  }

  function hideAgentMenu() {
    const menu = el("agent-context-menu");
    if (menu) menu.hidden = true;
  }

  function showAgentMenu(projectPk, agent, x, y, onEdit) {
    let menu = el("agent-context-menu");
    if (!menu) {
      menu = document.createElement("div");
      menu.id = "agent-context-menu";
      menu.className = "agent-context-menu";
      menu.setAttribute("role", "menu");
      document.body.appendChild(menu);
      document.addEventListener("click", function () {
        hideAgentMenu();
      });
      document.addEventListener("keydown", function (ev) {
        if (ev.key === "Escape") hideAgentMenu();
      });
    }
    menu.replaceChildren();
    const mk = function (label, fn) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "agent-context-menu-item";
      b.setAttribute("role", "menuitem");
      b.textContent = label;
      b.addEventListener("click", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        hideAgentMenu();
        fn();
      });
      menu.appendChild(b);
    };
    mk("Edit command", function () {
      if (onEdit) onEdit(agent);
      openCommandEditDialog(projectPk, agent, onEdit);
    });
    const status = String(agent.status || "");
    const terminal =
      !!agent.terminal ||
      ["completed", "failed", "cancelled"].indexOf(status) >= 0;
    if (status === "pending" || terminal) {
      mk("Re-run", function () {
        if (onEdit) onEdit(agent);
        const cmd =
          commandEditorValue() || agentCommandSeed(agent);
        rerunJob(projectPk, agent, cmd);
      });
    }
    mk("Open live", function () {
      window.location.href = "/projects/" + projectPk + "/jobs/" + agent.id + "/";
    });
    menu.hidden = false;
    const pad = 8;
    const w = menu.offsetWidth || 160;
    const h = menu.offsetHeight || 80;
    let left = x;
    let top = y;
    if (left + w > window.innerWidth - pad) left = window.innerWidth - w - pad;
    if (top + h > window.innerHeight - pad) top = window.innerHeight - h - pad;
    menu.style.left = Math.max(pad, left) + "px";
    menu.style.top = Math.max(pad, top) + "px";
  }

  function renderGraph(listEl, agents, projectPk, selectedId, onSelect, onContext) {
    if (window.PeonAgentGraph && typeof window.PeonAgentGraph.render === "function") {
      window.PeonAgentGraph.render(
        listEl,
        agents,
        projectPk,
        selectedId,
        onSelect,
        onContext || null
      );
      return;
    }
    if (!listEl) return;
    listEl.replaceChildren();
    var p = document.createElement("p");
    p.className = "meta";
    p.textContent = "Agent graph script failed to load.";
    listEl.appendChild(p);
  }

  function appendMessage(log, msg, latestByJob) {
    if (!log || !msg || msg.id == null) return;
    if (log.querySelector('[data-id="' + msg.id + '"]')) return;
    const text = String(msg.content || "").replace(/\n{3,}/g, "\n\n").trim();
    if (!text) return;
    const empty = log.querySelector(".empty");
    if (empty) empty.remove();

    if (msg.job_id && latestByJob) {
      latestByJob[String(msg.job_id)] = text;
    }

    const metaObj = msg.metadata || {};
    const role = String(metaObj.role || metaObj.chat_role || "").toLowerCase();
    const event = String(metaObj.event || "").toLowerCase();

    const article = document.createElement("article");
    const typ = String(msg.message_type || "log").replace(/[^\w-]/g, "");
    article.className = "stream-entry stream-" + typ;
    if (role === "user") article.classList.add("stream-user");
    if (event === "project_instruction" || event === "operator_guidance") {
      article.classList.add("stream-instruction");
    }
    article.dataset.id = String(msg.id);
    if (msg.job_id) article.dataset.jobId = String(msg.job_id);

    const meta = document.createElement("header");
    meta.className = "stream-meta";
    const time = document.createElement("span");
    time.className = "stream-time";
    time.textContent = formatTime(msg.created_at) || "·";
    meta.appendChild(time);
    const badge = document.createElement("span");
    let badgeText = badgeForType(typ);
    if (role === "user") badgeText = "YOU";
    else if (event === "operator_guidance") badgeText = "STEER";
    badge.className =
      "stream-badge stream-badge-" + badgeText.toLowerCase().replace(/[^\w-]/g, "");
    badge.textContent = badgeText;
    meta.appendChild(badge);
    const title = msg.job_title || msg.job_id || "";
    if (title) {
      const who = document.createElement("span");
      who.className = "stream-agent";
      who.textContent = title;
      meta.appendChild(who);
    }

    const body = document.createElement("pre");
    body.textContent = text;

    article.appendChild(meta);
    article.appendChild(body);
    log.appendChild(article);
    log.scrollTop = log.scrollHeight;
  }

  function updateSkills(skills) {
    const row = el("skills-used");
    if (!row) return;
    row.replaceChildren();
    if (!skills || !skills.length) {
      const empty = document.createElement("span");
      empty.className = "meta empty-skills";
      empty.textContent = "No skills queued yet.";
      row.appendChild(empty);
      return;
    }
    skills.forEach(function (s) {
      const name = typeof s === "string" ? s : s.name;
      const desc = typeof s === "string" ? "" : s.description || "";
      const cat = typeof s === "string" ? "" : s.category || "";
      const tags = typeof s === "string" ? [] : s.tags || [];
      const card = document.createElement("article");
      card.className = "skill-card";
      const n = document.createElement("div");
      n.className = "skill-card-name";
      n.textContent = name;
      card.appendChild(n);
      if (cat) {
        const c = document.createElement("div");
        c.className = "skill-card-cat";
        c.textContent = cat;
        card.appendChild(c);
      }
      if (desc) {
        const d = document.createElement("div");
        d.className = "skill-card-desc";
        d.textContent = desc;
        card.appendChild(d);
      }
      if (tags.length) {
        const tg = document.createElement("div");
        tg.className = "skill-card-tags";
        tags.forEach(function (t) {
          const chip = document.createElement("span");
          chip.className = "chip";
          chip.textContent = t;
          tg.appendChild(chip);
        });
        card.appendChild(tg);
      }
      row.appendChild(card);
    });
  }

  function updateObjectives(objectives) {
    const body = el("objectives-body");
    if (!body || !objectives) return;
    const count = el("objectives-count");
    if (count) count.textContent = String(objectives.length);
    objectives.forEach(function (o) {
      const row = body.querySelector('[data-objective-id="' + o.id + '"]');
      if (!row) return;
      const cell = row.querySelector("[data-objective-status]");
      if (cell) cell.textContent = o.status || "";
    });
  }

  function updateProgress(progress) {
    if (!progress) return;
    const fill = el("progress-fill");
    const label = el("progress-label");
    const pct = Math.round((Number(progress.ratio) || 0) * 100);
    if (fill) fill.style.width = pct + "%";
    if (label) {
      label.textContent =
        (progress.objectives_done || 0) +
        "/" +
        (progress.objectives_total || 0) +
        " objectives · " +
        (progress.jobs_done || 0) +
        "/" +
        (progress.jobs_total || 0) +
        " agents";
    }
  }

  function updateSandbox(sandbox) {
    if (!sandbox) return;
    const name = el("sandbox-name");
    const mode = el("sandbox-mode");
    if (name) name.textContent = sandbox.name || "";
    if (mode) mode.textContent = sandbox.mode || "";
  }

  function updateProjectStatus(project, projectPk) {
    if (!project) return;
    const status = String(project.status || "");
    const statusEl = el("project-status");
    if (statusEl) {
      statusEl.textContent = status;
      statusEl.dataset.status = status;
    }
    const phaseEl = el("project-phase");
    if (phaseEl) phaseEl.textContent = project.phase || "—";

    updateProgress(project.progress);
    updateSandbox(project.sandbox);

    const bar = el("project-control-bar");
    if (!bar) return;
    bar.dataset.projectStatus = status;

    let pauseForm = bar.querySelector('[data-control="pause"]');
    let resumeForm = bar.querySelector('[data-control="resume"]');
    const controlUrl = "/projects/" + projectPk + "/control/";

    if (status === "active") {
      if (resumeForm) resumeForm.remove();
      if (!pauseForm) {
        pauseForm = postForm(controlUrl, { action: "pause" });
        pauseForm.setAttribute("data-control", "pause");
        pauseForm.appendChild(actionButton("Pause project", "secondary"));
        bar.insertBefore(pauseForm, bar.firstChild);
      }
    } else if (status === "paused") {
      if (pauseForm) pauseForm.remove();
      if (!resumeForm) {
        resumeForm = postForm(controlUrl, { action: "resume" });
        resumeForm.setAttribute("data-control", "resume");
        resumeForm.appendChild(actionButton("Resume project", "secondary"));
        bar.insertBefore(resumeForm, bar.firstChild);
      }
    } else {
      if (pauseForm) pauseForm.remove();
      if (resumeForm) resumeForm.remove();
    }
  }

  function updateRunNext(ready) {
    const btn = el("run-next-objective");
    if (!btn) return;
    const on = !!ready;
    btn.disabled = !on;
    btn.title = on ? "" : "No ready next objective";
  }

  function kindLabel(kind) {
    if (kind === "final") return "Final";
    if (kind === "plan") return "Plan";
    return "Finding";
  }

  function formatReportTime(iso) {
    if (window.PeonUI && window.PeonUI.formatIsoTime) {
      return window.PeonUI.formatIsoTime(iso, { compact: true, empty: "—" });
    }
    if (!iso) return "—";
    return String(iso);
  }

  function updateReports(reports, projectPk) {
    const wrap = el("reports-body-wrap");
    const cta = el("reports-final-cta");
    if (!wrap) return;
    const rows = reports || [];
    let body = el("reports-body");
    const empty = el("reports-empty");

    if (!rows.length) {
      if (cta) cta.replaceChildren();
      if (body) body.replaceChildren();
      if (empty) {
        empty.hidden = false;
      } else {
        wrap.innerHTML =
          '<p class="meta" id="reports-empty">No reports yet — the analyzer writes <code>findings/report.md</code> when reporting runs.</p>' +
          '<table hidden id="reports-table-live"><thead><tr><th>Report</th><th>Type</th><th>Modified</th><th>Size</th><th></th></tr></thead><tbody id="reports-body"></tbody></table>';
      }
      return;
    }

    if (empty) empty.hidden = true;
    let table = wrap.querySelector("table");
    if (!table || table.hidden) {
      wrap.innerHTML =
        '<table><thead><tr><th>Report</th><th>Type</th><th>Modified</th><th>Size</th><th></th></tr></thead><tbody id="reports-body"></tbody></table>';
      body = el("reports-body");
    } else if (!body) {
      body = document.createElement("tbody");
      body.id = "reports-body";
      table.appendChild(body);
    }

    const sig = rows
      .map(function (r) {
        return r.relative_path + ":" + (r.modified_at || "") + ":" + r.size;
      })
      .join("|");
    if (wrap.dataset.sig === sig) return;
    wrap.dataset.sig = sig;

    body.replaceChildren();
    let finalHref = "";
    rows.forEach(function (r) {
      const tr = document.createElement("tr");
      if (r.kind === "final") {
        tr.className = "report-final";
        finalHref = "/projects/" + projectPk + "/reports/" + r.relative_path + "/view/";
      }
      tr.dataset.reportPath = r.relative_path;
      const viewBase = "/projects/" + projectPk + "/reports/" + r.relative_path;
      const nameLink = document.createElement("a");
      nameLink.href = r.is_markdown ? viewBase + "/view/" : viewBase + "?view=1";
      if (!r.is_markdown) {
        nameLink.target = "_blank";
        nameLink.rel = "noopener";
      }
      nameLink.textContent = r.display_name || r.name;
      const pathMeta = document.createElement("div");
      pathMeta.className = "meta";
      const code = document.createElement("code");
      code.textContent = r.relative_path;
      pathMeta.appendChild(code);
      const tdName = document.createElement("td");
      tdName.appendChild(nameLink);
      tdName.appendChild(pathMeta);
      const tdKind = document.createElement("td");
      tdKind.className = "meta status";
      tdKind.textContent = kindLabel(r.kind);
      const tdMod = document.createElement("td");
      tdMod.className = "meta";
      tdMod.textContent = formatReportTime(r.modified_at);
      const tdSize = document.createElement("td");
      tdSize.className = "meta";
      tdSize.textContent = (r.size || 0) + " B";
      const tdAct = document.createElement("td");
      if (r.is_markdown) {
        const view = document.createElement("a");
        view.className = "btn secondary";
        view.href = viewBase + "/view/";
        view.textContent = "View";
        tdAct.appendChild(view);
      }
      const dl = document.createElement("a");
      dl.className = "btn secondary";
      dl.href = viewBase;
      dl.textContent = "Download";
      tdAct.appendChild(dl);
      tr.appendChild(tdName);
      tr.appendChild(tdKind);
      tr.appendChild(tdMod);
      tr.appendChild(tdSize);
      tr.appendChild(tdAct);
      body.appendChild(tr);
    });

    if (cta) {
      cta.replaceChildren();
      if (finalHref) {
        const a = document.createElement("a");
        a.className = "btn";
        a.href = finalHref;
        a.textContent = "Open final report";
        cta.appendChild(a);
      }
    }
  }

  function updateFindingsCounts(counts) {
    const hint = el("findings-counts");
    if (!hint || !counts) return;
    hint.textContent =
      (counts.open || 0) +
      " open · " +
      (counts.confirmed || 0) +
      " confirmed · " +
      (counts.all || 0) +
      " total";
  }

  function renderFindingsRows(findings, statusChoices, projectPk) {
    const body = el("findings-body");
    if (!body) return;
    body.replaceChildren();
    const rows = findings || [];
    if (!rows.length) {
      const tr = document.createElement("tr");
      tr.className = "findings-empty-row";
      const td = document.createElement("td");
      td.colSpan = 6;
      td.className = "meta findings-empty";
      td.textContent = "No findings match this filter.";
      tr.appendChild(td);
      body.appendChild(tr);
      return;
    }
    rows.forEach(function (f) {
      const tr = document.createElement("tr");
      tr.dataset.findingId = f.id;
      tr.dataset.status = f.status || "";
      tr.dataset.severity = f.severity || "";
      const tdSeq = document.createElement("td");
      tdSeq.className = "meta";
      tdSeq.textContent = "FIND-" + f.seq;
      const tdTitle = document.createElement("td");
      const strong = document.createElement("strong");
      strong.textContent = f.title || "";
      tdTitle.appendChild(strong);
      if (f.host) {
        const host = document.createElement("div");
        host.className = "meta";
        host.textContent = f.host + (f.port ? ":" + f.port : "");
        tdTitle.appendChild(host);
      }
      if (f.evidence) {
        const ev = document.createElement("div");
        ev.className = "meta finding-evidence";
        ev.textContent = String(f.evidence).slice(0, 120);
        tdTitle.appendChild(ev);
      }
      const tdSev = document.createElement("td");
      const sev = document.createElement("span");
      sev.className = "sev-pill";
      sev.dataset.severity = f.severity || "";
      sev.textContent = f.severity || "";
      tdSev.appendChild(sev);
      const tdKind = document.createElement("td");
      tdKind.className = "meta";
      const kindCode = document.createElement("code");
      kindCode.textContent = f.kind || "";
      tdKind.appendChild(kindCode);
      const tdAsset = document.createElement("td");
      tdAsset.className = "meta";
      if (f.asset_type) {
        const ac = document.createElement("code");
        ac.textContent = f.asset_type;
        tdAsset.appendChild(ac);
      } else {
        tdAsset.textContent = "—";
      }
      const tdStatus = document.createElement("td");
      tdStatus.className = "finding-status-cell";
      const sel = document.createElement("select");
      sel.className = "finding-status-select";
      sel.setAttribute("aria-label", "Finding status");
      sel.dataset.findingId = f.id;
      sel.dataset.prev = f.status || "open";
      (statusChoices || []).forEach(function (opt) {
        const o = document.createElement("option");
        o.value = opt.value;
        o.textContent = opt.label;
        if (opt.value === f.status) o.selected = true;
        sel.appendChild(o);
      });
      tdStatus.appendChild(sel);
      tr.appendChild(tdSeq);
      tr.appendChild(tdTitle);
      tr.appendChild(tdSev);
      tr.appendChild(tdKind);
      tr.appendChild(tdAsset);
      tr.appendChild(tdStatus);
      body.appendChild(tr);
    });
  }

  function bindFindingsBoard(projectPk, statusChoices) {
    const filter = el("findings-filter");
    const body = el("findings-body");

    async function reloadFindings() {
      const statusEl = el("finding_status");
      const sevEl = el("finding_severity");
      const qs =
        "?finding_status=" +
        encodeURIComponent((statusEl && statusEl.value) || "all") +
        "&finding_severity=" +
        encodeURIComponent((sevEl && sevEl.value) || "all");
      try {
        const res = await fetch("/projects/" + projectPk + "/findings.json" + qs, {
          headers: { Accept: "application/json" },
        });
        if (!res.ok) return;
        const data = await res.json();
        renderFindingsRows(data.findings, statusChoices, projectPk);
        updateFindingsCounts(data.counts);
      } catch (_) {}
    }

    if (filter) {
      filter.addEventListener("change", function (ev) {
        if (ev.target && ev.target.matches("select")) {
          ev.preventDefault();
          reloadFindings();
        }
      });
    }

    if (body) {
      body.addEventListener("change", async function (ev) {
        const sel = ev.target;
        if (!sel || !sel.classList.contains("finding-status-select")) return;
        const findingId = sel.dataset.findingId;
        const next = sel.value;
        const prev = sel.dataset.prev || "";
        if (!findingId || next === prev) return;
        const fd = new FormData();
        fd.append("csrfmiddlewaretoken", csrfToken());
        fd.append("status", next);
        try {
          const res = await fetch(
            "/projects/" + projectPk + "/findings/" + findingId + "/triage/",
            {
              method: "POST",
              headers: {
                Accept: "application/json",
                "X-Requested-With": "XMLHttpRequest",
              },
              body: fd,
            }
          );
          const data = await res.json().catch(function () {
            return null;
          });
          if (!res.ok || !data || !data.ok) {
            sel.value = prev;
            if (window.PeonUI) {
              window.PeonUI.toast(
                (data && data.error) || "Could not update status",
                "error"
              );
            }
            return;
          }
          sel.dataset.prev = data.status;
          const tr = sel.closest("tr");
          if (tr) tr.dataset.status = data.status;
          updateFindingsCounts(data.counts);
          if (window.PeonUI) {
            window.PeonUI.toast("FIND-" + data.seq + " → " + data.status, "ok");
          }
          const filterStatus = el("finding_status");
          if (
            filterStatus &&
            filterStatus.value !== "all" &&
            filterStatus.value !== data.status
          ) {
            reloadFindings();
          }
        } catch (_) {
          sel.value = prev;
        }
      });
    }
  }

  window.PeonProjectOps = {
    init: function (opts) {
      const log = el("project-stream-log");
      const agentsList = el("agents-list");
      const projectPk = opts.projectPk;
      let lastId = 0;
      let lastSig = "";
      let lastProjectStatus = "";
      let selectedId = "";
      let agentsCache = [];
      const knownAgentStatus = {};
      const latestByJob = {};

      function selectAgent(agent) {
        if (!agent) return;
        selectedId = String(agent.id);
        renderGraph(
          agentsList,
          agentsCache,
          projectPk,
          selectedId,
          selectAgent,
          onAgentContext
        );
        renderContext(agent, latestByJob);
        fillContextActions(el("context-actions"), agent, projectPk);
      }

      function onAgentContext(agent, x, y) {
        selectAgent(agent);
        showAgentMenu(projectPk, agent, x, y, selectAgent);
      }

      (opts.bootstrap || []).forEach(function (m) {
        appendMessage(log, m, latestByJob);
        if (m.id > lastId) lastId = m.id;
      });

      function toast(msg, kind) {
        if (window.PeonUI && typeof window.PeonUI.toast === "function") {
          window.PeonUI.toast(msg, kind);
        }
      }

      async function pollMessages() {
        try {
          const res = await fetch(
            "/projects/" + projectPk + "/messages.json?after=" + lastId,
            { headers: { Accept: "application/json" } }
          );
          if (!res.ok) return;
          const data = await res.json();
          let touched = false;
          let graphDirty = false;
          (data.messages || []).forEach(function (m) {
            appendMessage(log, m, latestByJob);
            if (m.id > lastId) lastId = m.id;
            if (m.job_id && String(m.job_id) === selectedId) touched = true;
            if (
              m.job_id &&
              String(m.message_type || "").toLowerCase() === "tool"
            ) {
              flattenAgents(agentsCache).forEach(function (a) {
                if (String(a.id) === String(m.job_id)) {
                  var text = String(m.content || "").trim();
                  if (text) {
                    a.last_command = text;
                    a.commands = [text].concat(a.commands || []).slice(0, 4);
                    a.tool_calls = Number(a.tool_calls || 0) + 1;
                    graphDirty = true;
                  }
                }
              });
            }
          });
          if (graphDirty) {
            renderGraph(
              agentsList,
              agentsCache,
              projectPk,
              selectedId,
              selectAgent,
              onAgentContext
            );
          }
          if (touched) {
            const cur = flattenAgents(agentsCache).find(function (a) {
              return String(a.id) === selectedId;
            });
            if (cur) renderContext(cur, latestByJob);
          }
        } catch (_) {}
      }

      async function pollJobs() {
        try {
          const res = await fetch("/projects/" + projectPk + "/jobs.json", {
            headers: { Accept: "application/json" },
          });
          if (!res.ok) return;
          const data = await res.json();
          const project = data.project || {};
          const agents = data.agents || [];
          const objectives = data.objectives || [];
          const skills = data.skills_used || [];
          const status = project.status || "";
          if (lastProjectStatus && lastProjectStatus !== status) {
            if (
              status === "finished" ||
              status === "finished_with_errors" ||
              status === "cancelled"
            ) {
              toast(
                "Project → " + status,
                status === "finished" ? "ok" : "warn"
              );
            }
          }
          lastProjectStatus = status;

          function walkAgents(list) {
            (list || []).forEach(function (a) {
              const prev = knownAgentStatus[a.id];
              if (prev && prev !== a.status) {
                if (a.status === "failed") {
                  toast((a.title || "Agent") + " failed", "error");
                } else if (a.status === "completed") {
                  toast((a.title || "Agent") + " completed", "ok");
                }
              }
              knownAgentStatus[a.id] = a.status;
              walkAgents(a.subagents);
            });
          }
          walkAgents(agents);

          const reports = data.reports || [];
          const nextReady = data.next_objective_ready;
          updateRunNext(nextReady);
          // Reports can appear without agent status changes — always sync.
          updateReports(reports, projectPk);

          const sig = [
            status,
            project.phase || "",
            JSON.stringify(project.progress || {}),
            (project.sandbox && project.sandbox.name) || "",
            agents
              .map(function (a) {
                return (
                  a.id +
                  ":" +
                  a.status +
                  ":" +
                  (a.updated_at || "") +
                  ":" +
                  (a.subagents || [])
                    .map(function (s) {
                      return s.id + ":" + s.status;
                    })
                    .join(",")
                );
              })
              .join("|"),
            JSON.stringify(skills),
            objectives
              .map(function (o) {
                return o.id + ":" + o.status;
              })
              .join("|"),
            selectedId,
            String(!!nextReady),
          ].join("||");
          if (sig === lastSig) return;
          lastSig = sig;
          agentsCache = agents;

          let selected = flattenAgents(agents).find(function (a) {
            return String(a.id) === selectedId;
          });
          if (!selected) {
            selected = pickDefaultAgent(agents);
            selectedId = selected ? String(selected.id) : "";
          }
          renderGraph(
            agentsList,
            agents,
            projectPk,
            selectedId,
            selectAgent,
            onAgentContext
          );
          renderContext(selected, latestByJob);
          fillContextActions(el("context-actions"), selected, projectPk);
          updateObjectives(objectives);
          updateSkills(skills);
          updateProjectStatus(project, projectPk);
        } catch (_) {}
      }

      setInterval(pollMessages, 1000);
      setInterval(pollJobs, 2000);
      pollMessages();
      pollJobs();
      bindFindingsBoard(projectPk, opts.findingStatuses || []);
      bindProjectChat(projectPk);
      bindProjectFiles(projectPk);
    },
  };

  function formatBytes(n) {
    const v = Number(n) || 0;
    if (v < 1024) return v + " B";
    if (v < 1024 * 1024) return (v / 1024).toFixed(1) + " KiB";
    return (v / (1024 * 1024)).toFixed(1) + " MiB";
  }

  function renderProjectFilesList(inputs) {
    const list = el("project-files-list");
    if (!list) return;
    const rows = Array.isArray(inputs) ? inputs : [];
    if (!rows.length) {
      list.innerHTML =
        '<li class="meta project-files-empty" id="project-files-empty">' +
        "No files uploaded yet — use + in Live activity.</li>";
      return;
    }
    list.innerHTML = rows
      .map(function (f) {
        const name = String(f.name || "");
        const path = String(f.sandbox_path || "");
        const size = formatBytes(f.size);
        return (
          '<li class="project-file-row" data-name="' +
          escapeAttr(name) +
          '"><div class="project-file-meta"><code class="project-file-name">' +
          escapeHtml(name) +
          '</code><span class="meta">' +
          escapeHtml(size) +
          ' · <code>' +
          escapeHtml(path) +
          "</code></span></div>" +
          '<button type="button" class="secondary project-file-remove" data-name="' +
          escapeAttr(name) +
          '" aria-label="Remove ' +
          escapeAttr(name) +
          '">Remove</button></li>'
        );
      })
      .join("");
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  function bindProjectFiles(projectPk) {
    const panel = el("project-files");
    const form = el("project-chat-form");
    const fileInput = el("project-chat-file");
    const plus = el("project-chat-plus");
    if ((!panel && !fileInput) || (panel && panel.dataset.boundFiles)) return;
    if (panel) panel.dataset.boundFiles = "1";
    else if (form) form.dataset.boundFiles = "1";

    const uploadUrl =
      (form && form.getAttribute("data-inputs-url")) ||
      (panel && panel.getAttribute("data-inputs-url")) ||
      "/projects/" + projectPk + "/inputs/";
    const deleteUrl =
      (panel && panel.getAttribute("data-inputs-delete-url")) ||
      "/projects/" + projectPk + "/inputs/delete/";

    async function uploadFiles(fileList) {
      const files = Array.from(fileList || []);
      if (!files.length) return;
      const body = new FormData();
      files.forEach(function (f) {
        body.append("uploads", f);
      });
      try {
        const res = await fetch(uploadUrl, {
          method: "POST",
          headers: {
            Accept: "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": csrfToken(),
          },
          body: body,
        });
        const data = await res.json().catch(function () {
          return null;
        });
        if (!res.ok || !data || !data.ok) {
          const err =
            (data && data.errors && data.errors.join("; ")) ||
            (data && data.error) ||
            "Upload failed";
          if (window.PeonUI) window.PeonUI.toast(err, "error");
          return;
        }
        renderProjectFilesList(data.inputs || []);
        if (window.PeonUI) {
          window.PeonUI.toast(
            "Uploaded " + ((data.saved && data.saved.length) || 0) + " file(s)",
            "ok"
          );
        }
      } catch (_) {
        if (window.PeonUI) window.PeonUI.toast("Upload failed", "error");
      }
    }

    async function removeFile(name) {
      if (!name) return;
      try {
        const res = await fetch(deleteUrl, {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": csrfToken(),
          },
          body: JSON.stringify({ name: name }),
        });
        const data = await res.json().catch(function () {
          return null;
        });
        if (!res.ok || !data || !data.ok) {
          if (window.PeonUI) {
            window.PeonUI.toast(
              (data && data.error) || "Could not remove file",
              "error"
            );
          }
          return;
        }
        renderProjectFilesList(data.inputs || []);
        if (window.PeonUI) window.PeonUI.toast("Removed " + name, "ok");
      } catch (_) {
        if (window.PeonUI) window.PeonUI.toast("Could not remove file", "error");
      }
    }

    if (plus && fileInput) {
      plus.addEventListener("click", function () {
        if (plus.disabled || fileInput.disabled) return;
        fileInput.click();
      });
    }
    if (fileInput) {
      fileInput.addEventListener("change", function () {
        const files = fileInput.files;
        uploadFiles(files).finally(function () {
          fileInput.value = "";
        });
      });
    }

    if (panel) {
      panel.addEventListener("click", function (ev) {
        const btn = ev.target.closest(".project-file-remove");
        if (!btn || !panel.contains(btn)) return;
        const name = btn.getAttribute("data-name") || "";
        if (!name) return;
        if (!window.confirm("Remove " + name + " from this project?")) return;
        removeFile(name);
      });
    }
  }

  function bindProjectChat(projectPk) {
    const form = el("project-chat-form");
    const input = el("project-chat-input");
    const send = el("project-chat-send");
    if (!form || !input || form.dataset.boundChat) return;
    form.dataset.boundChat = "1";
    const url = form.getAttribute("data-chat-url") || "/projects/" + projectPk + "/chat/";

    function setBusy(busy) {
      input.disabled = !!busy || form.dataset.disabled === "1";
      if (send) send.disabled = !!busy || form.dataset.disabled === "1";
    }

    async function submit() {
      const message = (input.value || "").trim();
      if (!message || form.dataset.disabled === "1") return;
      setBusy(true);
      try {
        const res = await fetch(url, {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": csrfToken(),
          },
          body: JSON.stringify({ message: message }),
        });
        const data = await res.json().catch(function () {
          return null;
        });
        if (!res.ok || !data || !data.ok) {
          if (window.PeonUI) {
            window.PeonUI.toast(
              (data && data.error) || "Could not replan project",
              "error"
            );
          }
          return;
        }
        input.value = "";
        if (window.PeonUI) {
          window.PeonUI.toast(
            "Replanned — " +
              (data.objectives != null ? data.objectives : "?") +
              " objectives" +
              (data.primary_job_id ? "; next " + data.primary_job_id.slice(0, 8) : ""),
            "ok"
          );
        }
      } catch (_) {
        if (window.PeonUI) window.PeonUI.toast("Could not replan project", "error");
      } finally {
        setBusy(false);
        input.focus();
      }
    }

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      submit();
    });
    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        submit();
      }
    });
  }
})();
