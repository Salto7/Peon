/* Project ops console: agent graph, context, skills cards, live feed. */
(function () {
  function el(id) {
    return document.getElementById(id);
  }

  function csrfToken() {
    return (window.PeonUI && window.PeonUI.csrfToken && window.PeonUI.csrfToken()) || "";
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

  function badgeForType(typ, meta) {
    meta = meta || {};
    var tag = String(meta.tag || meta.feed_tag || "").toLowerCase();
    var event = String(meta.event || "").toLowerCase();
    var role = String(meta.role || meta.chat_role || "").toLowerCase();
    if (role === "user" || tag === "you") return "YOU";
    if (tag === "need" || event === "need_input") return "NEED";
    if (tag === "stop" || event === "operator_stop") return "STOP";
    if (tag === "ask" || event === "console_ask_reply" || event === "console_ask")
      return "ASK";
    if (
      tag === "steer" ||
      event === "operator_guidance" ||
      event === "project_instruction"
    )
      return "STEER";
    if (tag === "find" || tag === "finding" || event.indexOf("finding") >= 0)
      return "FIND";
    if (tag === "net" || tag === "host" || event.indexOf("host") >= 0) return "NET";
    if (tag === "shell" || tag === "terminal" || event === "terminal") return "SHELL";
    if (tag === "loot") return "LOOT";
    var t = String(typ || "log").toLowerCase();
    if (t === "tool") return "TOOL";
    if (t === "error" || t === "stderr") return "ERR";
    if (t === "status") return "STATUS";
    if (t === "result") return "RESULT";
    if (t === "thinking") return "THINK";
    if (t === "steer") return "STEER";
    return "LOG";
  }

  var feedFilter = "all";

  function feedFilterKey(badge) {
    var b = String(badge || "").toUpperCase();
    if (b === "TOOL") return "tool";
    if (b === "FIND") return "find";
    if (b === "ERR") return "err";
    if (b === "STEER" || b === "YOU" || b === "NEED" || b === "STOP") return "steer";
    if (b === "SHELL") return "shell";
    return "other";
  }

  function applyFeedFilter() {
    var log = el("project-stream-log");
    if (!log) return;
    var entries = log.querySelectorAll(".stream-entry");
    entries.forEach(function (article) {
      var key = article.dataset.feedKey || "other";
      var show = feedFilter === "all" || key === feedFilter;
      article.hidden = !show;
    });
  }

  function bindFeedFilters() {
    var bar = el("feed-filters");
    if (!bar || bar.dataset.bound) return;
    bar.dataset.bound = "1";
    bar.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-feed-filter]");
      if (!btn || !bar.contains(btn)) return;
      feedFilter = btn.getAttribute("data-feed-filter") || "all";
      bar.querySelectorAll(".feed-filter").forEach(function (b) {
        b.classList.toggle("is-active", b === btn);
      });
      applyFeedFilter();
    });
  }

  function focusChat() {
    var chat = el("war-chat");
    var input = el("project-chat-input");
    if (chat) chat.scrollIntoView({ block: "nearest", behavior: "smooth" });
    if (input) input.focus();
  }

  function revealShellCard() {
    var shell = document.querySelector(".console-workspace");
    if (shell) {
      shell.classList.remove("is-collapsed");
      shell.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  function bindWorkspaceTabs() {
    var activators = {};
    document.querySelectorAll("[data-tabset]").forEach(function (root) {
      if (root.dataset.boundTabs) return;
      root.dataset.boundTabs = "1";
      var tabs = root.querySelectorAll(":scope > .workspace-header [data-workspace-tab]");
      if (!tabs.length) tabs = root.querySelectorAll("[data-workspace-tab]");
      var panes = root.querySelectorAll(":scope > [data-workspace-pane]");
      if (!panes.length) panes = root.querySelectorAll("[data-workspace-pane]");
      var setName = root.getAttribute("data-tabset") || root.id || "main";
      var storageKey =
        "peon.tabset." + setName + "." + (root.dataset.pk || "");
      function activate(name) {
        tabs.forEach(function (t) {
          var on = t.getAttribute("data-workspace-tab") === name;
          t.classList.toggle("is-active", on);
          t.setAttribute("aria-selected", on ? "true" : "false");
        });
        panes.forEach(function (p) {
          var on = p.getAttribute("data-workspace-pane") === name;
          p.classList.toggle("is-active", on);
          p.hidden = !on;
        });
        window.dispatchEvent(
          new CustomEvent("peon:workspace-tab", {
            detail: { tab: name, tabset: setName },
          })
        );
        try {
          localStorage.setItem(storageKey, name);
        } catch (_) {}
      }
      tabs.forEach(function (t) {
        t.addEventListener("click", function () {
          activate(t.getAttribute("data-workspace-tab") || "");
        });
      });
      var saved = "";
      try {
        saved = localStorage.getItem(storageKey) || "";
      } catch (_) {}
      if (saved && root.querySelector('[data-workspace-pane="' + saved + '"]')) {
        activate(saved);
      }
      activators[setName] = activate;
    });
    window.PeonProjectOps.showWorkspaceTab = function (name) {
      if (name === "chat") {
        focusChat();
        return;
      }
      if (name === "terminal") {
        revealShellCard();
        return;
      }
      Object.keys(activators).forEach(function (key) {
        if (activators[key]) activators[key](name);
      });
    };
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

  function isChatMessage(msg) {
    var meta = (msg && msg.metadata) || {};
    var role = String(meta.role || meta.chat_role || "").toLowerCase();
    var tag = String(meta.tag || meta.feed_tag || "").toLowerCase();
    var event = String(meta.event || "").toLowerCase();
    // Live-feed only (shell audits, tools, findings) — never the chat thread.
    if (
      tag === "shell" ||
      tag === "terminal" ||
      tag === "tool" ||
      tag === "find" ||
      tag === "finding" ||
      tag === "net" ||
      tag === "loot" ||
      event === "terminal"
    ) {
      return false;
    }
    if (role === "user") return true;
    if (tag === "you" || tag === "steer" || tag === "need" || tag === "stop" || tag === "ask")
      return true;
    if (
      event.indexOf("operator") >= 0 ||
      event.indexOf("instruction") >= 0 ||
      event === "need_input" ||
      event.indexOf("replan") >= 0 ||
      event.indexOf("console_") === 0
    )
      return true;
    // Do not treat default stream_meta role=assistant alone as chat.
    return false;
  }

  function appendChatBubble(msg) {
    var thread = el("chat-thread");
    if (!thread || !msg) return;
    var id = msg.id != null ? String(msg.id) : "";
    if (id && thread.querySelector('[data-chat-id="' + id + '"]')) return;
    var text = String(msg.content || "")
      .replace(/\r\n/g, "\n")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{2,}/g, "\n")
      .trim();
    if (!text) return;

    var metaObj = msg.metadata || {};
    var role = String(metaObj.role || metaObj.chat_role || "").toLowerCase();
    var tag = String(metaObj.tag || "").toLowerCase();
    var side =
      role === "user" || tag === "you" ? "user" : "assistant";
    if (tag === "need") side = "system";
    if (tag === "stop" && role !== "assistant") side = "user";

    // Merge stream lines onto optimistic local-* bubbles (thinking node may be last).
    if (id && String(id).indexOf("local-") !== 0) {
      var locals = thread.querySelectorAll(
        '.chat-bubble[data-chat-id^="local-"]'
      );
      for (var i = 0; i < locals.length; i++) {
        var lb = locals[i];
        var bodyEl = lb.querySelector(".chat-bubble-body");
        if (!bodyEl || bodyEl.textContent !== text) continue;
        var isUserLocal = lb.classList.contains("chat-bubble-user");
        var wantUser = side === "user";
        if (isUserLocal !== wantUser) continue;
        lb.dataset.chatId = id;
        var timeEl = lb.querySelector(".chat-bubble-meta span:nth-child(2)");
        var t = formatTime(msg.created_at);
        if (timeEl && t) timeEl.textContent = t;
        return;
      }
    }

    var empty = thread.querySelector(".chat-thread-empty");
    if (empty) empty.remove();

    var bubble = document.createElement("div");
    bubble.className = "chat-bubble chat-bubble-" + side;
    if (id) bubble.dataset.chatId = id;

    var meta = document.createElement("div");
    meta.className = "chat-bubble-meta";
    var who = document.createElement("span");
    who.textContent =
      side === "user" ? "You" : side === "system" ? "Need input" : "Peon";
    meta.appendChild(who);
    var time = document.createElement("span");
    time.textContent = formatTime(msg.created_at) || "";
    if (time.textContent) meta.appendChild(time);
    var badgeText = badgeForType(String(msg.message_type || "log"), metaObj);
    if (badgeText && badgeText !== "LOG") {
      var badge = document.createElement("span");
      badge.className =
        "stream-badge stream-badge-" +
        badgeText.toLowerCase().replace(/[^\w-]/g, "");
      badge.textContent = badgeText;
      meta.appendChild(badge);
    }

    var body = document.createElement("pre");
    body.className = "chat-bubble-body";
    body.textContent = text;

    bubble.appendChild(meta);
    bubble.appendChild(body);
    thread.appendChild(bubble);
    thread.scrollTop = thread.scrollHeight;
  }

  function appendMessage(log, msg, latestByJob) {
    if (!log || !msg || msg.id == null) return;
    if (log.querySelector('[data-id="' + msg.id + '"]')) return;
    const text = String(msg.content || "")
      .replace(/\r\n/g, "\n")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{2,}/g, "\n")
      .trim();
    if (!text) return;
    const empty = log.querySelector(".empty");
    if (empty) empty.remove();

    if (msg.job_id && latestByJob) {
      latestByJob[String(msg.job_id)] = text;
    }

    if (isChatMessage(msg)) {
      appendChatBubble(msg);
    }

    const metaObj = msg.metadata || {};
    const role = String(metaObj.role || metaObj.chat_role || "").toLowerCase();
    const event = String(metaObj.event || "").toLowerCase();

    const article = document.createElement("article");
    const typ = String(msg.message_type || "log").replace(/[^\w-]/g, "");
    article.className = "feed-bubble stream-entry stream-" + typ;
    if (role === "user") article.classList.add("stream-user");
    if (event === "project_instruction" || event === "operator_guidance") {
      article.classList.add("stream-instruction");
    }
    article.dataset.id = String(msg.id);
    if (msg.job_id) article.dataset.jobId = String(msg.job_id);

    const body = document.createElement("div");
    body.className = "feed-bubble-body";
    body.textContent = text;

    const meta = document.createElement("header");
    meta.className = "feed-bubble-meta stream-meta";
    const who = document.createElement("span");
    who.className = "stream-agent";
    who.textContent = msg.job_title || msg.job_id || "Peon";
    meta.appendChild(who);
    const time = document.createElement("span");
    time.className = "stream-time";
    time.textContent = formatTime(msg.created_at) || "";
    if (time.textContent) meta.appendChild(time);
    const badge = document.createElement("span");
    let badgeText = badgeForType(typ, metaObj);
    if (role === "user" && badgeText === "LOG") badgeText = "YOU";
    else if (event === "operator_guidance" && badgeText === "LOG") badgeText = "STEER";
    badge.className =
      "stream-badge stream-badge-" + badgeText.toLowerCase().replace(/[^\w-]/g, "");
    badge.textContent = badgeText;
    meta.appendChild(badge);
    article.dataset.feedKey = feedFilterKey(badgeText);

    article.appendChild(body);
    article.appendChild(meta);
    log.appendChild(article);
    if (feedFilter !== "all" && article.dataset.feedKey !== feedFilter) {
      article.hidden = true;
    }
    log.scrollTop = log.scrollHeight;
  }

  function updateSkills(skills) {
    const row = el("skills-used");
    if (!row) return;
    row.replaceChildren();
    if (!skills || !skills.length) {
      const empty = document.createElement("span");
      empty.className = "meta empty-skills";
      empty.textContent = "None yet";
      row.appendChild(empty);
      return;
    }
    skills.forEach(function (s) {
      const name = typeof s === "string" ? s : s.name || "";
      const desc = typeof s === "string" ? "" : s.description || "";
      const tags = typeof s === "string" ? [] : s.tags || [];
      const chip = document.createElement("span");
      chip.className = "skills-used-chip";
      chip.tabIndex = 0;
      const n = document.createElement("span");
      n.className = "skills-used-name";
      n.textContent = name;
      chip.appendChild(n);
      const tip = document.createElement("span");
      tip.className = "skills-used-tip";
      tip.setAttribute("role", "tooltip");
      const tipName = document.createElement("strong");
      tipName.className = "skills-used-tip-name";
      tipName.textContent = name;
      tip.appendChild(tipName);
      if (desc) {
        const d = document.createElement("span");
        d.className = "skills-used-tip-desc";
        d.textContent = desc;
        tip.appendChild(d);
      }
      if (tags.length) {
        const tg = document.createElement("span");
        tg.className = "skills-used-tip-tags";
        tags.forEach(function (t) {
          const c = document.createElement("span");
          c.className = "chip";
          c.textContent = t;
          tg.appendChild(c);
        });
        tip.appendChild(tg);
      }
      chip.appendChild(tip);
      row.appendChild(chip);
    });
  }

  function updateObjectives(objectives) {
    const body = el("objectives-body");
    if (!body || !objectives) return;
    const count = el("objectives-count");
    if (count) count.textContent = String(objectives.length);
    objectives.forEach(function (o) {
      const card = body.querySelector('[data-objective-id="' + o.id + '"]');
      if (!card) return;
      const cell = card.querySelector("[data-objective-status]");
      if (cell) {
        cell.textContent = o.status || "";
        cell.dataset.status = o.status || "";
      }
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

  function appendStatusMenu(menu, statusChoices, current) {
    (statusChoices || []).forEach(function (opt) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "status-hover-option";
      btn.setAttribute("role", "option");
      btn.dataset.status = opt.value;
      if (opt.value === current) btn.setAttribute("aria-selected", "true");
      const pill = document.createElement("span");
      pill.className = "status-pill";
      pill.dataset.status = opt.value;
      pill.textContent = opt.label || opt.value;
      btn.appendChild(pill);
      menu.appendChild(btn);
    });
  }

  function renderFindingsRows(findings, statusChoices) {
    const body = el("findings-body");
    if (!body) return;
    body.replaceChildren();
    const rows = findings || [];
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "meta findings-empty";
      empty.textContent = "No findings match this filter.";
      body.appendChild(empty);
      return;
    }
    rows.forEach(function (f) {
      const card = document.createElement("article");
      card.className = "finding-card";
      card.dataset.findingId = f.id;
      card.dataset.status = f.status || "";
      card.dataset.severity = f.severity || "";

      const top = document.createElement("div");
      top.className = "finding-card-top";
      const seq = document.createElement("span");
      seq.className = "finding-seq meta";
      seq.textContent = "FIND-" + f.seq;
      const sev = document.createElement("span");
      sev.className = "sev-pill";
      sev.dataset.severity = f.severity || "";
      sev.setAttribute("aria-label", "Severity " + (f.severity || "unknown"));
      sev.textContent = f.severity || "";
      const hover = document.createElement("div");
      hover.className = "status-hover";
      hover.dataset.findingId = f.id;
      hover.dataset.prev = f.status || "open";
      const trigger = document.createElement("button");
      trigger.type = "button";
      trigger.className = "status-pill status-hover-trigger";
      trigger.dataset.status = f.status || "";
      trigger.setAttribute("aria-haspopup", "listbox");
      trigger.setAttribute("aria-label", "Finding status " + (f.status || ""));
      trigger.textContent = f.status || "";
      const menu = document.createElement("div");
      menu.className = "status-hover-menu";
      menu.setAttribute("role", "listbox");
      appendStatusMenu(menu, statusChoices, f.status);
      hover.appendChild(trigger);
      hover.appendChild(menu);
      top.appendChild(seq);
      top.appendChild(sev);
      top.appendChild(hover);

      const title = document.createElement("h3");
      title.className = "finding-card-title";
      title.textContent = f.title || "";
      card.appendChild(top);
      card.appendChild(title);
      if (f.host) {
        const host = document.createElement("p");
        host.className = "meta finding-host";
        host.textContent = f.host + (f.port ? ":" + f.port : "");
        card.appendChild(host);
      }
      if (f.evidence) {
        const ev = document.createElement("p");
        ev.className = "meta finding-evidence";
        ev.textContent = String(f.evidence).slice(0, 140);
        card.appendChild(ev);
      }
      const meta = document.createElement("div");
      meta.className = "finding-card-meta";
      const kindCode = document.createElement("code");
      kindCode.textContent = f.kind || "";
      meta.appendChild(kindCode);
      if (f.asset_type) {
        const ac = document.createElement("code");
        ac.textContent = f.asset_type;
        meta.appendChild(ac);
      }
      card.appendChild(meta);
      body.appendChild(card);
    });
  }

  async function triageFinding(projectPk, findingId, next, hoverEl) {
    const prev = (hoverEl && hoverEl.dataset.prev) || "";
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
        if (window.PeonUI) {
          window.PeonUI.toast(
            (data && data.error) || "Could not update status",
            "error"
          );
        }
        return;
      }
      if (hoverEl) {
        hoverEl.dataset.prev = data.status;
        const trigger = hoverEl.querySelector(".status-hover-trigger");
        if (trigger) {
          trigger.dataset.status = data.status;
          trigger.textContent = data.status;
          trigger.setAttribute("aria-label", "Finding status " + data.status);
        }
        hoverEl.querySelectorAll(".status-hover-option").forEach(function (opt) {
          opt.setAttribute(
            "aria-selected",
            opt.dataset.status === data.status ? "true" : "false"
          );
        });
      }
      const card = hoverEl && hoverEl.closest(".finding-card");
      if (card) card.dataset.status = data.status;
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
        return true;
      }
    } catch (_) {}
    return false;
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
        renderFindingsRows(data.findings, statusChoices);
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
      body.addEventListener("click", async function (ev) {
        const opt = ev.target.closest(".status-hover-option");
        if (opt) {
          ev.preventDefault();
          const hover = opt.closest(".status-hover");
          if (!hover) return;
          const findingId = hover.dataset.findingId;
          const next = opt.dataset.status;
          const needReload = await triageFinding(projectPk, findingId, next, hover);
          hover.classList.remove("is-open");
          if (needReload) reloadFindings();
          return;
        }
        const trigger = ev.target.closest(".status-hover-trigger");
        if (trigger) {
          ev.preventDefault();
          const hover = trigger.closest(".status-hover");
          if (!hover) return;
          const wasOpen = hover.classList.contains("is-open");
          body.querySelectorAll(".status-hover.is-open").forEach(function (h) {
            h.classList.remove("is-open");
          });
          if (!wasOpen) hover.classList.add("is-open");
        }
      });
      document.addEventListener("click", function (ev) {
        if (ev.target.closest(".status-hover")) return;
        body.querySelectorAll(".status-hover.is-open").forEach(function (h) {
          h.classList.remove("is-open");
        });
      });
    }
  }

  window.PeonProjectOps = {
    appendChatBubble: appendChatBubble,
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
        window.dispatchEvent(
          new CustomEvent("peon:agent-selected", {
            detail: { jobId: String(agent.id), agent: agent },
          })
        );
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
          updatePendingInputs(data.pending_inputs || []);

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
      var ws = el("console-workspace");
      if (ws) ws.dataset.pk = String(projectPk);
      bindFeedFilters();
      bindWorkspaceTabs();
      window.PeonProjectOps.focusChat = focusChat;
    },
  };

  function bindProjectFiles(projectPk) {
    if (window.PeonProjectConsoleChat && window.PeonProjectConsoleChat.bindFiles) {
      window.PeonProjectConsoleChat.bindFiles(projectPk, {
        el: el,
        csrfToken: csrfToken,
        appendChatBubble: appendChatBubble,
      });
    }
  }

  function bindProjectChat(projectPk) {
    if (window.PeonProjectConsoleChat && window.PeonProjectConsoleChat.bindChat) {
      window.PeonProjectConsoleChat.bindChat(projectPk, {
        el: el,
        csrfToken: csrfToken,
        appendChatBubble: appendChatBubble,
      });
    }
  }

  function updatePendingInputs(pending) {
    if (window.PeonProjectConsoleChat && window.PeonProjectConsoleChat.updatePendingInputs) {
      window.PeonProjectConsoleChat.updatePendingInputs(pending, { el: el });
    }
  }

})();
