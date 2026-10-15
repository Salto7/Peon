/* Paperclip-style agent hierarchy: PM apex, pyramid below, panel-centered. */
(function () {
  var NODE_W = 240;
  var NODE_H = 96;
  var GAP_X = 28;
  var GAP_Y = 48;
  var PAD = 28;
  var ZOOM_MIN = 0.35;
  var ZOOM_MAX = 3.5;
  var _zoom = { s: 1, x: 0, y: 0 };
  var _zoomReady = false;
  var _pan = null;
  var _viewport = null;
  var _world = null;
  var _seen = {}; // agent ids already animated in; live re-renders must not replay it

  function statusPill(status) {
    return window.PeonUI.statusPill(status);
  }

  /** green=active, red=failed, blue=completed, grey=not started */
  function ledKind(status) {
    var s = String(status || "").toLowerCase();
    if (s === "running" || s === "paused") return "active";
    if (s === "failed" || s === "cancelled") return "failed";
    if (s === "completed") return "done";
    return "idle";
  }

  function shortCmd(text) {
    var t = String(text || "").replace(/\s+/g, " ").trim();
    if (!t) return "";
    return t.length > 56 ? t.slice(0, 54) + "…" : t;
  }

  function storageKey(projectPk) {
    return "peon-org-pos:" + String(projectPk || "");
  }

  function savePositions(projectPk, positions) {
    try {
      var out = {};
      Object.keys(positions).forEach(function (id) {
        var p = positions[id];
        out[id] = { x: p.x, y: p.y };
      });
      sessionStorage.setItem(storageKey(projectPk), JSON.stringify(out));
    } catch (_) {}
  }

  /** Lane-ranked glyphs: ♛ PM, ♞ expert (officer), ♙ spawned (pawn), ⌕ analyzer. */
  function roleIcon(agent, lane) {
    var kind = lane || "";
    if (kind === "pm") return "♛";
    if (kind === "analyzer") return "⌕";
    if (kind === "skill") return "♙";
    if (kind === "expert") return "♞";
    if (isManagerAgent(agent)) return "♛";
    if (isAnalyzerAgent(agent)) return "⌕";
    if (agent.is_spawn || agent.execution_parent_id) return "♙";
    return "♞";
  }

  function flatten(agents, out) {
    (agents || []).forEach(function (a) {
      out.push(a);
      flatten(a.subagents || [], out);
    });
    return out;
  }

  function cloneNode(agent) {
    return {
      id: agent.id,
      title: agent.title,
      status: agent.status,
      primary_role_id: agent.primary_role_id,
      primary_skill_id: agent.primary_skill_id,
      role_label: agent.role_label,
      crew_role: agent.crew_role,
      role_ids: agent.role_ids,
      skill_ids: agent.skill_ids,
      lane: agent.lane,
      is_spawn: agent.is_spawn,
      execution_parent_id: agent.execution_parent_id,
      capabilities: agent.capabilities,
      reports_to: agent.reports_to,
      last_command: agent.last_command,
      objective_id: agent.objective_id,
      objective_seq: agent.objective_seq,
      objective_title: agent.objective_title,
      role: agent.role,
      subagents: (agent.subagents || []).map(cloneNode),
    };
  }

  function isManagerAgent(agent) {
    var rid = String(agent.primary_role_id || "").toLowerCase();
    var label = String(agent.role_label || "").toLowerCase();
    if (rid.indexOf("manager") >= 0 || label.indexOf("manager") >= 0) return true;
    if (!agent.reports_to && String(agent.role || "") === "ROOT") return true;
    return false;
  }

  function isAnalyzerAgent(agent) {
    var caps = (agent.capabilities || []).join(" ").toLowerCase();
    var rid = String(agent.primary_role_id || "").toLowerCase();
    return caps.indexOf("report") >= 0 || rid.indexOf("analyzer") >= 0;
  }

  function sortPyramidChildren(kids) {
    return (kids || []).slice().sort(function (a, b) {
      var aa = isAnalyzerAgent(a) ? 1 : 0;
      var ba = isAnalyzerAgent(b) ? 1 : 0;
      if (aa !== ba) return aa - ba;
      var ar = String(a.role_label || a.primary_role_id || a.title || "");
      var br = String(b.role_label || b.primary_role_id || b.title || "");
      return ar.localeCompare(br);
    });
  }

  /**
   * Single apex (PM) on top; other roots tuck under it; children sorted for a
   * stable pyramid (specialists, then analyzer).
   */
  function normalizePyramid(agents) {
    var roots = (agents || []).map(cloneNode);
    if (!roots.length) return [];

    var apexIdx = -1;
    for (var i = 0; i < roots.length; i++) {
      if (isManagerAgent(roots[i])) {
        apexIdx = i;
        break;
      }
    }
    if (apexIdx < 0) {
      for (var j = 0; j < roots.length; j++) {
        if (!roots[j].reports_to) {
          apexIdx = j;
          break;
        }
      }
    }
    if (apexIdx < 0) apexIdx = 0;

    var apex = roots[apexIdx];
    var orphans = [];
    for (var k = 0; k < roots.length; k++) {
      if (k === apexIdx) continue;
      orphans.push(roots[k]);
    }
    apex.subagents = sortPyramidChildren(
      (apex.subagents || []).concat(orphans)
    );
    apex.subagents.forEach(function walk(node) {
      node.subagents = sortPyramidChildren(node.subagents || []);
      node.subagents.forEach(walk);
    });
    return [apex];
  }

  function subtreeWidth(agent) {
    var kids = agent.subagents || [];
    if (!kids.length) return NODE_W;
    var w = 0;
    kids.forEach(function (kid, i) {
      w += subtreeWidth(kid);
      if (i < kids.length - 1) w += GAP_X;
    });
    return Math.max(NODE_W, w);
  }

  function place(agent, x, y, positions, edges) {
    var width = subtreeWidth(agent);
    var cx = x + width / 2 - NODE_W / 2;
    positions[agent.id] = {
      x: cx,
      y: y,
      w: NODE_W,
      h: NODE_H,
      agent: agent,
    };
    var kids = agent.subagents || [];
    if (!kids.length) return width;
    var cursor = x;
    kids.forEach(function (kid, i) {
      var kw = subtreeWidth(kid);
      place(kid, cursor, y + NODE_H + GAP_Y, positions, edges);
      edges.push({ from: agent.id, to: kid.id });
      cursor += kw + (i < kids.length - 1 ? GAP_X : 0);
    });
    return width;
  }

  function layout(agents) {
    var tree = normalizePyramid(agents);
    var positions = {};
    var edges = [];
    var totalW = 0;
    tree.forEach(function (root, idx) {
      totalW += subtreeWidth(root);
      if (idx < tree.length - 1) totalW += GAP_X * 2;
    });
    var x = PAD;
    tree.forEach(function (root, idx) {
      var w = place(root, x, PAD, positions, edges);
      x += w + (idx < tree.length - 1 ? GAP_X * 2 : 0);
    });

    var minX = Infinity;
    var maxX = 0;
    var maxY = NODE_H + PAD * 2;
    Object.keys(positions).forEach(function (id) {
      var p = positions[id];
      minX = Math.min(minX, p.x);
      maxX = Math.max(maxX, p.x + p.w);
      maxY = Math.max(maxY, p.y + p.h + PAD);
    });
    if (!isFinite(minX)) minX = PAD;

    // Equal side padding so flex-centering in the panel keeps the pyramid centered.
    var shift = PAD - minX;
    if (shift !== 0) {
      Object.keys(positions).forEach(function (id) {
        positions[id].x += shift;
      });
      maxX += shift;
    }

    var width = Math.max(maxX + PAD, NODE_W + PAD * 2, totalW + PAD * 2);
    return {
      positions: positions,
      edges: edges,
      width: width,
      height: Math.max(maxY, NODE_H + PAD * 2),
    };
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
        "M " +
          x1 +
          " " +
          y1 +
          " C " +
          x1 +
          " " +
          mid +
          ", " +
          x2 +
          " " +
          mid +
          ", " +
          x2 +
          " " +
          y2
      );
      var live = a.agent && b.agent && ledKind(b.agent.status) === "active";
      path.setAttribute("class", "flow-edge" + (live ? " is-live" : ""));
      path.setAttribute("fill", "none");
      svg.appendChild(path);
    });
  }

  function resizeCanvas(canvas, svg, positions) {
    var maxX = NODE_W + PAD * 2;
    var maxY = PAD * 2;
    Object.keys(positions).forEach(function (id) {
      var p = positions[id];
      var node = canvas.querySelector(
        '.flow-node[data-agent-id="' + String(id).replace(/"/g, "") + '"]'
      );
      var w = p.w || NODE_W;
      var h = p.h || NODE_H;
      if (node) {
        w = Math.max(w, node.offsetWidth || 0);
        h = Math.max(h, node.offsetHeight || 0);
        p.w = w;
        p.h = h;
      }
      maxX = Math.max(maxX, p.x + w + PAD);
      maxY = Math.max(maxY, p.y + h + PAD);
    });
    if (maxY < NODE_H + PAD * 2) maxY = NODE_H + PAD * 2;
    canvas.style.width = maxX + "px";
    canvas.style.height = maxY + "px";
    svg.setAttribute("width", String(maxX));
    svg.setAttribute("height", String(maxY));
  }

  function laneOf(agent) {
    var lane = String(agent.lane || "").toLowerCase();
    if (lane) return lane;
    if (agent.is_spawn || agent.execution_parent_id) return "skill";
    if (isManagerAgent(agent)) return "pm";
    if (isAnalyzerAgent(agent)) return "analyzer";
    return "expert";
  }

  function makeNode(agent, projectPk, selectedId, onSelect, onContext) {
    var led = ledKind(agent.status);
    var lane = laneOf(agent);
    var node = document.createElement("button");
    node.type = "button";
    node.className =
      "flow-node org-agent-node lane-" +
      lane +
      (String(agent.id) === String(selectedId) ? " is-selected" : "") +
      (led === "active" ? " is-running" : "") +
      (lane === "skill" ? " is-spawn" : "") +
      (_seen[String(agent.id)] ? "" : " is-new");
    _seen[String(agent.id)] = true;
    node.dataset.agentId = String(agent.id);
    node.dataset.status = String(agent.status || "");
    node.dataset.led = led;
    node.dataset.lane = lane;
    node.dataset.roleId = String(agent.primary_role_id || "");

    var row = document.createElement("div");
    row.className = "org-agent-row";

    var iconWrap = document.createElement("div");
    iconWrap.className = "org-agent-icon";
    iconWrap.setAttribute("aria-hidden", "true");
    var glyph = document.createElement("span");
    glyph.className =
      "org-agent-glyph" + (lane === "analyzer" ? " org-agent-glyph-analyzer" : "");
    glyph.textContent = roleIcon(agent, lane);
    glyph.title =
      lane === "pm"
        ? "Project manager"
        : lane === "analyzer"
          ? "Analyzer"
          : lane === "skill"
            ? "Spawned skill worker"
            : lane === "expert"
              ? "Expert"
              : "Agent";
    iconWrap.appendChild(glyph);
    var ledEl = document.createElement("span");
    ledEl.className = "flow-node-led org-agent-led";
    ledEl.dataset.led = led;
    ledEl.title = agent.status || "pending";
    iconWrap.appendChild(ledEl);
    row.appendChild(iconWrap);

    var text = document.createElement("div");
    text.className = "org-agent-text";

    var skills = Array.isArray(agent.skill_ids)
      ? agent.skill_ids.filter(Boolean)
      : [];
    var primarySkill =
      agent.primary_skill_id || (skills.length ? skills[0] : "");
    var roleId =
      agent.primary_role_id ||
      (agent.role_ids && agent.role_ids[0]) ||
      "";
    var roleLabel =
      agent.role_label ||
      agent.crew_role ||
      roleId ||
      "Agent";
    var skillTitle =
      (skills.length ? skills.join(", ") : "") ||
      primarySkill ||
      "skill worker";
    var heading = lane === "skill" ? skillTitle : roleLabel;

    var title = document.createElement("div");
    title.className = "flow-node-title org-agent-title";
    title.textContent = heading;
    title.title = heading;
    text.appendChild(title);

    var skillLine = document.createElement("div");
    skillLine.className = "org-agent-skills";
    if (lane === "skill") {
      var spawnTag = document.createElement("span");
      spawnTag.className = "org-agent-tag org-agent-tag-spawned";
      spawnTag.textContent = "spawned";
      skillLine.appendChild(spawnTag);
    } else if (skills.length) {
      var skillTag = document.createElement("span");
      skillTag.className = "org-agent-tag org-agent-tag-skill";
      skillTag.textContent = "skills";
      skillLine.appendChild(skillTag);
      var skillNames = document.createElement("span");
      skillNames.className = "org-agent-skill-names";
      skillNames.textContent = skills.join(", ");
      skillNames.title = skills.join(", ");
      skillLine.appendChild(skillNames);
    } else if (primarySkill) {
      skillLine.textContent = primarySkill;
      skillLine.title = primarySkill;
    } else {
      skillLine.textContent = lane === "pm" ? "project-manager" : "—";
    }
    text.appendChild(skillLine);

    var objLine = document.createElement("div");
    objLine.className = "org-agent-obj";
    var seq =
      agent.objective_seq != null && agent.objective_seq !== ""
        ? Number(agent.objective_seq)
        : NaN;
    var oid = String(agent.objective_id || "").trim();
    if (oid && !isNaN(seq) && seq > 0) {
      var objLink = document.createElement("a");
      objLink.className = "org-agent-obj-link";
      objLink.href = "#objectives";
      objLink.textContent = "OBJ-" + seq;
      objLink.title =
        agent.objective_title
          ? "OBJ-" + seq + ": " + agent.objective_title
          : "Open OBJ-" + seq;
      objLink.addEventListener("click", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        if (window.PeonWarNav && typeof window.PeonWarNav.show === "function") {
          window.PeonWarNav.show("results");
        } else {
          try {
            location.hash = "objectives";
          } catch (_) {}
        }
        window.requestAnimationFrame(function () {
          var card = document.querySelector(
            '.objective-card[data-objective-id="' + oid + '"]'
          );
          if (!card) return;
          card.scrollIntoView({ behavior: "smooth", block: "center" });
          card.classList.add("is-highlight");
          window.setTimeout(function () {
            card.classList.remove("is-highlight");
          }, 1800);
        });
      });
      objLine.appendChild(objLink);
    } else {
      objLine.textContent =
        shortCmd(agent.last_command) || String(agent.status || "pending");
      objLine.title =
        agent.last_command || agent.objective_title || agent.status || "";
    }
    if (String(agent.status || "") === "idle") {
      var wait = document.createElement("span");
      wait.className = "org-agent-wait";
      var waitingOn = Array.isArray(agent.waiting_on) ? agent.waiting_on : [];
      if (waitingOn.length) {
        wait.textContent =
          " · idle, waiting for " +
          waitingOn
            .map(function (n) {
              return "OBJ-" + n;
            })
            .join(", ");
      } else if (agent.idle_note) {
        wait.textContent = " · idle: " + agent.idle_note;
      } else {
        wait.textContent = " · idle, waiting for a message";
      }
      wait.title = wait.textContent.replace(/^ · /, "");
      objLine.appendChild(wait);
    }
    text.appendChild(objLine);

    row.appendChild(text);
    node.appendChild(row);

    node.addEventListener("click", function (ev) {
      if (node.dataset.didDrag === "1") {
        node.dataset.didDrag = "";
        ev.preventDefault();
        return;
      }
      onSelect(agent);
    });
    node.addEventListener("contextmenu", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      if (typeof onContext === "function") {
        onContext(agent, ev.clientX, ev.clientY);
      }
    });
    return node;
  }

  function enableDrag(node, id, positions, projectPk, canvas, svg, edges) {
    var dragging = false;
    var startX = 0;
    var startY = 0;
    var origX = 0;
    var origY = 0;

    function onMove(ev) {
      if (!dragging) return;
      var s = _zoom.s || 1;
      var dx = (ev.clientX - startX) / s;
      var dy = (ev.clientY - startY) / s;
      if (Math.abs(ev.clientX - startX) + Math.abs(ev.clientY - startY) > 3) {
        node.dataset.didDrag = "1";
      }
      var pos = positions[id];
      pos.x = Math.max(0, origX + dx);
      pos.y = Math.max(0, origY + dy);
      node.style.left = pos.x + "px";
      node.style.top = pos.y + "px";
      resizeCanvas(canvas, svg, positions);
      drawEdges(svg, positions, edges);
    }

    function onUp() {
      if (!dragging) return;
      dragging = false;
      node.classList.remove("is-dragging");
      canvas.classList.remove("is-dragging-node");
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp);
      savePositions(projectPk, positions);
    }

    node.addEventListener("pointerdown", function (ev) {
      if (ev.button !== 0) return;
      if (ev.target && ev.target.closest && ev.target.closest("a")) return;
      ev.stopPropagation();
      dragging = true;
      node.dataset.didDrag = "";
      startX = ev.clientX;
      startY = ev.clientY;
      origX = positions[id].x;
      origY = positions[id].y;
      node.classList.add("is-dragging");
      canvas.classList.add("is-dragging-node");
      try {
        node.setPointerCapture(ev.pointerId);
      } catch (_) {}
      document.addEventListener("pointermove", onMove);
      document.addEventListener("pointerup", onUp);
    });
  }

  function clampZoom(s) {
    return Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, s));
  }

  function applyZoom() {
    if (!_world) return;
    _world.style.transform =
      "translate(" + _zoom.x + "px," + _zoom.y + "px) scale(" + _zoom.s + ")";
  }

  function viewportPoint(clientX, clientY) {
    if (!_viewport) return { x: clientX, y: clientY };
    var r = _viewport.getBoundingClientRect();
    return { x: clientX - r.left, y: clientY - r.top };
  }

  function zoomAt(clientX, clientY, nextS) {
    var p = viewportPoint(clientX, clientY);
    var s = clampZoom(nextS);
    var wx = (p.x - _zoom.x) / _zoom.s;
    var wy = (p.y - _zoom.y) / _zoom.s;
    _zoom.s = s;
    _zoom.x = p.x - wx * s;
    _zoom.y = p.y - wy * s;
    applyZoom();
  }

  function centerGraph() {
    if (!_viewport || !_world) return;
    var canvas = _world.querySelector(".org-agent-canvas");
    var vw = _viewport.clientWidth || 0;
    var vh = _viewport.clientHeight || 0;
    var cw =
      (canvas && (canvas.offsetWidth || parseFloat(canvas.style.width))) || 0;
    var ch =
      (canvas && (canvas.offsetHeight || parseFloat(canvas.style.height))) || 0;
    _zoom.s = 1;
    _zoom.x = Math.max(12, (vw - cw) / 2);
    _zoom.y = Math.max(12, (vh - ch) / 2);
    applyZoom();
  }

  function ensureToolbar(viewport) {
    if (!viewport || viewport.querySelector(".as-zoom-toolbar")) return;
    var bar = document.createElement("div");
    bar.className = "as-zoom-toolbar";
    bar.innerHTML =
      '<button type="button" class="as-zoom-btn" data-ag-zoom="in" title="Zoom in" aria-label="Zoom in">+</button>' +
      '<button type="button" class="as-zoom-btn" data-ag-zoom="out" title="Zoom out" aria-label="Zoom out">−</button>' +
      '<button type="button" class="as-zoom-btn" data-ag-zoom="center" title="Center" aria-label="Center">⌂</button>';
    bar.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      var btn = ev.target.closest("[data-ag-zoom]");
      if (!btn || !_viewport) return;
      var act = btn.getAttribute("data-ag-zoom");
      var r = _viewport.getBoundingClientRect();
      var cx = r.left + r.width / 2;
      var cy = r.top + r.height / 2;
      if (act === "in") zoomAt(cx, cy, _zoom.s * 1.2);
      else if (act === "out") zoomAt(cx, cy, _zoom.s / 1.2);
      else centerGraph();
    });
    bar.addEventListener("pointerdown", function (ev) {
      ev.stopPropagation();
    });
    viewport.appendChild(bar);
  }

  function bindPanZoom(viewport) {
    if (!viewport || viewport.dataset.agZoomBound === "1") return;
    viewport.dataset.agZoomBound = "1";
    viewport.addEventListener(
      "wheel",
      function (ev) {
        ev.preventDefault();
        zoomAt(
          ev.clientX,
          ev.clientY,
          _zoom.s * (ev.deltaY < 0 ? 1.12 : 1 / 1.12)
        );
      },
      { passive: false }
    );
    viewport.addEventListener("pointerdown", function (ev) {
      if (_pan) return;
      if (ev.button !== 0 && ev.button !== 1) return;
      if (ev.target.closest && ev.target.closest(".org-agent-node")) return;
      if (ev.target.closest && ev.target.closest(".as-zoom-toolbar")) return;
      ev.preventDefault();
      try {
        viewport.setPointerCapture(ev.pointerId);
      } catch (_) {}
      _pan = {
        x: ev.clientX,
        y: ev.clientY,
        ox: _zoom.x,
        oy: _zoom.y,
        moved: false,
      };
      viewport.classList.add("is-panning");
    });
    viewport.addEventListener("pointermove", function (ev) {
      if (!_pan) return;
      if (Math.abs(ev.clientX - _pan.x) + Math.abs(ev.clientY - _pan.y) > 4) {
        _pan.moved = true;
      }
      _zoom.x = _pan.ox + (ev.clientX - _pan.x);
      _zoom.y = _pan.oy + (ev.clientY - _pan.y);
      applyZoom();
    });
    function endPan(ev) {
      if (!_pan) return;
      try {
        viewport.releasePointerCapture(ev.pointerId);
      } catch (_) {}
      var wasClick = ev.type === "pointerup" && !_pan.moved;
      _pan = null;
      viewport.classList.remove("is-panning");
      // A plain click on empty canvas clears the agent selection.
      if (wasClick) {
        window.dispatchEvent(new CustomEvent("peon:agent-deselected"));
      }
    }
    viewport.addEventListener("pointerup", endPan);
    viewport.addEventListener("pointercancel", endPan);
    viewport.addEventListener("dblclick", function (ev) {
      if (ev.target.closest && ev.target.closest(".org-agent-node")) return;
      if (ev.target.closest && ev.target.closest(".as-zoom-toolbar")) return;
      centerGraph();
    });
  }

  function ensureViewport(listEl) {
    var viewport = listEl.querySelector(".org-agent-viewport");
    if (!viewport) {
      listEl.replaceChildren();
      viewport = document.createElement("div");
      viewport.className = "org-agent-viewport";
      listEl.appendChild(viewport);
      ensureToolbar(viewport);
      bindPanZoom(viewport);
    } else {
      var oldWorld = viewport.querySelector(".org-agent-world");
      if (oldWorld) oldWorld.remove();
      var empty = viewport.querySelector(".agents-empty");
      if (empty) empty.remove();
    }
    _viewport = viewport;
    return viewport;
  }

  function render(listEl, agents, projectPk, selectedId, onSelect, onContext) {
    if (!listEl) return;
    if (!agents || !agents.length) {
      _viewport = null;
      _world = null;
      _zoomReady = false;
      listEl.replaceChildren();
      var empty = document.createElement("p");
      empty.className = "meta agents-empty";
      empty.textContent =
        "No agents yet — create a project with a brief to auto-plan.";
      listEl.appendChild(empty);
      return;
    }

    var viewport = ensureViewport(listEl);

    // Always recompute pyramid so live polls stay PM-top / centered.
    var lay = layout(agents);
    try {
      sessionStorage.removeItem(storageKey(projectPk));
    } catch (_) {}

    var world = document.createElement("div");
    world.className = "org-agent-world";
    _world = world;

    var canvas = document.createElement("div");
    canvas.className = "flow-canvas org-agent-canvas";
    canvas.style.width = lay.width + "px";
    canvas.style.height = lay.height + "px";

    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "flow-edges");
    svg.setAttribute("width", String(lay.width));
    svg.setAttribute("height", String(lay.height));
    drawEdges(svg, lay.positions, lay.edges);
    canvas.appendChild(svg);

    Object.keys(lay.positions).forEach(function (id) {
      var pos = lay.positions[id];
      var node = makeNode(
        pos.agent,
        projectPk,
        selectedId,
        onSelect,
        onContext || null
      );
      node.style.left = pos.x + "px";
      node.style.top = pos.y + "px";
      node.style.width = pos.w + "px";
      node.style.minHeight = pos.h + "px";
      enableDrag(node, id, lay.positions, projectPk, canvas, svg, lay.edges);
      canvas.appendChild(node);
    });

    world.appendChild(canvas);
    viewport.appendChild(world);
    resizeCanvas(canvas, svg, lay.positions);
    drawEdges(svg, lay.positions, lay.edges);
    requestAnimationFrame(function () {
      resizeCanvas(canvas, svg, lay.positions);
      drawEdges(svg, lay.positions, lay.edges);
      if (!_zoomReady) {
        centerGraph();
        _zoomReady = true;
      } else {
        applyZoom();
      }
    });
  }

  window.PeonAgentGraph = {
    render: render,
    layout: layout,
    normalizePyramid: normalizePyramid,
    ledKind: ledKind,
    flatten: flatten,
    statusPill: statusPill,
    center: centerGraph,
  };
})();
