/* Prospectly canvas engine (docs/design/pipeline-canvas.md §2).
   World layer + pan/zoom (0.5–2.0) + phase bands/stage zones/wires +
   zone drag (8px snap, alignment guides) + density flip at 0.8 +
   pl.canvas.v1 localStorage (debounced, quota-swallowing, version-banded).
   Constants mirror canvas.css tokens. Vanilla JS, CSP-safe (CSSOM only). */
(function () {
  "use strict";

  /* ----- constants (mirrored in canvas.css) ----- */
  var ZONE_W = 264;
  var ZONE_GAP = 48;
  var BAND_LABEL_H = 36;
  var GRID = 8;
  var ALIGN_TOL = 4; // spec §2.3: guides at ±4px edge/center alignment
  var MIN_ZOOM = 0.5;
  var MAX_ZOOM = 2;
  var ZOOM_STEP = 1.25;
  var DENSITY_FLOOR = 0.8; // spec §2.5
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
  var setupOrigin = { x: 0, y: 0 }; // world origin of .setup-cluster

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
    /* Low zoom turns wire labels into colliding microtext (browser-verified
       at fit: "dedupesflags inactivefilters"). Keep the connectors and drop
       the annotations below 0.75; card/zone chrome uses the density switch. */
    world.classList.toggle("labels-hidden", zoom < 0.75);
  }

  function updateDensity() {
    if (!vp) return;
    var screen = vp.parentNode;
    screen.classList.toggle("density-compact", zoom < DENSITY_FLOOR);
    screen.classList.toggle("density-full", zoom >= DENSITY_FLOOR);
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

  function fitElement(el, pad) {
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
    var z = Math.min((rect.width - p * 2) / b.w, (rect.height - p * 2) / b.h, 1);
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

  /* Fit just the stage board (rail "Board") — the board's wrapper has no
     layout box of its own because zones are absolutely positioned. */
  function fitBoard() {
    var parts = zones.concat(labels);
    if (!parts.length) return;
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    parts.forEach(function (el) {
      minX = Math.min(minX, el.offsetLeft);
      minY = Math.min(minY, el.offsetTop);
      maxX = Math.max(maxX, el.offsetLeft + el.offsetWidth);
      maxY = Math.max(maxY, el.offsetTop + el.offsetHeight);
    });
    fitElement(
      { offsetLeft: minX, offsetTop: minY, offsetWidth: maxX - minX, offsetHeight: maxY - minY },
      56
    );
  }

  function fitOutreach() {
    fitElement(world ? world.querySelector('[data-region="outreach"]') : null, 56);
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
      /* Corridor budget (spec §V2.1): the space between the setup cluster
         and the board label carries the card→card lanes, both label rows and
         the trunk. 170px is the minimum that fits them all at 0.5 zoom,
         where counter-scaled labels are ~20px tall in world units. */
      /* Corridor budget (spec §V2.1): the band between the setup cluster and
         the board label carries the two card→card exit lanes and labels, both
         board-label rows and the trunk. At 72px the board-label rows landed
         *inside* the cluster. 230px gives ≥8px between every baseline at 0.5
         zoom — the worst case, where counter-scaled labels are 20px tall in
         world units. Width still bounds fit() at 1440×900, so this costs no
         zoom: height has ~90px of slack before it binds. */
      dy = setup.offsetHeight + setupLabel.offsetHeight + 230;
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
      var rowY = y + BAND_LABEL_H;
      var x = 0;
      var maxH = 0;
      byBand[band].forEach(function (z) {
        z.style.left = x + "px";
        z.style.top = rowY + "px";
        x += ZONE_W + ZONE_GAP;
        maxH = Math.max(maxH, z.offsetHeight);
        maxRight = Math.max(maxRight, x - ZONE_GAP);
      });
      y = rowY + maxH + ZONE_GAP;
    });

    if (boardLabel) {
      boardLabel.style.left = "0px";
      boardLabel.style.top = Math.max(0, dy - boardLabel.offsetHeight) + "px";
    }
    if (outreach && outreachLabel) {
      var ox = maxRight + 120;
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
      drawSystemWires();
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

  function wire(g, pts, label, labelAt, anchor) {
    var path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", busPath(pts, 10));
    path.setAttribute("class", "system-wire");
    path.setAttribute("marker-end", "url(#pl-wire-arrow)");
    g.appendChild(path);
    if (label && labelAt) {
      var text = document.createElementNS(SVG_NS, "text");
      text.setAttribute("class", "wire-label");
      text.setAttribute("x", labelAt.x);
      text.setAttribute("y", labelAt.y);
      text.setAttribute("text-anchor", anchor || "middle");
      /* Counter-scale so a label keeps its ~10px on-screen size at every
         zoom — spec §V2.8 requires it legible at 0.5. Redrawn on every
         setView, so this never goes stale. */
      text.style.fontSize = 10 / zoom + "px";
      text.textContent = label;
      g.appendChild(text);
    }
  }

  function midY(card) {
    /* Cards live inside .setup-cluster (their offsetParent), so world
       coordinates need the cluster's origin added back. */
    var y = setupOrigin.y + card.offsetTop;
    return y + Math.min(card.offsetHeight / 2, 36);
  }

  function cardBox(card) {
    return {
      x: setupOrigin.x + card.offsetLeft,
      y: setupOrigin.y + card.offsetTop,
      w: card.offsetWidth,
      h: card.offsetHeight,
    };
  }

  function drawSystemWires() {
    var g = world ? world.querySelector("[data-system-wires]") : null;
    if (!g) return;
    while (g.firstChild) g.removeChild(g.firstChild);

    var setup = world.querySelector('[data-region="setup"]');
    var boardLabel = world.querySelector('[data-region-label="board"]');
    if (!setup || !boardLabel || !zones.length) return;
    setupOrigin = { x: setup.offsetLeft, y: setup.offsetTop };
    var clusterBottom = setupOrigin.y + setup.offsetHeight;

    /* First zone row's top edge — the drops land there, past the region and
       band label rows (both sit left of every drop x, see dropX below). */
    var zoneTop = zones.reduce(function (m, z) {
      return Math.min(m, z.offsetTop);
    }, Infinity);
    var busY = boardLabel.offsetTop - 6; // trunk, just above the board label
    var boardRight = zones.reduce(function (m, z) {
      return Math.max(m, z.offsetLeft + z.offsetWidth);
    }, 0);

    var toBoard = Array.prototype.filter.call(
      world.querySelectorAll(".setup-card[data-wire-target]"),
      function (c) {
        return c.getAttribute("data-wire-target") === "board";
      }
    );

    /* One bus per grid column: every card of a column leaves through the same
       20px gap (or open space right of the last column), so six wires become
       three verticals instead of six near-parallel lines. */
    var cols = [];
    toBoard.forEach(function (card) {
      var col = null;
      for (var i = 0; i < cols.length; i++) {
        if (cols[i].left === card.offsetLeft) col = cols[i];
      }
      if (!col) {
        col = { left: card.offsetLeft, cards: [] };
        cols.push(col);
      }
      col.cards.push(card);
    });

    /* Per-column trunk (spec §V2.1). Column pitch is 244px (232 card + 12
       gap) and every bus sits 6px right of its column, so:
       - the drop stops 40px short of the *next* bus — otherwise its trunk
         would run through the next column's corner and the two would merge;
       - the label centres in the free window between this bus and the next,
         which is also the only place a label can sit without a bus vertical
         crossing its text (buses run all the way down to the trunk);
       - drops stay inside a zone of the first row (zone geometry is fixed in
         CSS: 264px wide, 48px gap), clear of the region label at x < 246.
       The last column has no neighbour, so it gets the full 246px run. */
    var RUN = 246;
    var LAST_RUN = 246;
    var minDrop = Math.max(300, boardLabel.offsetWidth + 24);

    var buses = cols.map(function (col) {
      var box = cardBox(col.cards[0]);
      return box.x + box.w + 6;
    });

    var routes = cols.map(function (col, i) {
      var busX = buses[i];
      var dropX = i + 1 < buses.length ? Math.min(busX + RUN, buses[i + 1] - 40) : busX + LAST_RUN;
      if (dropX < minDrop) dropX = minDrop;
      if (dropX > boardRight - 40) dropX = boardRight - 40;
      var labelX = (busX + (i + 1 < buses.length ? buses[i + 1] : busX + 256)) / 2;
      return { col: col, busX: busX, dropX: dropX, labelX: labelX };
    });

    routes.forEach(function (r) {
      r.col.cards.forEach(function (card, k) {
        var box = cardBox(card);
        var sy = midY(card);
        /* Two label rows stacked above the trunk: 0.5-zoom glyphs are ~20px
           tall in world units, so 35px of baseline separation keeps them
           clear of each other, of the card→card labels above and of the
           trunk below. */
        var labelY = k === 0 ? busY - 26 : busY - 61;
        wire(
          g,
          [
            { x: box.x + box.w, y: sy },
            { x: r.busX, y: sy },
            { x: r.busX, y: busY },
            { x: r.dropX, y: busY },
            { x: r.dropX, y: zoneTop },
          ],
          card.getAttribute("data-wire-label"),
          { x: r.labelX, y: labelY }
        );
      });
    });

    /* Card → card (spec §V2.3: Usage → Providers, General → Profile). Both
       must reach a card in a *different* column, and both column gaps are
       already occupied by board buses, so both wires leave through the
       corridor under the cluster and climb outside its right edge — the only
       vertical space no card occupies. The outer wire takes the upper lane,
       so the two lanes never intersect; descents land on the target's top
       edge. Labels ride the exit segment in the corridor, where no bus
       vertical reaches. */
    var laneNear = setupOrigin.y - 6;
    var laneFar = setupOrigin.y - 20;
    var clusterRight = setupOrigin.x + setup.offsetWidth;
    Array.prototype.forEach.call(
      world.querySelectorAll(".setup-card[data-wire-target^='card:']"),
      function (card, idx) {
        var target = world.querySelector(
          '[data-setup-card="' + card.getAttribute("data-wire-target").slice(5) + '"]'
        );
        if (!target) return;
        var box = cardBox(card);
        var tgt = cardBox(target);
        var lane = idx === 0 ? laneNear : laneFar;
        var vx = clusterRight + 40 + idx * 24;
        var exitY = clusterBottom + 20 + idx * 48;
        var sx = box.x + box.w / 2;
        var tx = tgt.x + tgt.w / 2;
        wire(
          g,
          [
            { x: sx, y: box.y + box.h },
            { x: sx, y: exitY },
            { x: vx, y: exitY },
            { x: vx, y: lane },
            { x: tx, y: lane },
            { x: tx, y: tgt.y },
          ],
          card.getAttribute("data-wire-label"),
          { x: sx + 60, y: exitY + 29 }
        );
      }
    );

    /* Board (02 OUTREACH band) → outreach region: out of the band's right
       edge, through the empty gutter right of the board, into the region. */
    var outreach = world.querySelector('[data-region="outreach"]');
    if (outreach) {
      var bandZones = zones.filter(function (z) {
        return z.getAttribute("data-band") === "02";
      });
      var src = (bandZones.length ? bandZones : zones).reduce(function (m, z) {
        return z.offsetLeft + z.offsetWidth > m.offsetLeft + m.offsetWidth ? z : m;
      });
      var sx = src.offsetLeft + src.offsetWidth;
      var sy = src.offsetTop + Math.min(src.offsetHeight / 2, 40);
      var ox = outreach.offsetLeft;
      var oy = outreach.offsetTop + 60;
      var gutter = ox > boardRight ? boardRight + (ox - boardRight) / 2 : ox - 40;
      wire(
        g,
        [
          { x: sx, y: sy },
          { x: gutter, y: sy },
          { x: gutter, y: oy },
          { x: ox, y: oy },
        ],
        outreach.getAttribute("data-wire-label") || "messages in flight",
        { x: (sx + gutter) / 2, y: sy - 8 }
      );
    }
  }

  function drawWires() {
    if (!wireGroup) return;
    while (wireGroup.firstChild) wireGroup.removeChild(wireGroup.firstChild);
    for (var i = 0; i < zones.length - 1; i++) {
      var a = zones[i];
      var b = zones[i + 1];
      var line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      var x1, y1, x2, y2;
      if (a.getAttribute("data-band") === b.getAttribute("data-band")) {
        /* Same band row: straight right-mid → left-mid (spec §2.3). */
        x1 = a.offsetLeft + a.offsetWidth;
        y1 = a.offsetTop + a.offsetHeight / 2;
        x2 = b.offsetLeft;
        y2 = b.offsetTop + b.offsetHeight / 2;
      } else {
        /* Band transition: bottom-mid → top-mid through the empty gap
           between rows. The literal right→left reading is geometrically
           unavoidable clipping under stacked rows; order/arrow semantics
           are identical (decision recorded in the Phase A report). */
        x1 = a.offsetLeft + a.offsetWidth / 2;
        y1 = a.offsetTop + a.offsetHeight;
        x2 = b.offsetLeft + b.offsetWidth / 2;
        y2 = b.offsetTop;
      }
      line.setAttribute("x1", x1);
      line.setAttribute("y1", y1);
      line.setAttribute("x2", x2);
      line.setAttribute("y2", y2);
      line.setAttribute("marker-end", "url(#pl-wire-arrow)");
      wireGroup.appendChild(line);
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
      fit();
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

    /* First load: fit-to-content capped at 1.0 — unless a stored view
       exists (spec §2.2/§2.6). */
    var b = boardState();
    if (typeof b.zoom === "number" && typeof b.panX === "number" && typeof b.panY === "number") {
      zoom = clampZoom(b.zoom);
      panX = b.panX;
      panY = b.panY;
      applyTransform();
      updateZoomReadout();
      updateDensity();
    } else {
      /* Opening view = fit the whole world (spec §2.1/§V2.1). Wire labels
         are hidden below 0.75 so the overview does not become colliding
         microtext; Home (rail / g h) re-fits the same world. /outreach
         starts framed on its own region. */
      if (window.location.pathname === "/outreach") fitOutreach();
      else fit();
    }

    vp.addEventListener("pointerdown", onPointerDown);
    vp.addEventListener("wheel", onWheel, { passive: false });

    scheduleWires();
  }

  /* Controls cluster (real buttons, spec §2.2) + rail view actions (§V2.4). */
  document.addEventListener("click", function (e) {
    var viewBtn = e.target.closest && e.target.closest("[data-canvas-view]");
    if (viewBtn && vp) {
      var what = viewBtn.getAttribute("data-canvas-view");
      if (what === "world") fit();
      else if (what === "board") fitBoard();
      else if (what === "outreach") fitOutreach();
      return;
    }
    var btn = e.target.closest && e.target.closest("[data-canvas]");
    if (!btn || !vp) return;
    var action = btn.getAttribute("data-canvas");
    if (action === "in") zoomCenter(ZOOM_STEP);
    else if (action === "out") zoomCenter(1 / ZOOM_STEP);
    else if (action === "reset") resetZoom();
    else if (action === "fit") fit();
  });

  /* Board swaps in place (HTMX): re-init against the fresh DOM. */
  document.addEventListener("htmx:afterSwap", function (e) {
    var t = e.detail.target;
    if (t && t.id === "board-root") init();
  });

  window.PlCanvas = {
    init: init,
    requestWires: scheduleWires,
    patchBoard: patchBoard,
    fit: fit,
    fitBoard: fitBoard,
    fitOutreach: fitOutreach,
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
    store: function () {
      return store;
    },
  };

  init();
})();
