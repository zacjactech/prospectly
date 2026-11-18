/* Prospectly canvas engine (docs/design/pipeline-canvas.md §2).
   World layer + pan/zoom (0.5–2.0) + phase bands/stage zones/wires +
   zone drag (8px snap, alignment guides) + density flip at 0.8 +
   pl.canvas.v1 localStorage (debounced, quota-swallowing, version-banded).
   Constants mirror canvas.css tokens. Vanilla JS, CSP-safe (CSSOM only). */
(function () {
  "use strict";

  /* ----- constants (mirrored in canvas.css) ----- */
  var ZONE_W = 288;
  var ZONE_GAP_X = 24;
  var ZONE_GAP_Y = 48;
  var BAND_LABEL_H = 36;
  var BAND_GAP = 12;
  var GRID = 8;
  var ALIGN_TOL = 4; // spec §2.3: guides at ±4px edge/center alignment
  var MIN_ZOOM = 0.5;
  var MAX_ZOOM = 2;
  /* Readable floor for fit-to-view (spec §V2.1 "minimum 0.93 scale, pan
     instead of shrinking"): 13px card name × 0.93 = 12.09px on screen. */
  var MIN_READABLE = 0.93;
  var ZOOM_STEP = 1.25;
  /* Phase 1.1/1.2: never shrink below the readable floor (card name 13px →
     ≥12px); pan instead of shrinking. Card focus keeps the zoom, clamped to
     100–125% so the card is never re-framed unpredictably. */
  var MIN_FOCUS_ZOOM = 1;
  var MAX_FOCUS_ZOOM = 1.1;
  var DENSITY_FLOOR = 0.75; // spec §2.5, amended 2026-11-16: board fit at small laptops lands here
  var STORE_KEY = "pl.canvas.v1";
  var STORE_VERSION = 1;
  var BOARD = "prospects";
  var FIT_PAD = 32;

  /* ----- module state ----- */
  var vp = null; // viewport element (rebound on every board swap)
  var world = null;
  var wireGroup = null;
  var zones = [];
  var labels = [];
  var zoom = 1;
  var panX = 0;
  var panY = 0;
  var wirePending = false;
  var saveTimer = null;

  /* ----- storage (spec §2.6) ----- */

  function readStore() {
    var raw = null;
    try {
      raw = localStorage.getItem(STORE_KEY);
    } catch (e) {
      return { version: STORE_VERSION, boards: {} }; // storage unavailable
    }
    if (!raw) return { version: STORE_VERSION, boards: {} };
    var data = null;
    try {
      data = JSON.parse(raw);
    } catch (e) {
      data = null;
    }
    if (!data || typeof data !== "object" || data.version !== STORE_VERSION) {
      /* Version mismatch or parse failure → discard, render defaults. */
      try {
        localStorage.removeItem(STORE_KEY);
      } catch (e2) {
        /* ignore */
      }
      return { version: STORE_VERSION, boards: {} };
    }
    if (!data.boards || typeof data.boards !== "object") data.boards = {};
    return data;
  }

  var store = readStore();

  function boardState() {
    if (!store.boards[BOARD]) store.boards[BOARD] = {};
    var b = store.boards[BOARD];
    if (!b.zones || typeof b.zones !== "object") b.zones = {};
    if (!b.cardOrder || typeof b.cardOrder !== "object") b.cardOrder = {};
    return b;
  }

  function scheduleSave() {
    /* Flush the live view into the blob on every save trigger so zoom/pan
       persist together with zones and cardOrder (spec §2.6 shape). */
    if (vp) {
      var b = boardState();
      b.zoom = zoom;
      b.panX = panX;
      b.panY = panY;
    }
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      try {
        localStorage.setItem(STORE_KEY, JSON.stringify(store));
      } catch (e) {
        /* quota exceeded / private mode — swallow (spec §2.6) */
      }
    }, 500);
  }

  function patchBoard(fn) {
    fn(boardState());
    scheduleSave();
  }

  /* ----- transform / view ----- */

  function clampZoom(z) {
    return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));
  }

  function applyTransform() {
    if (!world) return;
    world.style.transform =
      "translate(" + panX + "px, " + panY + "px) scale(" + zoom + ")";
    updateEdgeFades();
  }

  /* Which viewport edges cut content? Measured from the content union
     (every world child is absolutely positioned — the world itself shrink-
     wraps to 0x0, so its offset box is useless here). Toggles .has-edge-*,
     which light the matching gradient on the .world-edges overlay. */
  function updateEdgeFades() {
    if (!vp || !world) return;
    var r = vp.getBoundingClientRect();
    var minX = Infinity;
    var minY = Infinity;
    var maxX = -Infinity;
    var maxY = -Infinity;
    var nodes = world.querySelectorAll(
      ".canvas-zone, .setup-cluster, .region-label, .band-label, .msg-zone"
    );
    for (var i = 0; i < nodes.length; i++) {
      var b = nodes[i].getBoundingClientRect();
      if (b.width < 1 && b.height < 1) continue;
      if (b.left < minX) minX = b.left;
      if (b.top < minY) minY = b.top;
      if (b.right > maxX) maxX = b.right;
      if (b.bottom > maxY) maxY = b.bottom;
    }
    if (minX === Infinity) return;
    vp.classList.toggle("has-edge-t", minY < r.top - 8);
    vp.classList.toggle("has-edge-r", maxX > r.right + 8);
    vp.classList.toggle("has-edge-b", maxY > r.bottom + 8);
    vp.classList.toggle("has-edge-l", minX < r.left - 8);
  }

  var densityMode = null;
  function updateDensity() {
    if (!vp) return;
    var screen = vp.parentNode;
    var compact = zoom < DENSITY_FLOOR;
    screen.classList.toggle("density-compact", compact);
    screen.classList.toggle("density-full", !compact);
    var mode = compact ? "compact" : "full";
    if (densityMode !== null && densityMode !== mode) {
      /* Row spacing was measured with the other card height: re-measure it
         now so expanded cards never sit on top of the next row. */
      densityMode = mode;
      relayout();
      return;
    }
    densityMode = mode;
  }

  function updateZoomReadout() {
    if (!vp) return;
    var readout = vp.querySelector("[data-canvas-zoom]");
    if (readout) {
      var pct = Math.round(zoom * 100) + "%";
      readout.textContent = pct;
      /* Keep the accessible name in sync with the visible percentage
         (axe label-content-name-mismatch), while still explaining reset. */
      readout.setAttribute(
        "aria-label",
        "Reset zoom to 100 percent (currently " + pct + ")"
      );
    }
  }

  function setView(z, x, y) {
    zoom = clampZoom(z);
    panX = x;
    panY = y;
    applyTransform();
    updateZoomReadout();
    updateDensity();
    scheduleWires(); // density flips change card/zone heights
    scheduleSave();
  }

  function zoomAt(target, clientX, clientY) {
    var rect = vp.getBoundingClientRect();
    var cx = clientX - rect.left;
    var cy = clientY - rect.top;
    var wx = (cx - panX) / zoom;
    var wy = (cy - panY) / zoom;
    var z = clampZoom(target);
    setView(z, cx - wx * z, cy - wy * z);
  }

  function zoomCenter(factor) {
    var rect = vp.getBoundingClientRect();
    zoomAt(zoom * factor, rect.left + rect.width / 2, rect.top + rect.height / 2);
  }

  function contentBounds() {
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    var seen = false;
    var parts = zones.concat(labels);
    [".setup-cluster", ".canvas-region", "[data-region-label]"].forEach(function (sel) {
      Array.prototype.forEach.call(world.querySelectorAll(sel), function (el) {
        parts.push(el);
      });
    });
    parts.forEach(function (el) {
      seen = true;
      var l = el.offsetLeft;
      var t = el.offsetTop;
      minX = Math.min(minX, l);
      minY = Math.min(minY, t);
      maxX = Math.max(maxX, l + el.offsetWidth);
      maxY = Math.max(maxY, t + el.offsetHeight);
    });
    if (!seen || minX === Infinity) return null;
    return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
  }

  function fit() {
    fitElement(null, FIT_PAD);
  }

  function fitElement(el, pad, maxZoom) {
    var b = null;
    if (el) {
      b = {
        x: el.offsetLeft,
        y: el.offsetTop,
        w: el.offsetWidth,
        h: el.offsetHeight,
      };
    } else {
      b = contentBounds();
    }
    if (!b || !vp) return;
    var rect = vp.getBoundingClientRect();
    if (rect.width < 10 || rect.height < 10) return;
    var p = pad || FIT_PAD;
    var cap = typeof maxZoom === "number" ? maxZoom : 1;
    var z = Math.min((rect.width - p * 2) / b.w, (rect.height - p * 2) / b.h, cap);
    z = clampZoom(z);
    setView(
      z,
      (rect.width - b.w * z) / 2 - b.x * z,
      (rect.height - b.h * z) / 2 - b.y * z
    );
  }

  function resetZoom() {
    zoomCenter(1 / zoom); // anchor at viewport center → exactly 100%
  }

  /* Bounds of the stage board (zones + band labels): the board's wrapper has
     no layout box of its own because zones are absolutely positioned. */
  function boardBounds() {
    var parts = zones.concat(labels);
    if (!parts.length) return null;
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    parts.forEach(function (el) {
      minX = Math.min(minX, el.offsetLeft);
      minY = Math.min(minY, el.offsetTop);
      maxX = Math.max(maxX, el.offsetLeft + el.offsetWidth);
      maxY = Math.max(maxY, el.offsetTop + el.offsetHeight);
    });
    return {
      offsetLeft: minX,
      offsetTop: minY,
      offsetWidth: maxX - minX,
      offsetHeight: maxY - minY,
    };
  }

  /* Pipeline + full Messages lane: every board zone/label and the message
     region/label, so fit-to-view never clips the Messages count. */
  function stageBounds() {
    var b = boardBounds();
    if (!b) return null;
    var out = world ? world.querySelector('[data-region="outreach"]') : null;
    var outLabel = world ? world.querySelector('[data-region-label="outreach"]') : null;
    var parts = [];
    if (out) parts.push(out);
    if (outLabel) parts.push(outLabel);
    if (!parts.length) return b;
    var minX = b.offsetLeft, minY = b.offsetTop;
    var maxX = b.offsetLeft + b.offsetWidth;
    var maxY = b.offsetTop + b.offsetHeight;
    parts.forEach(function (el) {
      minX = Math.min(minX, el.offsetLeft);
      minY = Math.min(minY, el.offsetTop);
      maxX = Math.max(maxX, el.offsetLeft + el.offsetWidth);
      maxY = Math.max(maxY, el.offsetTop + el.offsetHeight);
    });
    return { offsetLeft: minX, offsetTop: minY, offsetWidth: maxX - minX, offsetHeight: maxY - minY };
  }

  /* Fit just the stage board (used by the lens focus). */
  function fitBoard(maxZoom) {
    var b = boardBounds();
    if (b) fitElement(b, 56, maxZoom);
  }

  /* Phase 1.1 — fit-to-view: fill the viewport width, but never shrink below
     the readable floor (spec §V2.1: card name 13px → ≥12px ⇒ 0.93). A board
     wider/taller than the viewport at that scale is panned, not zoomed out —
     fitting BOTH axes collapsed tall worlds onto MIN_ZOOM (50% first load,
     density-compact) whenever the setup cluster + four phase rows exceeded the
     viewport height. */
  function fitBoardReadable(maxZoom) {
    var b = stageBounds();
    if (!b || !vp) return;
    var rect = vp.getBoundingClientRect();
    if (rect.width < 10 || rect.height < 10) return;
    var cap = typeof maxZoom === "number" ? maxZoom : 1;
    var pad = 48;
    var z = Math.min(MAX_ZOOM, Math.max(MIN_READABLE, Math.min(cap, (rect.width - pad * 2) / b.offsetWidth)));
    var w = b.offsetWidth * z;
    var h = b.offsetHeight * z;
    /* Centre while it fits; otherwise start flush at the pad (48px) so the
       first lane / setup cluster is never clipped off-canvas. */
    var x = w + pad * 2 <= rect.width ? (rect.width - w) / 2 - b.offsetLeft * z : pad - b.offsetLeft * z;
    var y = h + pad * 2 <= rect.height ? (rect.height - h) / 2 - b.offsetTop * z : pad - b.offsetTop * z;
    setView(z, x, y);
  }

  /* Phase 1.2 — selecting a card pans (never zooms) so the card is centered
     in the visible area beside the open drawer. Zoom is kept and only clamped
     to 100–125%. */
  function focusCard(card) {
    if (!vp || !card) return;
    var rect = vp.getBoundingClientRect();
    if (rect.width < 10 || rect.height < 10) return;
    var r = card.getBoundingClientRect();
    var wx = (r.left + r.width / 2 - rect.left - panX) / zoom;
    var wy = (r.top + r.height / 2 - rect.top - panY) / zoom;
    var z = Math.min(MAX_FOCUS_ZOOM, Math.max(MIN_FOCUS_ZOOM, zoom));
    var drawerW = 480;
    var panels = document.querySelectorAll(".panel");
    var panel = panels.length ? panels[panels.length - 1] : null;
    if (panel) {
      var pw = panel.getBoundingClientRect().width;
      if (pw > 0) drawerW = pw;
    }
    var avail = Math.max(280, rect.width - drawerW);
    setView(z, rect.left + avail / 2 - wx * z, rect.top + rect.height / 2 - wy * z);
  }

  /* Bring a focused card into view without changing zoom — keyboard users
     must never tab to an off-screen setup/prospect card. */
  function ensureVisible(el) {
    if (!vp || !el) return;
    var r = el.getBoundingClientRect();
    var v = vp.getBoundingClientRect();
    if (r.top >= v.top && r.bottom <= v.bottom && r.left >= v.left && r.right <= v.right) {
      return;
    }
    var wx = (r.left + r.width / 2 - v.left - panX) / zoom;
    var wy = (r.top + r.height / 2 - v.top - panY) / zoom;
    setView(zoom, v.left + v.width / 2 - wx * zoom, v.top + v.height / 2 - wy * zoom);
  }

  function fitOutreach() {
    fitElement(world ? world.querySelector('[data-region="outreach"]') : null, 56);
  }

  /* Owner 2026-11-17: "a btn that properly arranges the elements when they
     are scattered" — forget every dragged zone position, re-run the default
     arrangement and frame the whole world. */
  /* Re-run the arrangement against the live DOM without moving the camera.
     Every region swap can replace the outreach element with server HTML that
     carries no inline styles, and a density flip changes card heights — both
     leave regions/rows at stale coordinates (owner: "cards overlap when they
     expand"). */
  function relayout() {
    if (!world || !zones || !zones.length) return;
    applyDefaultLayout();
    applyStoredZones();
    scheduleWires();
    updateEdgeFades();
  }

  /* The pending chip lives in the toolbar, outside both swap targets, so it
     went stale after approvals (critique P2: "Pending review · 1" survived
     approving the last message). Recount it from the fresh outreach DOM on
     every region swap; hidden at zero (the markup is always rendered). */
  function syncPendingChip() {
    var chip = document.querySelector("[data-pending-chip]");
    if (!chip) return;
    var zone = document.querySelector('#outreach-root [data-msg-zone="pending_review"]');
    var n = zone ? zone.querySelectorAll(".msg-card").length : 0;
    var count = chip.querySelector(".pending-count");
    if (count) count.textContent = String(n);
    chip.hidden = n === 0;
    chip.setAttribute(
      "aria-label",
      "Pending review · " + n + ": " + n + " awaiting approval, show the messages region"
    );
  }

  function tidy() {
    if (!world) return;
    patchBoard(function (b) {
      b.zones = {};
    });
    applyDefaultLayout();
    applyStoredZones();
    scheduleWires();
    fit();
  }

  /* ----- layout (default arrangement, spec §2.3) ----- */

  function zoneKey(zone) {
    return zone.getAttribute("data-zone");
  }

  function applyDefaultLayout() {
    /* World arrangement (spec §V2.1): the setup-card cluster occupies the
       top row; the stage board's bands start below it; the outreach region
       sits to the right of the board's tallest row. Zones keep the base
       spec's row arithmetic (zones 264px wide, 48px gaps, band label 36px
       above the row). */
    var setup = world.querySelector('[data-region="setup"]');
    var setupLabel = world.querySelector('[data-region-label="setup"]');
    var boardLabel = world.querySelector('[data-region-label="board"]');
    var outreach = world.querySelector('[data-region="outreach"]');
    var outreachLabel = world.querySelector('[data-region-label="outreach"]');

    var dy = 0;
    if (setup && setupLabel) {
      setup.style.left = "0px";
      setup.style.top = setupLabel.offsetHeight + "px";
      setupLabel.style.left = "0px";
      setupLabel.style.top = "0px";
      /* Corridor budget (owner cleanup 2026-11-16): with wire annotations
         gone, this band only needs the two card→card exit lanes and the
         board trunk — 150px keeps the wires clear without a large empty
         gap between setup and the board. */
      dy = setup.offsetHeight + setupLabel.offsetHeight + 150;
    }

    var byBand = {};
    var bandOrder = [];
    zones.forEach(function (z) {
      var b = z.getAttribute("data-band") || "";
      if (!byBand[b]) {
        byBand[b] = [];
        bandOrder.push(b);
      }
      byBand[b].push(z);
    });
    var y = dy;
    var maxRight = 0;
    bandOrder.forEach(function (band) {
      var label = labels.filter(function (l) {
        return l.getAttribute("data-band") === band;
      })[0];
      if (label) {
        label.style.left = "0px";
        label.style.top = y + "px";
      }
      var rowY = y + BAND_LABEL_H + BAND_GAP;
      var x = 0;
      var maxH = 0;
      byBand[band].forEach(function (z) {
        z.style.left = x + "px";
        z.style.top = rowY + "px";
        x += ZONE_W + ZONE_GAP_X;
        maxH = Math.max(maxH, z.offsetHeight);
        maxRight = Math.max(maxRight, x - ZONE_GAP_X);
      });
      y = rowY + maxH + ZONE_GAP_Y;
    });

    if (boardLabel) {
      boardLabel.style.left = "0px";
      boardLabel.style.top = Math.max(0, dy - boardLabel.offsetHeight) + "px";
    }
    if (outreach && outreachLabel) {
      /* Board-focused fit must not leave a clipped message card peeking in
         at the right edge; 200px keeps the region fully off-canvas until
         the user fits Home/Outreach. */
      var ox = maxRight + 48;
      outreach.style.left = ox + "px";
      outreach.style.top = dy + "px";
      outreachLabel.style.left = ox + "px";
      outreachLabel.style.top = Math.max(0, dy - outreachLabel.offsetHeight) + "px";
    }
  }

  function applyStoredZones() {
    var stored = boardState().zones;
    zones.forEach(function (z) {
      var pos = stored[zoneKey(z)];
      if (
        pos &&
        typeof pos.x === "number" &&
        typeof pos.y === "number" &&
        isFinite(pos.x) &&
        isFinite(pos.y)
      ) {
        z.style.left = pos.x + "px";
        z.style.top = pos.y + "px";
      }
    });
  }

  /* ----- wires (spec §2.1/§2.3) ----- */

  function scheduleWires() {
    if (wirePending) return;
    wirePending = true;
    requestAnimationFrame(function () {
      wirePending = false;
      drawWires();
    });
  }

  /* Fixed system wires (spec §V2.1): cards → what they feed. Orthogonal bus
     routing — a wire never crosses a card: it exits its card into the grid
     gap (or just outside the cluster), runs down to the trunk line above the
     board label, along it, then drops onto the board's top edge. Read-only
     explanation of the flow: nothing here is a runtime dependency, and
     nothing on a wire can be rewired by the user. */
  var SVG_NS = "http://www.w3.org/2000/svg";

  /* Polyline with rounded corners (r px), for orthogonal routes. */
  function busPath(pts, r) {
    var d = "M" + pts[0].x + " " + pts[0].y;
    for (var i = 1; i < pts.length - 1; i++) {
      var p0 = pts[i - 1];
      var p1 = pts[i];
      var p2 = pts[i + 1];
      var d1 = Math.sqrt(Math.pow(p1.x - p0.x, 2) + Math.pow(p1.y - p0.y, 2)) || 1;
      var d2 = Math.sqrt(Math.pow(p2.x - p1.x, 2) + Math.pow(p2.y - p1.y, 2)) || 1;
      var rr = Math.min(r, d1 / 2, d2 / 2);
      var a = { x: p1.x - ((p1.x - p0.x) / d1) * rr, y: p1.y - ((p1.y - p0.y) / d1) * rr };
      var b = { x: p1.x + ((p2.x - p1.x) / d2) * rr, y: p1.y + ((p2.y - p1.y) / d2) * rr };
      d += " L" + a.x + " " + a.y + " Q" + p1.x + " " + p1.y + " " + b.x + " " + b.y;
    }
    var last = pts[pts.length - 1];
    return d + " L" + last.x + " " + last.y;
  }

  /* Stage flow connectors (Phase 1.4): orthogonal only. Within a lane the
     wire leaves the right edge and enters the next left edge at mid-height;
     between lanes it leaves the bottom edge, travels through the empty gap,
     and enters the next top edge. It never crosses a node. */
  function drawWires() {
    if (!wireGroup) return;
    while (wireGroup.firstChild) wireGroup.removeChild(wireGroup.firstChild);
    for (var i = 0; i < zones.length - 1; i++) {
      var a = zones[i];
      var b = zones[i + 1];
      var pts;
      if (a.getAttribute("data-band") === b.getAttribute("data-band")) {
        pts = [
          { x: a.offsetLeft + a.offsetWidth, y: a.offsetTop + a.offsetHeight / 2 },
          { x: b.offsetLeft, y: b.offsetTop + b.offsetHeight / 2 },
        ];
      } else {
        var ax = a.offsetLeft + a.offsetWidth / 2;
        var bx = b.offsetLeft + b.offsetWidth / 2;
        var aBottom = a.offsetTop + a.offsetHeight;
        var bTop = b.offsetTop;
        var midY = aBottom + (bTop - aBottom) / 2;
        pts = [
          { x: ax, y: aBottom },
          { x: ax, y: midY },
          { x: bx, y: midY },
          { x: bx, y: bTop },
        ];
      }
      var path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("d", busPath(pts, 10));
      path.setAttribute("class", "flow-wire");
      path.setAttribute("marker-end", "url(#pl-wire-arrow)");
      wireGroup.appendChild(path);
    }
    var b2 = contentBounds();
    if (b2 && vp) {
      var svg = wireGroup.parentNode;
      svg.setAttribute("width", Math.ceil(b2.w + b2.x) + 40);
      svg.setAttribute("height", Math.ceil(b2.h + b2.y) + 40);
    }
  }

  /* ----- zone drag (8px snap + alignment guides, spec §2.3) ----- */

  function snap(v) {
    return Math.round(v / GRID) * GRID;
  }

  function startZoneDrag(e, zone) {
    var startX = e.clientX;
    var startY = e.clientY;
    var originX = parseFloat(zone.style.left) || 0;
    var originY = parseFloat(zone.style.top) || 0;
    var active = false;

    var gV = document.createElement("div");
    gV.className = "alignment-guide v";
    gV.style.display = "none";
    var gH = document.createElement("div");
    gH.className = "alignment-guide h";
    gH.style.display = "none";

    function others() {
      return zones.filter(function (z) {
        return z !== zone;
      });
    }

    function bestAlign(val, size, axis) {
      /* axis 'x': compare left/center/right edges; 'y': top/middle/bottom. */
      var mine =
        axis === "x" ? [val, val + size / 2, val + size] : [val, val + size / 2, val + size];
      var best = null;
      others().forEach(function (o) {
        var ol = parseFloat(o.style.left) || 0;
        var ot = parseFloat(o.style.top) || 0;
        var theirs =
          axis === "x"
            ? [ol, ol + o.offsetWidth / 2, ol + o.offsetWidth]
            : [ot, ot + o.offsetHeight / 2, ot + o.offsetHeight];
        mine.forEach(function (m) {
          theirs.forEach(function (t) {
            var d = t - m;
            if (Math.abs(d) <= ALIGN_TOL && (best === null || Math.abs(d) < Math.abs(best.d))) {
              best = { d: d, at: t };
            }
          });
        });
      });
      return best;
    }

    function move(ev) {
      var sdx = ev.clientX - startX;
      var sdy = ev.clientY - startY;
      if (!active) {
        if (Math.hypot(sdx, sdy) < 4) return; // same 4px intent threshold
        active = true;
        document.body.style.userSelect = "none";
        e.preventDefault();
      }
      var wx = (sdx) / zoom;
      var wy = (sdy) / zoom;
      var nx = snap(originX + wx);
      var ny = snap(originY + wy);

      var ax = bestAlign(nx, zone.offsetWidth, "x");
      if (ax) nx += ax.d;
      var ay = bestAlign(ny, zone.offsetHeight, "y");
      if (ay) ny += ay.d;

      zone.style.left = nx + "px";
      zone.style.top = ny + "px";

      if (ax) {
        gV.style.display = "block";
        gV.style.left = ax.at + "px";
      } else gV.style.display = "none";
      if (ay) {
        gH.style.display = "block";
        gH.style.top = ay.at + "px";
      } else gH.style.display = "none";

      scheduleWires(); // rAF-throttled recompute (spec §2.3)
    }

    function up(ev) {
      zone.removeEventListener("pointermove", move);
      zone.removeEventListener("pointerup", up);
      zone.removeEventListener("pointercancel", up);
      document.body.style.userSelect = "";
      if (gV.parentNode) gV.parentNode.removeChild(gV);
      if (gH.parentNode) gH.parentNode.removeChild(gH);
      if (zone.hasPointerCapture && zone.hasPointerCapture(ev.pointerId)) {
        zone.releasePointerCapture(ev.pointerId);
      }
      if (active) {
        var key = zoneKey(zone);
        var x = parseFloat(zone.style.left) || 0;
        var y = parseFloat(zone.style.top) || 0;
        patchBoard(function (b) {
          b.zones[key] = { x: x, y: y };
        });
        scheduleWires();
      }
    }

    world.appendChild(gV);
    world.appendChild(gH);
    zone.setPointerCapture(e.pointerId);
    zone.addEventListener("pointermove", move);
    zone.addEventListener("pointerup", up);
    zone.addEventListener("pointercancel", up);
  }

  /* ----- pan ----- */

  function startPan(e) {
    var startX = e.clientX;
    var startY = e.clientY;
    var originX = panX;
    var originY = panY;
    var moved = false;
    vp.classList.add("panning");
    e.preventDefault();

    function move(ev) {
      var dx = ev.clientX - startX;
      var dy = ev.clientY - startY;
      if (!moved && Math.hypot(dx, dy) < 4) return;
      moved = true;
      panX = originX + dx;
      panY = originY + dy;
      applyTransform();
    }
    function up(ev) {
      vp.removeEventListener("pointermove", move);
      vp.removeEventListener("pointerup", up);
      vp.removeEventListener("pointercancel", up);
      vp.classList.remove("panning");
      if (vp.hasPointerCapture && vp.hasPointerCapture(ev.pointerId)) {
        vp.releasePointerCapture(ev.pointerId);
      }
      if (moved) scheduleSave();
    }
    vp.setPointerCapture(e.pointerId);
    vp.addEventListener("pointermove", move);
    vp.addEventListener("pointerup", up);
    vp.addEventListener("pointercancel", up);
  }

  function onPointerDown(e) {
    if (e.pointerType !== "mouse" && !e.isPrimary) return; // no pinch (spec §2.2)
    if (e.button !== 0) return;
    var t = e.target;
    if (t.closest && t.closest(".canvas-controls, .move-menu, .panel")) return;
    /* Setup cards and outreach message cards are links/forms that live
       outside `.canvas-zone`: pan must not capture their pointerdown, or
       the trailing click is retargeted to the viewport and panels never
       open (browser-verified). */
    if (t.closest && t.closest("a, button, form, input, select, textarea, label")) return;
    var zone = t.closest && t.closest(".canvas-zone");
    if (zone) {
      if (t.closest(".prospect-card") || t.closest("button, a, form, input, select, textarea")) {
        return; // board.js owns cards; controls behave normally
      }
      /* Zone drag: frame or header only (spec §2.3) — the card stack and
         the scrollable zone body keep their own behavior. */
      if (t.closest(".zone-head") || t === zone) startZoneDrag(e, zone);
      return;
    }
    startPan(e);
  }

  function onWheel(e) {
    if (!vp) return;
    e.preventDefault();
    /* Owner request 2026-11-16: plain wheel zooms at the pointer; Ctrl/Cmd +
       wheel moves the canvas (deltaX/deltaY). Trackpad pinch also arrives as
       ctrl+wheel, so pinch now pans rather than zooms. */
    if (e.ctrlKey || e.metaKey) {
      panX -= e.deltaX;
      panY -= e.deltaY;
      applyTransform();
      scheduleSave();
      return;
    }
    if (e.deltaY === 0) return;
    zoomAt(zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1), e.clientX, e.clientY);
  }

  /* ----- keyboard (spec §6.2 — Phase A: zoom keys) ----- */

  function typingTarget(e) {
    return (
      e.target &&
      e.target.matches &&
      e.target.matches("input, textarea, select, [contenteditable]")
    );
  }

  document.addEventListener("keydown", function (e) {
    if (!vp) return;
    if (typingTarget(e)) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (window.PlPanel && window.PlPanel.isOpen()) return;
    if (document.body.classList.contains("move-menu-open")) return;
    var k = e.key;
    if (k === "+" || k === "=") {
      e.preventDefault();
      zoomCenter(ZOOM_STEP);
    } else if (k === "-" || k === "_") {
      e.preventDefault();
      zoomCenter(1 / ZOOM_STEP);
    } else if (k === "0") {
      e.preventDefault();
      resetZoom();
    } else if (k === "f") {
      e.preventDefault();
      fitBoardReadable();
    }
  });

  /* ----- init / (re)bind ----- */

  function init() {
    vp = document.querySelector("[data-canvas-viewport]");
    if (!vp) return;
    world = vp.querySelector("[data-canvas-world]");
    wireGroup = world ? world.querySelector("[data-wire-lines]") : null;
    zones = world ? Array.prototype.slice.call(world.querySelectorAll(".canvas-zone")) : [];
    labels = world ? Array.prototype.slice.call(world.querySelectorAll(".band-label")) : [];
    if (!world) return;

    applyDefaultLayout();
    applyStoredZones();

    /* First load: a stage lens frames its single zone regardless of stored
       pan/zoom (shared `?stage=` links must be readable); otherwise restore
       the stored view, or fit the board. */
    var b = boardState();
    var lensStage = new URLSearchParams(window.location.search).get("stage");
    if (lensStage) {
      /* Filters leave one/few cards: keep lanes + Messages in view, never
         auto-zoom past 125%. */
      fitBoardReadable(1.25);
    } else if (
      typeof b.zoom === "number" &&
      typeof b.panX === "number" &&
      typeof b.panY === "number"
    ) {
      zoom = clampZoom(b.zoom);
      panX = b.panX;
      panY = b.panY;
      applyTransform();
      updateZoomReadout();
      updateDensity();
    } else {
      /* Phase 1.1 — readable fit-to-view (min 0.92, pan instead of shrink).
         /outreach opens on its own message region. */
      if (window.location.pathname === "/outreach") fitOutreach();
      else fitBoardReadable();
    }

    vp.addEventListener("pointerdown", onPointerDown);
    vp.addEventListener("wheel", onWheel, { passive: false });

    scheduleWires();
    updateEdgeFades();
  }

  /* Controls cluster (real buttons, spec §2.2) + rail view actions (§V2.4). */
  document.addEventListener("click", function (e) {
    var viewBtn = e.target.closest && e.target.closest("[data-canvas-view]");
    if (viewBtn && vp) {
      var what = viewBtn.getAttribute("data-canvas-view");
      if (what === "world") fit();
      else if (what === "board") fitBoardReadable();
      else if (what === "outreach") fitOutreach();
      /* "You are here" must agree with itself (critique P1): the clicked view
         owns .active, aria-current, the h1 and the URL — screen readers and
         the Back button both get the truth. */
      var viewLinks = document.querySelectorAll(".nav-link[data-canvas-view]");
      for (var vi = 0; vi < viewLinks.length; vi++) {
        var link = viewLinks[vi];
        var isIt = link.getAttribute("data-canvas-view") === what;
        link.classList.toggle("active", isIt);
        if (isIt) link.setAttribute("aria-current", link.tagName === "A" ? "page" : "true");
        else link.removeAttribute("aria-current");
      }
      var heading = document.querySelector(".canvas-toolbar h1");
      if (heading) heading.textContent = what === "outreach" ? "Message pipeline" : "Prospect pipeline";
      var path = what === "outreach" ? "/outreach" : (window.location.pathname === "/" && what !== "board" ? "/" : "/prospects");
      if (window.history && window.history.replaceState && window.location.pathname !== path) {
        window.history.replaceState(null, "", path + window.location.search);
      }
      return;
    }
    var btn = e.target.closest && e.target.closest("[data-canvas]");
    if (!btn || !vp) return;
    var action = btn.getAttribute("data-canvas");
    if (action === "in") zoomCenter(ZOOM_STEP);
    else if (action === "out") zoomCenter(1 / ZOOM_STEP);
    else if (action === "reset") resetZoom();
    else if (action === "fit") fitBoardReadable();
    else if (action === "tidy") tidy();
  });

  /* Board swaps in place (HTMX): re-init against the fresh DOM. */
  document.addEventListener("htmx:afterSwap", function (e) {
    var t = e.detail.target;
    if (!t) return;
    if (t.id === "board-root") init(); // fresh zones → rebind + relayout
    else if (t.id === "outreach-root") {
      relayout(); // fresh region → reposition
      syncPendingChip(); // and the toolbar count is now fresh too
    }
  });

  document.addEventListener("focusin", function (e) {
    var el = e.target.closest && e.target.closest(".setup-card, .prospect-card");
    if (el) ensureVisible(el);
  });

  window.PlCanvas = {
    init: init,
    requestWires: scheduleWires,
    patchBoard: patchBoard,
    fit: fit,
    fitBoard: fitBoard,
    fitBoardReadable: fitBoardReadable,
    fitOutreach: fitOutreach,
    focusCard: focusCard,
    tidy: tidy,
    relayout: relayout,
    zoom: function () {
      return zoom;
    },
    worldPoint: function (clientX, clientY) {
      var rect = vp.getBoundingClientRect();
      return { x: (clientX - rect.left - panX) / zoom, y: (clientY - rect.top - panY) / zoom };
    },
    viewport: function () {
      return vp;
    },
    world: function () {
      return world;
    },
    store: function () {
      return store;
    },
  };

  /* Viewport resize (owner 2026-11-17): wires are geometry, so they always
     recompute; crossing the stacked/wide breakpoint rebuilds the arrangement
     the world was laid out for — saved coordinates only fit the layout they
     were dragged in, and stale ones leave connectors scattered. */
  var lastLayoutMode = window.innerWidth < 1024 ? "stacked" : "wide";
  var resizeFrame = 0;
  window.addEventListener("resize", function () {
    if (resizeFrame) return;
    resizeFrame = window.requestAnimationFrame(function () {
      resizeFrame = 0;
      if (!vp || !world) return;
      var mode = window.innerWidth < 1024 ? "stacked" : "wide";
      updateEdgeFades(); // viewport rect changed even when the mode did not
      if (mode !== lastLayoutMode) {
        lastLayoutMode = mode;
        patchBoard(function (b) {
          b.zones = {};
        });
        applyDefaultLayout();
        applyStoredZones();
        fit();
      }
      scheduleWires();
    });
  });

  init();
})();
