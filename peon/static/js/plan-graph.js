/* Durable plan editor: dependency graph of agents (not objective cards). */
(function () {
  var NODE_W = 200;
  var NODE_H = 88;
  var GAP_X = 36;
  var GAP_Y = 56;
  var PAD = 32;

  function el(id) {
    return document.getElementById(id);
  }

  function roleGlyph(roleId) {
    var rid = String(roleId || "").toLowerCase();
    if (rid.indexOf("manager") >= 0) return "♛";
    if (rid.indexOf("analyzer") >= 0 || rid.indexOf("report") >= 0) return "☰";
    if (rid.indexOf("scan") >= 0 || rid.indexOf("network") >= 0) return "◎";
    if (rid.indexOf("osint") >= 0) return "◈";
    if (rid.indexOf("web") >= 0 || rid.indexOf("crawl") >= 0) return "◉";
    if (rid.indexOf("code") >= 0) return "</>";
    return "◆";
  }

  function shortPrompt(text) {
    var t = String(text || "").replace(/\s+/g, " ").trim();
    if (!t) return "(no prompt)";
    return t.length > 64 ? t.slice(0, 62) + "…" : t;
  }

  /** Layer agents by depends_on seq → pyramid rows. */
  function layoutAgents(agents) {
    var bySeq = {};
    (agents || []).forEach(function (a) {
      bySeq[Number(a.seq) || 0] = a;
    });
    var depth = {};
    function depthOf(a, stack) {
      var seq = Number(a.seq) || 0;
      if (depth[seq] != null) return depth[seq];
      if (stack[seq]) return 0;
      stack[seq] = 1;
      var deps = a.depends_on || [];
      var maxD = -1;
      deps.forEach(function (d) {
        var dep = bySeq[Number(d)];
        if (dep) maxD = Math.max(maxD, depthOf(dep, stack));
      });
      depth[seq] = maxD + 1;
      delete stack[seq];
      return depth[seq];
    }
    (agents || []).forEach(function (a) {
      depthOf(a, {});
    });

    var layers = {};
    (agents || []).forEach(function (a) {
      var d = depth[Number(a.seq) || 0] || 0;
      if (!layers[d]) layers[d] = [];
      layers[d].push(a);
    });
    Object.keys(layers).forEach(function (k) {
      layers[k].sort(function (a, b) {
        return (Number(a.seq) || 0) - (Number(b.seq) || 0);
      });
    });

    var positions = {};
    var edges = [];
    var layerKeys = Object.keys(layers)
      .map(Number)
      .sort(function (a, b) {
        return a - b;
      });
    var maxRowW = NODE_W;
    layerKeys.forEach(function (d) {
      var row = layers[d];
      var rowW = row.length * NODE_W + Math.max(0, row.length - 1) * GAP_X;
      maxRowW = Math.max(maxRowW, rowW);
    });

    layerKeys.forEach(function (d) {
      var row = layers[d];
      var rowW = row.length * NODE_W + Math.max(0, row.length - 1) * GAP_X;
      var startX = PAD + (maxRowW - rowW) / 2;
      row.forEach(function (a, i) {
        var id = String(a.id);
        positions[id] = {
          x: startX + i * (NODE_W + GAP_X),
          y: PAD + d * (NODE_H + GAP_Y),
          w: NODE_W,
          h: NODE_H,
          agent: a,
        };
        (a.depends_on || []).forEach(function (depSeq) {
          var dep = bySeq[Number(depSeq)];
          if (dep) edges.push({ from: String(dep.id), to: id });
        });
      });
    });

    var width = maxRowW + PAD * 2;
    var height =
      PAD * 2 +
      Math.max(1, layerKeys.length) * NODE_H +
      Math.max(0, layerKeys.length - 1) * GAP_Y;
    return { positions: positions, edges: edges, width: width, height: height };
  }

  function drawEdges(svg, positions, edges) {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    edges.forEach(function (e) {
      var a = positions[e.from];
      var b = positions[e.to];
      if (!a || !b) return;
      var x1 = a.x + a.w / 2;
      var y1 = a.y + a.h;
      var x2 = b.x + b.w / 2;
      var y2 = b.y;
      var mid = y1 + (y2 - y1) / 2;
      var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute(
        "d",
        "M " + x1 + " " + y1 + " C " + x1 + " " + mid + ", " + x2 + " " + mid + ", " + x2 + " " + y2
      );
      path.setAttribute("class", "flow-edge plan-flow-edge");
      path.setAttribute("fill", "none");
      svg.appendChild(path);
    });
  }

  function makeNode(agent, selectedId, onSelect) {
    var node = document.createElement("button");
    node.type = "button";
    node.className =
      "flow-node org-agent-node plan-graph-node" +
      (String(agent.id) === String(selectedId) ? " is-selected" : "");
    node.dataset.agentId = String(agent.id);
    node.dataset.status = String(agent.status || "pending");
    node.dataset.led = "idle";

    var row = document.createElement("div");
    row.className = "org-agent-row";

    var iconWrap = document.createElement("div");
    iconWrap.className = "org-agent-icon";
    iconWrap.setAttribute("aria-hidden", "true");
    var glyph = document.createElement("span");
    glyph.className = "org-agent-glyph";
    glyph.textContent = roleGlyph(agent.role_id);
    iconWrap.appendChild(glyph);
    var ledEl = document.createElement("span");
    ledEl.className = "flow-node-led org-agent-led";
    ledEl.dataset.led = "idle";
    ledEl.title = "OBJ-" + (agent.seq || "?");
    iconWrap.appendChild(ledEl);
    row.appendChild(iconWrap);

    var text = document.createElement("div");
    text.className = "org-agent-text";

    var title = document.createElement("div");
    title.className = "flow-node-title org-agent-title";
    title.textContent = agent.title || agent.role_id || "Agent";
    text.appendChild(title);

    var sub = document.createElement("div");
    sub.className = "org-agent-sub";
    sub.textContent =
      "OBJ-" + (agent.seq || "?") + " · " + (agent.role_id || "unassigned");
    text.appendChild(sub);

    var live = document.createElement("div");
    live.className = "org-agent-live";
    live.textContent = shortPrompt(agent.prompt || agent.description);
    live.title = agent.prompt || agent.description || "";
    text.appendChild(live);

    row.appendChild(text);
    node.appendChild(row);
    node.addEventListener("click", function () {
      onSelect(agent);
    });
    return node;
  }

  function init(opts) {
    var host = el((opts && opts.hostId) || "plan-graph-host");
    if (!host || host.dataset.bound) return;
    host.dataset.bound = "1";
    var state = opts.data || { agents: [], roles: [], schedules: [] };
    var apiUrl = opts.apiUrl || "";
    var selectedId = "";

    function roles() {
      return state.roles || [];
    }

    function agents() {
      return state.agents || [];
    }

    function fillRoleSelect(select, current) {
      if (!select) return;
      select.replaceChildren();
      roles().forEach(function (r) {
        var opt = document.createElement("option");
        opt.value = r.id;
        opt.textContent = r.label || r.id;
        if (r.id === current) opt.selected = true;
        select.appendChild(opt);
      });
    }

    function roleSkills(roleId) {
      var r = roles().find(function (x) {
        return x.id === roleId;
      });
      return (r && r.skills) || [];
    }

    function fillSkillChecks(roleId, selected) {
      var host = el("plan-edit-skills");
      if (!host) return;
      host.replaceChildren();
      var skills = roleSkills(roleId);
      var sel = {};
      (selected || skills).forEach(function (s) {
        sel[s] = true;
      });
      if (!skills.length) {
        var empty = document.createElement("p");
        empty.className = "meta";
        empty.textContent = "No skills on this role.";
        host.appendChild(empty);
        return;
      }
      skills.forEach(function (sid) {
        var lab = document.createElement("label");
        lab.className = "skill-check";
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = sid;
        cb.checked = !!sel[sid];
        lab.appendChild(cb);
        lab.appendChild(document.createTextNode(" " + sid));
        host.appendChild(lab);
      });
    }

    function readSkillChecks() {
      var host = el("plan-edit-skills");
      if (!host) return [];
      return Array.prototype.slice
        .call(host.querySelectorAll('input[type="checkbox"]:checked'))
        .map(function (cb) {
          return cb.value;
        });
    }

    function openEditor(agent) {
      var aside = el("plan-editor-aside");
      if (!aside) return;
      aside.hidden = false;
      fillRoleSelect(el("plan-edit-role"), agent.role_id || "");
      el("plan-edit-name").value = agent.title || "";
      el("plan-edit-prompt").value = agent.prompt || agent.description || "";
      fillSkillChecks(agent.role_id || "", agent.skill_ids || null);
      el("plan-edit-commands").value = (agent.commands || []).join("\n");
      var deps = el("plan-edit-deps");
      if (deps) {
        deps.value = (agent.depends_on || []).join(", ");
      }
      aside.dataset.agentId = String(agent.id || "");
      var roleSel = el("plan-edit-role");
      if (roleSel) {
        roleSel.onchange = function () {
          fillSkillChecks(roleSel.value, null);
        };
      }
    }

    function currentEditorAgent() {
      var aside = el("plan-editor-aside");
      if (!aside || aside.hidden) return null;
      var id = aside.dataset.agentId || "";
      return agents().find(function (a) {
        return String(a.id) === id;
      });
    }

    function applyEditor() {
      var agent = currentEditorAgent();
      if (!agent) return;
      agent.role_id = el("plan-edit-role").value;
      agent.title = el("plan-edit-name").value.trim() || agent.title;
      agent.prompt = el("plan-edit-prompt").value;
      agent.description = agent.prompt;
      agent.skill_ids = readSkillChecks();
      agent.commands = el("plan-edit-commands")
        .value.split(/\n+/)
        .map(function (s) {
          return s.trim();
        })
        .filter(Boolean);
      var depsEl = el("plan-edit-deps");
      if (depsEl) {
        agent.depends_on = depsEl.value
          .split(/[,\s]+/)
          .map(function (s) {
            return parseInt(s, 10);
          })
          .filter(function (n) {
            return Number.isFinite(n) && n > 0;
          });
      }
      render();
    }

    function render() {
      host.replaceChildren();
      var list = agents();
      if (!list.length) {
        var empty = document.createElement("p");
        empty.className = "meta";
        empty.textContent = "No agents in plan yet — add one to build the graph.";
        host.appendChild(empty);
        return;
      }

      var layout = layoutAgents(list);
      var wrap = document.createElement("div");
      wrap.className = "plan-graph-canvas-wrap";

      var canvas = document.createElement("div");
      canvas.className = "org-agent-canvas plan-graph-canvas";
      canvas.style.width = layout.width + "px";
      canvas.style.height = layout.height + "px";

      var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "flow-edges plan-flow-edges");
      svg.setAttribute("width", String(layout.width));
      svg.setAttribute("height", String(layout.height));
      canvas.appendChild(svg);

      Object.keys(layout.positions).forEach(function (id) {
        var p = layout.positions[id];
        var node = makeNode(p.agent, selectedId, function (agent) {
          selectedId = String(agent.id || "");
          openEditor(agent);
          render();
        });
        node.style.left = p.x + "px";
        node.style.top = p.y + "px";
        node.style.width = NODE_W + "px";
        canvas.appendChild(node);
      });

      drawEdges(svg, layout.positions, layout.edges);
      wrap.appendChild(canvas);
      host.appendChild(wrap);
    }

    function post(action) {
      var status = el("plan-status");
      if (status) status.textContent = action === "accept" ? "Starting…" : "Saving…";
      return window.PeonUI.postJson(apiUrl, { action: action, agents: agents() })
        .then(function (pack) {
          if (!pack.ok || !pack.data.ok) {
            if (status)
              status.textContent =
                (pack.data && pack.data.error) || "Save failed.";
            return pack;
          }
          if (pack.data.agents) state.agents = pack.data.agents;
          if (pack.data.roles) state.roles = pack.data.roles;
          if (pack.data.schedules) state.schedules = pack.data.schedules;
          render();
          renderSchedules();
          if (action === "accept" && pack.data.redirect) {
            if (status) status.textContent = "Accepted — opening war room…";
            window.location.href = pack.data.redirect;
            return pack;
          }
          if (status) status.textContent = "Saved.";
          return pack;
        })
        .catch(function () {
          if (status) status.textContent = "Network error.";
        });
    }

    var addBtn = el("plan-add-agent");
    if (addBtn) {
      addBtn.addEventListener("click", function () {
        var parent =
          agents().find(function (a) {
            return String(a.id) === String(selectedId);
          }) ||
          agents().find(function (a) {
            return (Number(a.seq) || 0) === 1;
          }) ||
          null;
        var role =
          (parent && parent.role_id) ||
          (roles().find(function (r) {
            return r.kind === "technical";
          }) ||
            roles()[0] ||
            {}
          ).id ||
          "";
        var nextSeq =
          agents().reduce(function (m, a) {
            return Math.max(m, Number(a.seq) || 0);
          }, 0) + 1;
        var node = {
          id: "new-" + Date.now(),
          seq: nextSeq,
          title: (parent && parent.title ? parent.title + " child" : "New specialist " + nextSeq),
          role_id: role,
          skill_ids: (parent && parent.skill_ids) || roleSkills(role),
          prompt: "Describe focused work for this agent.",
          description: "Describe focused work for this agent.",
          acceptance_criteria: "Observable done condition",
          phase: "recon",
          commands: [],
          depends_on: parent ? [Number(parent.seq) || 1] : nextSeq > 1 ? [1] : [],
          status: "pending",
        };
        state.agents = agents().concat([node]);
        selectedId = node.id;
        openEditor(node);
        render();
      });
    }
    var saveBtn = el("plan-save");
    if (saveBtn)
      saveBtn.addEventListener("click", function () {
        applyEditor();
        post("save");
      });
    var acceptBtn = el("plan-accept");
    if (acceptBtn)
      acceptBtn.addEventListener("click", function () {
        applyEditor();
        post("accept");
      });
    var applyBtn = el("plan-edit-apply");
    if (applyBtn) applyBtn.addEventListener("click", applyEditor);
    var closeBtn = el("plan-edit-close");
    if (closeBtn)
      closeBtn.addEventListener("click", function () {
        var aside = el("plan-editor-aside");
        if (aside) aside.hidden = true;
        selectedId = "";
        render();
      });
    var removeBtn = el("plan-edit-remove");
    if (removeBtn)
      removeBtn.addEventListener("click", function () {
        var agent = currentEditorAgent();
        if (!agent) return;
        state.agents = agents().filter(function (a) {
          return String(a.id) !== String(agent.id);
        });
        selectedId = "";
        var aside = el("plan-editor-aside");
        if (aside) aside.hidden = true;
        render();
      });

    function schedules() {
      return state.schedules || [];
    }

    function renderSchedules() {
      var shost = el("plan-schedules-host");
      if (!shost) return;
      shost.replaceChildren();
      var list = schedules();
      if (!list.length) {
        var empty = document.createElement("p");
        empty.className = "meta";
        empty.textContent = "No schedules yet.";
        shost.appendChild(empty);
        return;
      }
      var ul = document.createElement("ul");
      ul.className = "plan-schedule-list";
      list.forEach(function (s) {
        var li = document.createElement("li");
        li.className = "plan-schedule-item";
        var label =
          (s.title || s.role_id || "schedule") +
          " · " +
          s.kind +
          (s.kind === "interval"
            ? " every " + (s.interval_seconds || 0) + "s"
            : s.kind === "cron"
              ? " " + (s.cron || "")
              : "");
        var meta = document.createElement("span");
        meta.className = "meta";
        meta.textContent =
          label +
          (s.next_run_at ? " · next " + s.next_run_at : "") +
          (s.enabled ? "" : " · disabled");
        var del = document.createElement("button");
        del.type = "button";
        del.className = "secondary";
        del.textContent = "Remove";
        del.addEventListener("click", function () {
          postSchedule("schedule_delete", { id: s.id });
        });
        li.appendChild(meta);
        li.appendChild(del);
        ul.appendChild(li);
      });
      shost.appendChild(ul);
    }

    function postSchedule(action, extra) {
      var status = el("plan-status");
      if (status) status.textContent = "Updating schedule…";
      var body = Object.assign({ action: action }, extra || {});
      return window.PeonUI.postJson(apiUrl, body)
        .then(function (pack) {
          if (!pack.ok || !pack.data.ok) {
            if (status)
              status.textContent =
                (pack.data && pack.data.error) || "Schedule update failed.";
            return;
          }
          if (pack.data.schedules) state.schedules = pack.data.schedules;
          if (pack.data.agents) state.agents = pack.data.agents;
          render();
          renderSchedules();
          if (status) status.textContent = "Schedule saved.";
        })
        .catch(function () {
          if (status) status.textContent = "Network error.";
        });
    }

    var addSched = el("plan-add-schedule");
    if (addSched) {
      addSched.addEventListener("click", function () {
        var role = (roles()[0] && roles()[0].id) || "";
        var first = agents()[0];
        postSchedule("schedule_upsert", {
          title: "Recurring " + role,
          role_id: role,
          brief: "Scheduled durable run",
          kind: "interval",
          interval_seconds: 86400,
          objective_id:
            first && first.id && String(first.id).indexOf("new-") !== 0
              ? first.id
              : "",
          enabled: true,
        });
      });
    }

    fillRoleSelect(el("plan-edit-role"), "");
    render();
    renderSchedules();
  }

  window.PeonPlanGraph = { init: init };
})();
