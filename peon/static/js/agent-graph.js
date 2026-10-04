/* Paperclip-style agent hierarchy: top-down tree, clickable nodes. */
(function () {
  var NODE_W = 220;
  var NODE_H = 78;
  var GAP_X = 28;
  var GAP_Y = 42;
  var PAD = 24;

  function statusPill(status) {
    if (window.PeonUI && window.PeonUI.statusPill) {
      return window.PeonUI.statusPill(status);
    }
    var pill = document.createElement("span");
    pill.className = "status-pill";
    pill.dataset.status = status || "";
    pill.textContent = status || "";
    return pill;
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

  function loadPositions(projectPk) {
    try {
      var raw = sessionStorage.getItem(storageKey(projectPk));
      if (!raw) return {};
      var data = JSON.parse(raw);
      return data && typeof data === "object" ? data : {};
    } catch (_) {
      return {};
    }
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

  function roleIcon(agent) {
    var rid = String(agent.primary_role_id || "").toLowerCase();
    var caps = (agent.capabilities || []).join(" ").toLowerCase();
    if (rid.indexOf("manager") >= 0 || !agent.reports_to) return "♛";
    if (caps.indexOf("report") >= 0) return "☰";
    if (
      rid.indexOf("scan") >= 0 ||
      rid.indexOf("network") >= 0 ||
      caps.indexOf("recon") >= 0
    ) {
      return "◎";
    }
    if (rid.indexOf("osint") >= 0) return "◈";
    if (caps.indexOf("exploit") >= 0 || rid.indexOf("code") >= 0) return "</>";
    return "◉";
  }

  function flatten(agents, out) {
    (agents || []).forEach(function (a) {
      out.push(a);
      flatten(a.subagents || [], out);
    });
    return out;
  }

  function subtreeWidth(agent) {
    var kids = agent.subagents || [];
    if (!kids.length) return NODE_W;
    var w = 0;
    kids.forEach(function (k, i) {
      w += subtreeWidth(k);
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
    var positions = {};
    var edges = [];
    var x = PAD;
    var roots = agents || [];
    roots.forEach(function (root, idx) {
      var w = place(root, x, PAD, positions, edges);
      x += w + (idx < roots.length - 1 ? GAP_X * 2 : 0);
    });

    var maxX = NODE_W + PAD * 2;
    var maxY = NODE_H + PAD * 2;
    Object.keys(positions).forEach(function (id) {
      var p = positions[id];
      maxX = Math.max(maxX, p.x + p.w + PAD);
      maxY = Math.max(maxY, p.y + p.h + PAD);
    });
    return {
      positions: positions,
      edges: edges,
      width: Math.max(maxX, NODE_W + PAD * 2),
      height: Math.max(maxY, NODE_H + PAD * 2),
    };
  }

  function applySaved(positions, saved) {
    Object.keys(saved || {}).forEach(function (id) {
      if (!positions[id]) return;
      var s = saved[id];
      if (typeof s.x === "number") positions[id].x = s.x;
      if (typeof s.y === "number") positions[id].y = s.y;
    });
    var maxX = NODE_W + PAD * 2;
    var maxY = NODE_H + PAD * 2;
    Object.keys(positions).forEach(function (id) {
      var p = positions[id];
      maxX = Math.max(maxX, p.x + p.w + PAD);
      maxY = Math.max(maxY, p.y + p.h + PAD);
    });
    return { width: maxX, height: maxY };
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
      path.setAttribute("class", "flow-edge");
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

  function makeNode(agent, projectPk, selectedId, onSelect, onContext) {
    var led = ledKind(agent.status);
    var node = document.createElement("button");
    node.type = "button";
    node.className =
      "flow-node org-agent-node" +
      (String(agent.id) === String(selectedId) ? " is-selected" : "") +
      (led === "active" ? " is-running" : "");
    node.dataset.agentId = String(agent.id);
    node.dataset.status = String(agent.status || "");
    node.dataset.led = led;

    var row = document.createElement("div");
    row.className = "org-agent-row";

    var iconWrap = document.createElement("div");
    iconWrap.className = "org-agent-icon";
    iconWrap.setAttribute("aria-hidden", "true");
    var glyph = document.createElement("span");
    glyph.className = "org-agent-glyph";
    glyph.textContent = roleIcon(agent);
    iconWrap.appendChild(glyph);
    var ledEl = document.createElement("span");
    ledEl.className = "flow-node-led org-agent-led";
    ledEl.dataset.led = led;
    ledEl.title = agent.status || "pending";
    iconWrap.appendChild(ledEl);
    row.appendChild(iconWrap);

    var text = document.createElement("div");
    text.className = "org-agent-text";

    var title = document.createElement("div");
    title.className = "flow-node-title org-agent-title";
    title.textContent =
      agent.role_label ||
      agent.primary_role_id ||
      agent.crew_role ||
      agent.title ||
      "Agent";
    text.appendChild(title);

    var sub = document.createElement("div");
    sub.className = "org-agent-sub";
    sub.textContent =
      agent.crew_role ||
      agent.primary_role_id ||
      (agent.role_ids && agent.role_ids[0]) ||
      "role";
    text.appendChild(sub);

    var live = document.createElement("div");
    live.className = "org-agent-live";
    live.textContent =
      shortCmd(agent.last_command) ||
      shortCmd(agent.objective_title) ||
      String(agent.status || "pending");
    live.title =
      agent.last_command ||
      agent.objective_title ||
      agent.status ||
      "";
    text.appendChild(live);

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
      var dx = ev.clientX - startX;
      var dy = ev.clientY - startY;
      if (Math.abs(dx) + Math.abs(dy) > 3) node.dataset.didDrag = "1";
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

  function render(listEl, agents, projectPk, selectedId, onSelect, onContext) {
    if (!listEl) return;
    listEl.replaceChildren();
    if (!agents || !agents.length) {
      var p = document.createElement("p");
      p.className = "meta agents-empty";
      p.textContent = "No agents yet — create a project with a brief to auto-plan.";
      listEl.appendChild(p);
      return;
    }

    var lay = layout(agents);
    var bounds = applySaved(lay.positions, loadPositions(projectPk));
    lay.width = Math.max(lay.width, bounds.width);
    lay.height = Math.max(lay.height, bounds.height);

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

    listEl.appendChild(canvas);
    requestAnimationFrame(function () {
      resizeCanvas(canvas, svg, lay.positions);
      drawEdges(svg, lay.positions, lay.edges);
    });
    resizeCanvas(canvas, svg, lay.positions);
    drawEdges(svg, lay.positions, lay.edges);
  }

  window.PeonAgentGraph = {
    render: render,
    layout: layout,
    ledKind: ledKind,
    flatten: flatten,
    statusPill: statusPill,
  };
})();
