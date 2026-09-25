/* n8n-style agent flow: roots left→right; subagents stacked; draggable nodes. */
(function () {
  var NODE_W = 210;
  var NODE_H = 118;
  var GAP_X = 72;
  var GAP_Y = 28;
  var PAD = 16;

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
    return t.length > 72 ? t.slice(0, 70) + "…" : t;
  }

  function storageKey(projectPk) {
    return "peon-graph-pos:" + String(projectPk || "");
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

  function layout(agents) {
    var positions = {};
    var edges = [];
    var x = PAD;
    var maxY = PAD + NODE_H;

    (agents || []).forEach(function (root, idx) {
      var kids = root.subagents || [];
      var stackH =
        kids.length > 0
          ? kids.length * (NODE_H + GAP_Y) - GAP_Y
          : NODE_H;
      var rootY = PAD + Math.max(0, (stackH - NODE_H) / 2);
      positions[root.id] = {
        x: x,
        y: rootY,
        w: NODE_W,
        h: NODE_H,
        agent: root,
      };

      var kidX = x + NODE_W + GAP_X;
      var ky = PAD;
      kids.forEach(function (kid) {
        positions[kid.id] = {
          x: kidX,
          y: ky,
          w: NODE_W,
          h: NODE_H,
          agent: kid,
        };
        edges.push({ from: root.id, to: kid.id });
        ky += NODE_H + GAP_Y;
      });

      if (idx > 0) {
        var prev = agents[idx - 1];
        if (prev && prev.id) {
          edges.push({ from: prev.id, to: root.id, sequential: true });
        }
      }

      var blockW = kids.length ? NODE_W + GAP_X + NODE_W : NODE_W;
      maxY = Math.max(maxY, PAD + stackH);
      x += blockW + GAP_X;
    });

    var maxX = x - GAP_X + PAD;
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

  function bezier(x1, y1, x2, y2) {
    var dx = Math.max(40, (x2 - x1) * 0.45);
    return (
      "M " +
      x1 +
      " " +
      y1 +
      " C " +
      (x1 + dx) +
      " " +
      y1 +
      ", " +
      (x2 - dx) +
      " " +
      y2 +
      ", " +
      x2 +
      " " +
      y2
    );
  }

  function drawEdges(svg, positions, edges) {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    edges.forEach(function (e) {
      var a = positions[e.from];
      var b = positions[e.to];
      if (!a || !b) return;
      var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      var x1 = a.x + a.w;
      var y1 = a.y + a.h / 2;
      var x2 = b.x;
      var y2 = b.y + b.h / 2;
      if (e.sequential && Math.abs(b.x - a.x) < 8) {
        x1 = a.x + a.w / 2;
        y1 = a.y + a.h;
        x2 = b.x + b.w / 2;
        y2 = b.y;
        path.setAttribute(
          "d",
          "M " +
            x1 +
            " " +
            y1 +
            " C " +
            x1 +
            " " +
            (y1 + 28) +
            ", " +
            x2 +
            " " +
            (y2 - 28) +
            ", " +
            x2 +
            " " +
            y2
        );
      } else {
        path.setAttribute("d", bezier(x1, y1, x2, y2));
      }
      path.setAttribute("class", "flow-edge" + (e.sequential ? " flow-edge-seq" : ""));
      path.setAttribute("fill", "none");
      svg.appendChild(path);
      var dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      dot.setAttribute("cx", String(x2));
      dot.setAttribute("cy", String(y2));
      dot.setAttribute("r", "3.5");
      dot.setAttribute("class", "flow-edge-dot");
      svg.appendChild(dot);
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
      "flow-node" +
      (String(agent.id) === String(selectedId) ? " is-selected" : "") +
      (led === "active" ? " is-running" : "");
    node.dataset.agentId = String(agent.id);
    node.dataset.status = String(agent.status || "");
    node.dataset.led = led;

    var head = document.createElement("div");
    head.className = "flow-node-head";

    var ledEl = document.createElement("span");
    ledEl.className = "flow-node-led";
    ledEl.dataset.led = led;
    ledEl.title = agent.status || "pending";
    ledEl.setAttribute("aria-hidden", "true");
    head.appendChild(ledEl);

    var role = document.createElement("div");
    role.className = "flow-node-role";
    role.textContent = agent.role || "ROOT";
    head.appendChild(role);
    node.appendChild(head);

    var title = document.createElement("div");
    title.className = "flow-node-title";
    title.textContent = agent.title || agent.id;
    node.appendChild(title);

    var cmd = document.createElement("div");
    cmd.className = "flow-node-cmd";
    var snippet =
      shortCmd(agent.last_command) ||
      shortCmd((agent.commands && agent.commands[0]) || "") ||
      shortCmd(agent.operator_command) ||
      shortCmd(agent.objective_description) ||
      "—";
    cmd.textContent = snippet;
    cmd.title =
      agent.operator_command ||
      agent.last_command ||
      (agent.commands && agent.commands[0]) ||
      "";
    node.appendChild(cmd);

    var foot = document.createElement("div");
    foot.className = "flow-node-foot";
    var calls = document.createElement("span");
    calls.className = "meta";
    var n = Number(agent.tool_calls || 0);
    calls.textContent = "calls " + n;
    foot.appendChild(calls);
    foot.appendChild(statusPill(agent.status));
    node.appendChild(foot);

    var live = document.createElement("a");
    live.className = "flow-node-live";
    live.href = "/projects/" + projectPk + "/jobs/" + agent.id + "/";
    live.textContent = "live";
    live.addEventListener("click", function (ev) {
      ev.stopPropagation();
    });
    node.appendChild(live);

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
    canvas.className = "flow-canvas";
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
    // Measure real node boxes (content can exceed NODE_H) so the card fits vertically.
    requestAnimationFrame(function () {
      resizeCanvas(canvas, svg, lay.positions);
      drawEdges(svg, lay.positions, lay.edges);
    });
    resizeCanvas(canvas, svg, lay.positions);
    drawEdges(svg, lay.positions, lay.edges);
  }

  window.PeonAgentGraph = { render: render, layout: layout, ledKind: ledKind };
})();
