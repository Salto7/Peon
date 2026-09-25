/* Engagement asset graph: SVG layout; open types/rels (no taxonomy allowlist). */
(function (global) {
  "use strict";

  var _host = null;
  var _svg = null;
  var _world = null;
  var _data = { nodes: [], edges: [], legend: {} };
  var _nodes = [];
  var _edges = [];
  var _byId = {};
  var _gEdges = null;
  var _gNodes = null;
  var _laidOut = false;
  var _drag = null;
  var _pan = null;
  var _ro = null;
  var _detail = null;
  var _zoom = { s: 1, x: 0, y: 0 };
  var ZOOM_MIN = 0.35;
  var ZOOM_MAX = 3.5;
  // Concentric rings (inner → outer) around the seed hub.
  var RING_ORDER = {
    seed: 0,
    in_scope: 0,
    discovered: 1,
    candidate: 1,
    finding: 2,
    exclusion: 2,
  };

  // Rules of Engagement authorization buckets only — node *types* and edge *rels* are free-form.
  var BUCKET_COLOR = {
    hub: "var(--muted)",
    seed: "var(--warn)",
    in_scope: "var(--accent)",
    candidate: "#7eb0f0",
    exclusion: "var(--bad)",
    finding: "var(--ok)",
    discovered: "var(--accent-dim)",
  };

  function hashHue(str) {
    var s = String(str || "");
    var h = 0;
    for (var i = 0; i < s.length; i++) {
      h = (h * 31 + s.charCodeAt(i)) >>> 0;
    }
    return h % 360;
  }

  function slugColor(slug, sat, light) {
    return "hsl(" + hashHue(slug) + " " + (sat || 42) + "% " + (light || 58) + "%)";
  }

  function nodeColor(node) {
    if (node.kind === "hub" || node.center) return BUCKET_COLOR.seed;
    if (node.bucket && BUCKET_COLOR[node.bucket]) return BUCKET_COLOR[node.bucket];
    return slugColor(node.type || node.id || "other", 40, 56);
  }

  function edgeColor(rel) {
    return slugColor(rel || "related", 35, 48);
  }

  function size() {
    if (!_host) return { w: 640, h: 420 };
    return {
      w: Math.max(320, _host.clientWidth || 640),
      h: Math.max(280, _host.clientHeight || 420),
    };
  }

  function pickCenter(nodes) {
    var hub = null;
    for (var i = 0; i < nodes.length; i++) {
      if (nodes[i].center || nodes[i].kind === "hub") return nodes[i];
    }
    for (i = 0; i < nodes.length; i++) {
      if (nodes[i].bucket === "seed") {
        hub = nodes[i];
        break;
      }
    }
    if (!hub) {
      for (i = 0; i < nodes.length; i++) {
        if (nodes[i].bucket === "in_scope") {
          hub = nodes[i];
          break;
        }
      }
    }
    return hub || nodes[0] || null;
  }

  /** Fixed circular layout: project seed (hub) at center, spokes on rings. */
  function layout(nodes, edges, w, h) {
    var n = nodes.length;
    if (!n) return;
    var cx = w / 2;
    var cy = h / 2;
    var hub = pickCenter(nodes);
    if (hub) {
      hub.kind = "hub";
      hub.x = cx;
      hub.y = cy;
    }
    var rings = {};
    nodes.forEach(function (node) {
      if (hub && node.id === hub.id) return;
      var ri =
        node.kind === "finding"
          ? 2
          : RING_ORDER.hasOwnProperty(node.bucket)
            ? RING_ORDER[node.bucket]
            : 1;
      if (!rings[ri]) rings[ri] = [];
      rings[ri].push(node);
    });
    var ringKeys = Object.keys(rings)
      .map(Number)
      .sort(function (a, b) {
        return a - b;
      });
    var maxR = Math.min(w, h) * 0.42;
    var minR = Math.min(w, h) * 0.22;
    ringKeys.forEach(function (ri, idx) {
      var group = rings[ri];
      var t = ringKeys.length <= 1 ? 0.55 : idx / Math.max(ringKeys.length - 1, 1);
      var radius = minR + (maxR - minR) * t;
      if (ringKeys.length === 1) radius = (minR + maxR) / 2;
      group.forEach(function (node, i) {
        var angle = (i / group.length) * Math.PI * 2 - Math.PI / 2;
        node.x = cx + Math.cos(angle) * radius;
        node.y = cy + Math.sin(angle) * radius;
      });
    });
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function displaySource(raw) {
    var s = String(raw || "").trim();
    if (!s) return "";
    if (s === "roe" || s === "rules_of_engagement") return "Rules of Engagement";
    return s;
  }

  function nodeDetailHtml(n) {
    if (!n) return "";
    var rows = [];
    function row(k, v) {
      if (v == null || v === "") return;
      rows.push(
        '<div class="as-detail-row"><span class="as-detail-k">' +
          escapeHtml(k) +
          '</span><span class="as-detail-v">' +
          escapeHtml(v) +
          "</span></div>"
      );
    }
    if (n.kind === "hub") row("Role", "project seed (center)");
    if (n.kind === "finding") {
      row("Finding", n.title || n.label || n.id);
      row("Severity", n.severity);
      row("Status", n.status);
      row("Kind", n.type);
    } else {
      row("Type", n.type || "asset");
      row("Value", n.value || n.label || n.id);
      row("Bucket", n.bucket);
      row("Source", displaySource(n.source));
    }
    var props = n.props || {};
    Object.keys(props).forEach(function (k) {
      if (k === "kind" || k === "title" || k === "severity" || k === "status" || k === "seq")
        return;
      var v = props[k];
      if (v == null || v === "" || typeof v === "object") return;
      row(k, v);
    });
    var head = escapeHtml(n.label || n.value || n.id || "Asset");
    return (
      '<div class="as-detail-title">' +
      head +
      "</div>" +
      (rows.length ? rows.join("") : '<div class="as-detail-row meta">No extra fields</div>')
    );
  }

  function ensureDetail() {
    if (!_host) return null;
    if (_detail && _detail.parentNode === _host) return _detail;
    _detail = document.createElement("div");
    _detail.className = "as-detail";
    _detail.hidden = true;
    _detail.setAttribute("role", "tooltip");
    _host.appendChild(_detail);
    return _detail;
  }

  function showDetail(node, clientX, clientY) {
    var el = ensureDetail();
    if (!el || !node) return;
    el.innerHTML = nodeDetailHtml(node);
    el.hidden = false;
    positionDetail(clientX, clientY);
  }

  function hideDetail() {
    if (_detail) _detail.hidden = true;
  }

  function positionDetail(clientX, clientY) {
    if (!_detail || !_host || _detail.hidden) return;
    var hostRect = _host.getBoundingClientRect();
    var pad = 12;
    var x = clientX - hostRect.left + pad;
    var y = clientY - hostRect.top + pad;
    _detail.style.left = "0px";
    _detail.style.top = "0px";
    var dw = _detail.offsetWidth || 220;
    var dh = _detail.offsetHeight || 120;
    if (x + dw > hostRect.width - 8) x = clientX - hostRect.left - dw - pad;
    if (y + dh > hostRect.height - 8) y = clientY - hostRect.top - dh - pad;
    x = Math.max(8, Math.min(x, hostRect.width - dw - 8));
    y = Math.max(8, Math.min(y, hostRect.height - dh - 8));
    _detail.style.left = x + "px";
    _detail.style.top = y + "px";
  }

  function applyZoom() {
    if (!_world) return;
    _world.setAttribute(
      "transform",
      "translate(" + _zoom.x + "," + _zoom.y + ") scale(" + _zoom.s + ")"
    );
  }

  function svgPoint(clientX, clientY) {
    var pt = _svg.createSVGPoint();
    pt.x = clientX;
    pt.y = clientY;
    var ctm = _svg.getScreenCTM();
    if (!ctm) return { x: clientX, y: clientY };
    return pt.matrixTransform(ctm.inverse());
  }

  function clampZoom(s) {
    return Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, s));
  }

  function zoomAt(clientX, clientY, nextS) {
    var p = svgPoint(clientX, clientY);
    var s = clampZoom(nextS);
    var wx = (p.x - _zoom.x) / _zoom.s;
    var wy = (p.y - _zoom.y) / _zoom.s;
    _zoom.s = s;
    _zoom.x = p.x - wx * s;
    _zoom.y = p.y - wy * s;
    applyZoom();
  }

  function resetZoom() {
    _zoom = { s: 1, x: 0, y: 0 };
    applyZoom();
  }

  function updateEdges() {
    if (!_gEdges) return;
    var lines = _gEdges.querySelectorAll("line");
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var S = _byId[line.getAttribute("data-source")];
      var T = _byId[line.getAttribute("data-target")];
      if (!S || !T) continue;
      line.setAttribute("x1", String(S.x));
      line.setAttribute("y1", String(S.y));
      line.setAttribute("x2", String(T.x));
      line.setAttribute("y2", String(T.y));
    }
  }

  function bindDrag(g, node) {
    g.style.cursor = "grab";
    g.addEventListener("pointerdown", function (ev) {
      if (ev.button !== 0) return;
      ev.preventDefault();
      ev.stopPropagation();
      hideDetail();
      g.setPointerCapture(ev.pointerId);
      var p = svgPoint(ev.clientX, ev.clientY);
      var wx = (p.x - _zoom.x) / _zoom.s;
      var wy = (p.y - _zoom.y) / _zoom.s;
      _drag = { id: node.id, el: g, ox: wx - node.x, oy: wy - node.y };
      g.style.cursor = "grabbing";
      g.classList.add("is-dragging");
    });
    g.addEventListener("pointermove", function (ev) {
      if (!_drag || _drag.id !== node.id) return;
      var p = svgPoint(ev.clientX, ev.clientY);
      var wx = (p.x - _zoom.x) / _zoom.s;
      var wy = (p.y - _zoom.y) / _zoom.s;
      node.x = wx - _drag.ox;
      node.y = wy - _drag.oy;
      g.setAttribute("transform", "translate(" + node.x + "," + node.y + ")");
      updateEdges();
    });
    function endDrag(ev) {
      if (!_drag || _drag.id !== node.id) return;
      try {
        g.releasePointerCapture(ev.pointerId);
      } catch (_) {}
      g.style.cursor = "grab";
      g.classList.remove("is-dragging");
      _drag = null;
    }
    g.addEventListener("pointerup", endDrag);
    g.addEventListener("pointercancel", endDrag);
  }

  function bindHover(g, node) {
    g.addEventListener("pointerenter", function (ev) {
      if (_drag) return;
      showDetail(node, ev.clientX, ev.clientY);
    });
    g.addEventListener("pointermove", function (ev) {
      if (_drag || !_detail || _detail.hidden) return;
      positionDetail(ev.clientX, ev.clientY);
    });
    g.addEventListener("pointerleave", function () {
      if (_drag) return;
      hideDetail();
    });
  }

  function bindPanZoom() {
    _svg.addEventListener(
      "wheel",
      function (ev) {
        ev.preventDefault();
        zoomAt(ev.clientX, ev.clientY, _zoom.s * (ev.deltaY < 0 ? 1.12 : 1 / 1.12));
      },
      { passive: false }
    );
    _svg.addEventListener("pointerdown", function (ev) {
      if (_drag) return;
      if (ev.button !== 0 && ev.button !== 1) return;
      if (ev.target.closest && ev.target.closest(".as-node")) return;
      ev.preventDefault();
      hideDetail();
      _svg.setPointerCapture(ev.pointerId);
      _pan = { x: ev.clientX, y: ev.clientY, ox: _zoom.x, oy: _zoom.y };
      _svg.classList.add("is-panning");
    });
    _svg.addEventListener("pointermove", function (ev) {
      if (!_pan) return;
      var dim = size();
      var scaleX = dim.w / (_svg.clientWidth || dim.w);
      var scaleY = dim.h / (_svg.clientHeight || dim.h);
      _zoom.x = _pan.ox + (ev.clientX - _pan.x) * scaleX;
      _zoom.y = _pan.oy + (ev.clientY - _pan.y) * scaleY;
      applyZoom();
    });
    function endPan(ev) {
      if (!_pan) return;
      try {
        _svg.releasePointerCapture(ev.pointerId);
      } catch (_) {}
      _pan = null;
      _svg.classList.remove("is-panning");
    }
    _svg.addEventListener("pointerup", endPan);
    _svg.addEventListener("pointercancel", endPan);
    _svg.addEventListener("dblclick", function (ev) {
      if (ev.target.closest && ev.target.closest(".as-node")) return;
      resetZoom();
    });
  }

  function ensureToolbar() {
    if (!_host || _host.querySelector(".as-zoom-toolbar")) return;
    var bar = document.createElement("div");
    bar.className = "as-zoom-toolbar";
    bar.innerHTML =
      '<button type="button" class="as-zoom-btn" data-as-zoom="in" title="Zoom in" aria-label="Zoom in">+</button>' +
      '<button type="button" class="as-zoom-btn" data-as-zoom="out" title="Zoom out" aria-label="Zoom out">−</button>' +
      '<button type="button" class="as-zoom-btn" data-as-zoom="reset" title="Reset zoom" aria-label="Reset zoom">⌂</button>';
    bar.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-as-zoom]");
      if (!btn) return;
      var act = btn.getAttribute("data-as-zoom");
      var dim = size();
      var cx = (_host.getBoundingClientRect().left || 0) + dim.w / 2;
      var cy = (_host.getBoundingClientRect().top || 0) + dim.h / 2;
      if (act === "in") zoomAt(cx, cy, _zoom.s * 1.2);
      else if (act === "out") zoomAt(cx, cy, _zoom.s / 1.2);
      else resetZoom();
    });
    _host.appendChild(bar);
  }

  function paintLegend() {
    var el = document.getElementById("attack-legend");
    if (!el) return;
    var leg = _data.legend || {};
    var buckets = leg.buckets || [];
    var types = leg.types || [];
    var rels = leg.relations || [];
    var html = "";
    var seen = {};
    function chip(label, color, title) {
      var key = label + "|" + color;
      if (seen[key]) return;
      seen[key] = true;
      html +=
        '<span class="as-leg" title="' +
        String(title || label).replace(/"/g, "&quot;") +
        '"><i class="as-leg-swatch" style="background:' +
        color +
        '"></i>' +
        label +
        "</span>";
    }
    buckets.forEach(function (b) {
      chip(b, BUCKET_COLOR[b] || slugColor(b), "bucket: " + b);
    });
    types.slice(0, 12).forEach(function (t) {
      chip(t, slugColor(t), "type: " + t);
    });
    if (types.length > 12) {
      chip("+" + (types.length - 12) + " types", "var(--muted)", types.slice(12).join(", "));
    }
    rels.slice(0, 8).forEach(function (r) {
      chip("→ " + r, edgeColor(r), "relation: " + r);
    });
    el.innerHTML = html || '<span class="meta">No assets yet</span>';
  }

  function paint() {
    if (!_host || !_svg) return;
    var dim = size();
    _svg.setAttribute("viewBox", "0 0 " + dim.w + " " + dim.h);
    _svg.setAttribute("width", "100%");
    _svg.setAttribute("height", "100%");
    _svg.innerHTML = "";
    _world = document.createElementNS("http://www.w3.org/2000/svg", "g");
    _world.setAttribute("class", "as-world");
    applyZoom();
    _gEdges = null;
    _gNodes = null;
    paintLegend();

    if (!_nodes.length) {
      var empty = document.createElementNS("http://www.w3.org/2000/svg", "text");
      empty.setAttribute("x", String(dim.w / 2));
      empty.setAttribute("y", String(dim.h / 2));
      empty.setAttribute("text-anchor", "middle");
      empty.setAttribute("fill", "currentColor");
      empty.setAttribute("opacity", "0.55");
      empty.textContent =
        "No assets yet — add Rules of Engagement subjects or wait for findings / skills.";
      _svg.appendChild(empty);
      return;
    }

    _gEdges = document.createElementNS("http://www.w3.org/2000/svg", "g");
    _edges.forEach(function (e) {
      var S = _byId[e.source];
      var T = _byId[e.target];
      if (!S || !T) return;
      var line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", String(S.x));
      line.setAttribute("y1", String(S.y));
      line.setAttribute("x2", String(T.x));
      line.setAttribute("y2", String(T.y));
      line.setAttribute("data-source", e.source);
      line.setAttribute("data-target", e.target);
      line.setAttribute("data-rel", e.rel || "related");
      line.setAttribute("class", "as-edge");
      line.setAttribute("stroke", edgeColor(e.rel));
      line.setAttribute("stroke-opacity", "0.55");
      var et = document.createElementNS("http://www.w3.org/2000/svg", "title");
      et.textContent = e.rel || "related";
      line.appendChild(et);
      _gEdges.appendChild(line);
    });
    _world.appendChild(_gEdges);

    _gNodes = document.createElementNS("http://www.w3.org/2000/svg", "g");
    _nodes.forEach(function (n) {
      var g = document.createElementNS("http://www.w3.org/2000/svg", "g");
      g.setAttribute(
        "class",
        "as-node as-bucket-" +
          (n.bucket || "other") +
          " as-type-" +
          (n.type || "other") +
          (n.kind === "hub" ? " as-hub" : "")
      );
      g.setAttribute("transform", "translate(" + n.x + "," + n.y + ")");
      g.setAttribute("data-type", n.type || "");
      g.setAttribute("data-bucket", n.bucket || "");
      g.setAttribute("data-id", n.id || "");

      var r = n.kind === "hub" ? 20 : n.kind === "finding" ? 10 : 14;
      var circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("r", String(r));
      circle.setAttribute("fill", nodeColor(n));
      circle.setAttribute("opacity", n.kind === "finding" ? "0.85" : "0.95");
      if (n.kind === "hub") {
        circle.setAttribute("stroke", "var(--warn)");
        circle.setAttribute("stroke-width", "2.5");
      }
      g.appendChild(circle);

      var label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("y", String(r + 14));
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("class", "as-label");
      label.style.pointerEvents = "none";
      label.textContent = n.label || n.id;
      g.appendChild(label);

      bindHover(g, n);
      bindDrag(g, n);
      _gNodes.appendChild(g);
    });
    _world.appendChild(_gNodes);
    _svg.appendChild(_world);
    ensureDetail();
  }

  function ensureLayout(force) {
    var dim = size();
    if (!_laidOut || force) {
      layout(_nodes, _edges, dim.w, dim.h);
      _laidOut = true;
    }
  }

  function rebuild(forceLayout) {
    hideDetail();
    _nodes = (_data.nodes || []).map(function (n) {
      return Object.assign({}, n);
    });
    _edges = _data.edges || [];
    _byId = {};
    _nodes.forEach(function (n) {
      _byId[n.id] = n;
    });
    _laidOut = false;
    ensureLayout(!!forceLayout);
    paint();
  }

  function fit() {
    // Recompute circle positions when the host size changes.
    if (_nodes.length) {
      ensureLayout(true);
    }
    paint();
  }

  function mount(opts) {
    _host = document.getElementById((opts && opts.hostId) || "attack-surface-host");
    if (!_host) return;
    _data = (opts && opts.data) || { nodes: [], edges: [], legend: {} };
    _zoom = { s: 1, x: 0, y: 0 };
    _detail = null;
    _host.innerHTML = "";
    _host.classList.add("as-host");
    _svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    _svg.setAttribute("class", "attack-surface-svg");
    _host.appendChild(_svg);
    ensureToolbar();
    ensureDetail();
    bindPanZoom();
    rebuild(true);
    if (_ro) {
      try {
        _ro.disconnect();
      } catch (_) {}
    }
    if (typeof ResizeObserver === "function") {
      _ro = new ResizeObserver(function () {
        fit();
      });
      _ro.observe(_host);
    }
    window.addEventListener("peon:war-pane", function (ev) {
      if (ev.detail && ev.detail.pane === "attack") fit();
    });
  }

  global.PeonAttackSurface = {
    mount: mount,
    fit: fit,
    resetZoom: resetZoom,
  };
})(window);
