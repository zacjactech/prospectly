/* Prospectly board interactions (docs/design/pipeline-canvas.md §8.1 step 3).
   Card drag → stage transition (4px threshold, ghost, zone hit-testing,
   optimistic move, POST /prospects/{id}/stage, 409 revert + danger toast),
   keyboard/pointer Move-to menu with server-rendered legal transitions,
   cardOrder persistence, board refresh + loading/error states.
   Drag performs NOTHING but a stage transition — no outreach endpoint is
   ever called from here (P1–P3). */
(function () {
  "use strict";

  var DRAG_THRESHOLD = 4;
  var CLICK_SUPPRESS_MS = 350;
  var MENU_CAP = 500; // spec §2.6: cardOrder capped at 500/zone

  var zones = [];
  var lastDragEnd = 0;
  var selectedCard = null;
  var moveMenu = null; // {menu, card, parent, next, invoker}

  /* ----- helpers ----- */

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : "";
  }

  function humanize(stage) {
    return String(stage || "").replace(/_/g, " ");
  }

  function toast(type, text) {
    if (window.PlPanel && window.PlPanel.toast) window.PlPanel.toast(type, text);
  }

  function announce(text) {
    var live = document.querySelector("[data-board-status]");
    if (live) live.textContent = text;
  }

  function screen() {
    var root = document.getElementById("board-root");
    return root ? root.querySelector(".canvas-screen") : null;
  }

  function zoneForStage(stage) {
    for (var i = 0; i < zones.length; i++) {
      var stages = (zones[i].getAttribute("data-stages") || "").split(/\s+/);
      if (stages.indexOf(stage) !== -1) return zones[i];
    }
    return null;
  }

  function zoneBody(zone) {
    return zone.querySelector(".zone-body");
  }

  /* ----- counts (spec §3.2: mono count, hot dots, terminal sub-badges) ----- */

  function recount(zone) {
    if (!zone) return;
    var cards = zone.querySelectorAll(".prospect-card");
    var n = cards.length;
    var due = 0;
    var review = 0;
    Array.prototype.forEach.call(cards, function (c) {
      if (c.getAttribute("data-followup") === "due") due += 1;
      if (c.getAttribute("data-review") === "1") review += 1;
    });
    var count = zone.querySelector("[data-zone-count]");
    if (count) {
      var num = count.querySelector("[data-count]");
      if (num) num.textContent = String(n);
      var sr = count.querySelector(".sr-only");
      if (sr) sr.textContent = n === 1 ? " prospect" : " prospects";
      if (due > 0) count.setAttribute("data-hot", "warn");
      else if (review > 0) count.setAttribute("data-hot", "accent");
      else count.removeAttribute("data-hot");
    }
    Array.prototype.forEach.call(zone.querySelectorAll("[data-sub-stage]"), function (sub) {
      var stage = sub.getAttribute("data-sub-stage");
      var c = zone.querySelectorAll('.prospect-card[data-stage="' + stage + '"]').length;
      var numEl = sub.querySelector("[data-count]");
      if (numEl) numEl.textContent = String(c);
    });
  }

  function recountAll() {
    zones.forEach(recount);
  }

  /* ----- cardOrder persistence (spec §2.6) ----- */

  function orderKey(zone) {
    return zone.getAttribute("data-zone");
  }

  function persistOrder(zone) {
    if (!zone) return;
    var ids = [];
    Array.prototype.forEach.call(zone.querySelectorAll(".prospect-card"), function (c) {
      ids.push(c.getAttribute("data-id"));
    });
    if (window.PlCanvas) {
      window.PlCanvas.patchBoard(function (b) {
        b.cardOrder[orderKey(zone)] = ids;
      });
    }
  }

  function applyCardOrder() {
    if (!window.PlCanvas) return;
    var store = window.PlCanvas.store();
    var board = store.boards && store.boards.prospects;
    var order = (board && board.cardOrder) || {};
    zones.forEach(function (zone) {
      var key = orderKey(zone);
      var stored = order[key];
      var body = zoneBody(zone);
      if (!body) return;
      var server = Array.prototype.slice.call(body.querySelectorAll(".prospect-card"));
      if (!stored || !Array.isArray(stored)) return;
      if (stored.length > MENU_CAP) {
        /* Beyond the cap → server order wins for this zone; prune the store. */
        persistOrder(zone);
        return;
      }
      var byId = {};
      server.forEach(function (c) {
        byId[c.getAttribute("data-id")] = c;
      });
      var seen = {};
      var frag = document.createDocumentFragment();
      var moved = false;
      stored.forEach(function (id) {
        var card = byId[id];
        if (card && !seen[id]) {
          seen[id] = 1;
          frag.appendChild(card);
          moved = true;
        }
      });
      server.forEach(function (c) {
        var id = c.getAttribute("data-id");
        if (!seen[id]) {
          seen[id] = 1;
          frag.appendChild(c);
          moved = true;
        }
      });
      if (!moved || !server.length) return;
      /* Re-insert cards after the (hidden) skeleton so state order stays. */
      var skeleton = body.querySelector(".zone-skeleton");
      var anchor = skeleton ? skeleton.nextSibling : body.firstChild;
      body.insertBefore(frag, anchor);
      persistOrder(zone); // prunes unknown ids back to the server payload
    });
  }

  /* ----- stage POST ----- */

  function postStage(id, stage) {
    return fetch("/prospects/" + encodeURIComponent(id) + "/stage", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-CSRF-Token": csrfToken(),
      },
      body: "stage=" + encodeURIComponent(stage),
    })
      .then(function (res) {
        if (res.ok) return { ok: true, status: res.status, msg: "" };
        return res.text().then(
          function (text) {
            var msg = String(text || "").trim();
            if (!msg && res.status === 403) msg = "Missing or invalid CSRF token.";
            if (!msg) msg = "The server rejected this move (HTTP " + res.status + ").";
            return { ok: false, status: res.status, msg: msg };
          },
          function () {
            return { ok: false, status: res.status, msg: "The server rejected this move." };
          }
        );
      })
      .catch(function () {
        return { ok: false, status: 0, msg: "Network unavailable: the move was not saved." };
      });
  }

  /* ----- optimistic move + revert ----- */

  function updateStageVisual(card, stage) {
    card.setAttribute("data-stage", stage);
    var badge = card.querySelector("[data-stage-badge]");
    if (badge) {
      badge.textContent = humanize(stage);
      badge.className = "badge pc-stage-tag stage-" + stage.replace(/_/g, "-");
      badge.title = humanize(stage);
    }
  }

  function snapshotStage(card) {
    var badge = card.querySelector("[data-stage-badge]");
    return {
      stage: card.getAttribute("data-stage"),
      text: badge ? badge.textContent : null,
      cls: badge ? badge.className : null,
      title: badge ? badge.title : null,
    };
  }

  function restoreStage(card, snap) {
    card.setAttribute("data-stage", snap.stage);
    var badge = card.querySelector("[data-stage-badge]");
    if (badge && snap.text !== null) {
      badge.textContent = snap.text;
      badge.className = snap.cls;
      badge.title = snap.title || snap.text;
    }
  }

  /* Move the card into the target zone (optimistic) and POST. On failure:
     snap back to the origin slot + danger toast with the server reason. */
  function performMove(card, targetZone, targetStage, origin) {
    var snap = origin.snap;
    var targetBody = zoneBody(targetZone);
    var sourceZone = origin.zone;

    if (targetBody) targetBody.appendChild(card);
    card.classList.remove("dragging", "snapback");
    card.style.left = "";
    card.style.top = "";
    card.style.width = "";
    card.style.transform = "";

    updateStageVisual(card, targetStage);
    recount(sourceZone);
    recount(targetZone);
    if (window.PlCanvas) window.PlCanvas.requestWires();

    announce(
      "Moving " +
        ((card.querySelector(".pc-name") || {}).textContent || "card").trim() +
        "…"
    );
    postStage(card.getAttribute("data-id"), targetStage).then(function (result) {
      if (result.ok) {
        /* The ghost keeps the source slot valid until the server decides;
           only now can it go. */
        if (origin.ghost && origin.ghost.parentNode) {
          origin.ghost.parentNode.removeChild(origin.ghost);
        }
        if (sourceZone !== targetZone) persistOrder(sourceZone);
        persistOrder(targetZone);
        announce("Card moved to " + humanize(targetStage));
        return;
      }
      /* Revert (409 / network / CSRF): snap back 120ms + reason toast. */
      restoreStage(card, snap);
      if (origin.parent) {
        var ghost = origin.ghost;
        if (ghost && ghost.parentNode) {
          origin.parent.insertBefore(card, ghost);
          ghost.parentNode.removeChild(ghost);
        } else {
          origin.parent.insertBefore(card, origin.next && origin.next.parentNode === origin.parent ? origin.next : null);
        }
      }
      snapBack(card, origin.dragRect);
      recount(sourceZone);
      recount(targetZone);
      if (window.PlCanvas) window.PlCanvas.requestWires();
      toast("error", result.msg);
      announce(result.msg);
    });
  }

  /* Animate the card from its drag position back into its slot (spec §3.1
     rejected-409: snap back 120ms ease-out). */
  function snapBack(card, dragRect) {
    if (!dragRect) return;
    var slot = card.getBoundingClientRect();
    var dx = dragRect.left - slot.left;
    var dy = dragRect.top - slot.top;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
    card.style.transform = "translate(" + dx + "px, " + dy + "px)";
    void card.offsetWidth; // flush the start value
    card.classList.add("snapback");
    card.style.transform = "";
    setTimeout(function () {
      card.classList.remove("snapback");
      card.style.transform = "";
    }, 200);
  }

  /* ----- card drag ----- */

  function zoneAt(clientX, clientY) {
    for (var i = 0; i < zones.length; i++) {
      var r = zones[i].getBoundingClientRect();
      if (clientX >= r.left && clientX <= r.right && clientY >= r.top && clientY <= r.bottom) {
        return zones[i];
      }
    }
    return null;
  }

  function startCardDrag(e, card) {
    var vp = window.PlCanvas && window.PlCanvas.viewport();
    if (!vp) return;
    var pointerId = e.pointerId;
    var startX = e.clientX;
    var startY = e.clientY;
    var active = false;
    var ghost = null;
    var origin = null;
    var grab = { x: 0, y: 0 };
    var dropZone = null;

    function setDrop(zone) {
      if (dropZone === zone) return;
      if (dropZone) dropZone.classList.remove("drop-target");
      dropZone = zone;
      if (dropZone) {
        var label = dropZone.querySelector(".drop-label");
        if (label) {
          label.textContent =
            "Move to " + humanize(dropZone.getAttribute("data-drop-stage"));
        }
        dropZone.classList.add("drop-target");
      }
    }

    function activate() {
      active = true;
      document.body.style.userSelect = "none";
      var rect = card.getBoundingClientRect();
      var pt = window.PlCanvas.worldPoint(startX, startY);
      var cardPt = window.PlCanvas.worldPoint(rect.left, rect.top);
      grab.x = pt.x - cardPt.x;
      grab.y = pt.y - cardPt.y;

      origin = {
        parent: card.parentNode,
        next: card.nextSibling,
        zone: card.closest(".canvas-zone"),
        snap: snapshotStage(card),
        dragRect: null,
        ghost: null,
      };
      ghost = document.createElement("div");
      ghost.className = "pc-ghost";
      ghost.style.height = card.offsetHeight + "px";
      origin.parent.insertBefore(ghost, origin.next);
      origin.ghost = ghost;

      card.style.width = card.offsetWidth + "px";
      card.classList.add("dragging");
      window.PlCanvas.viewport().appendChild(card); // world layer, above zones
      /* Capture only once the gesture becomes a drag — capturing on
         pointerdown would retarget the trailing click and break
         click-to-open (browser-verified reasoning, see Phase A report). */
      try {
        window.PlCanvas.viewport().setPointerCapture(pointerId);
      } catch (err) {
        /* capture is a nicety; document listeners still receive moves */
      }
      position(startX, startY);
    }

    function position(clientX, clientY) {
      var pt = window.PlCanvas.worldPoint(clientX, clientY);
      card.style.left = pt.x - grab.x + "px";
      card.style.top = pt.y - grab.y + "px";
    }

    function move(ev) {
      if (!active) {
        if (Math.hypot(ev.clientX - startX, ev.clientY - startY) < DRAG_THRESHOLD) return;
        activate();
        e.preventDefault();
      }
      position(ev.clientX, ev.clientY);
      setDrop(zoneAt(ev.clientX, ev.clientY));
    }

    function finish(ev) {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", finish);
      document.removeEventListener("pointercancel", finish);
      document.body.style.userSelect = "";
      if (vp.hasPointerCapture && vp.hasPointerCapture(ev.pointerId)) {
        vp.releasePointerCapture(ev.pointerId);
      }
      if (!active) return;
      lastDragEnd = Date.now();

      var target = dropZone;
      setDrop(null);
      if (origin) origin.dragRect = card.getBoundingClientRect();

      if (!target) {
        // Dropped on empty canvas → snap back, no request (spec §0.1).
        revertToOrigin(card, origin);
        return;
      }
      var targetStage = target.getAttribute("data-drop-stage");
      if (!targetStage || targetStage === card.getAttribute("data-stage")) {
        revertToOrigin(card, origin);
        return;
      }
      performMove(card, target, targetStage, origin);
    }

    function revertToOrigin(el, o) {
      if (o.ghost && o.ghost.parentNode) {
        o.parent.insertBefore(el, o.ghost);
        o.ghost.parentNode.removeChild(o.ghost);
      }
      el.classList.remove("dragging");
      el.style.left = "";
      el.style.top = "";
      el.style.width = "";
      snapBack(el, o.dragRect);
      recount(o.zone);
      if (window.PlCanvas) window.PlCanvas.requestWires();
    }

    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", finish);
    document.addEventListener("pointercancel", finish);
    /* No capture and no preventDefault here: the gesture may still turn out
       to be a plain click, which must reach the card unretargeted. */
  }

  /* ----- move menu (spec §3.1: role=menu, server-rendered transitions) ----- */

  function openMoveMenu(card) {
    if (!card) return;
    closeMoveMenu(false);
    var menu = card.querySelector("[data-move-menu]");
    if (!menu) return;
    moveMenu = {
      menu: menu,
      card: card,
      parent: card,
      next: menu.nextSibling,
      invoker: document.activeElement,
    };
    document.body.appendChild(menu);
    menu.hidden = false;
    document.body.classList.add("move-menu-open");

    var rect = card.getBoundingClientRect();
    var mw = menu.offsetWidth;
    var mh = menu.offsetHeight;
    var left = Math.min(rect.left, window.innerWidth - mw - 8);
    var top = rect.bottom + 4;
    if (top + mh > window.innerHeight - 8) top = Math.max(8, rect.top - mh - 4);
    menu.style.left = Math.max(8, left) + "px";
    menu.style.top = top + "px";

    var items = menu.querySelectorAll("button:not(:disabled)");
    if (items.length) items[0].focus();
    else {
      menu.setAttribute("tabindex", "-1");
      menu.focus();
    }
  }

  function closeMoveMenu(restoreFocus) {
    if (!moveMenu) return;
    var m = moveMenu;
    moveMenu = null;
    m.menu.hidden = true;
    if (m.next && m.next.parentNode === m.parent) m.parent.insertBefore(m.menu, m.next);
    else m.parent.appendChild(m.menu);
    document.body.classList.remove("move-menu-open");
    if (restoreFocus && m.invoker && document.contains(m.invoker)) m.invoker.focus();
  }

  function menuItems() {
    if (!moveMenu) return [];
    return Array.prototype.slice.call(
      moveMenu.menu.querySelectorAll("button:not(:disabled)")
    );
  }

  /* ----- events ----- */

  document.addEventListener("pointerdown", function (e) {
    if (e.button !== 0) return;
    var card = e.target.closest && e.target.closest(".prospect-card");
    if (!card) return;
    if (e.target.closest("button, a, form, input, select, textarea, label")) return;
    if (moveMenu && !moveMenu.menu.contains(e.target)) closeMoveMenu(false);
    startCardDrag(e, card);
  });

  /* Card click → detail panel (spec §3.1). Drags and inner controls are
     excluded: a real drag suppresses the trailing click. */
  document.addEventListener("click", function (e) {
    if (Date.now() - lastDragEnd < CLICK_SUPPRESS_MS) return;
    var card = e.target.closest && e.target.closest(".prospect-card");
    if (!card) return;
    if (e.target.closest("button, a, form, input, select, textarea, label")) return;
    if (!window.PlPanel) return;
    if (selectedCard && selectedCard !== card) selectedCard.classList.remove("selected");
    selectedCard = card;
    card.classList.add("selected");
    window.PlPanel.open("/prospects/" + encodeURIComponent(card.getAttribute("data-id")), {
      width: 560,
      invoker: card,
    });
    /* Phase 1.2 — pan (never zoom) so the selected card stays fully visible
       beside the drawer. */
    if (window.PlCanvas && window.PlCanvas.focusCard) {
      requestAnimationFrame(function () {
        window.PlCanvas.focusCard(card);
      });
    }
  });

  document.addEventListener("pl:panel-closed", function () {
    if (selectedCard) {
      selectedCard.classList.remove("selected");
      selectedCard = null;
    }
  });

  /* ⋯ button on the card (focus-reachable parity for `m`). */
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-card-move]");
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    openMoveMenu(btn.closest(".prospect-card"));
  });

  /* Menu item → same POST as drag (arrows + Enter parity, spec §3.1). */
  document.addEventListener("click", function (e) {
    if (!moveMenu) return;
    var item = e.target.closest && e.target.closest("[data-move-to]");
    if (!item || !moveMenu.menu.contains(item)) return;
    var card = moveMenu.card;
    var stage = item.getAttribute("data-move-to");
    closeMoveMenu(false);
    var zone = zoneForStage(stage);
    if (!card || !zone) return;
    var origin = {
      parent: card.parentNode,
      next: card.nextSibling,
      zone: card.closest(".canvas-zone"),
      snap: snapshotStage(card),
      dragRect: card.getBoundingClientRect(),
      ghost: null,
    };
    performMove(card, zone, stage, origin);
  });

  /* Click outside the menu closes it. */
  document.addEventListener("pointerdown", function (e) {
    if (!moveMenu) return;
    if (moveMenu.menu.contains(e.target)) return;
    if (e.target.closest && e.target.closest("[data-card-move]")) return;
    closeMoveMenu(false);
  }, true);

  /* `m` opens the menu for the highlighted (or focused) card. */
  document.addEventListener("keydown", function (e) {
    if (e.key !== "m" || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.target && e.target.matches && e.target.matches("input, textarea, select, [contenteditable]")) return;
    if (window.PlPanel && window.PlPanel.isOpen()) return;
    var card =
      document.querySelector(".prospect-card.highlight") ||
      (document.activeElement && document.activeElement.closest
        ? document.activeElement.closest(".prospect-card")
        : null);
    if (!card) return;
    e.preventDefault();
    if (moveMenu && moveMenu.card === card) closeMoveMenu(true);
    else openMoveMenu(card);
  });

  /* Escape: panel (panel.js, earlier capture) → move menu → help/drawer. */
  document.addEventListener(
    "keydown",
    function (e) {
      if (e.key !== "Escape" || !moveMenu) return;
      e.stopImmediatePropagation();
      closeMoveMenu(true);
    },
    true
  );

  /* Arrow navigation inside the menu (spec §3.1: arrows + Enter). */
  document.addEventListener("keydown", function (e) {
    if (!moveMenu) return;
    var items = menuItems();
    if (!items.length) return;
    var idx = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      var next =
        e.key === "ArrowDown"
          ? (idx + 1) % items.length
          : (idx - 1 + items.length) % items.length;
      if (idx === -1) next = 0;
      items[next].focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      items[0].focus();
    } else if (e.key === "End") {
      e.preventDefault();
      items[items.length - 1].focus();
    }
  });

  /* ----- board refresh: loading / error / swap (spec §2.4) ----- */

  function boardTarget(e) {
    var t = e.detail && (e.detail.target || (e.detail.elt && e.detail.elt.closest("#board-root")));
    return t && t.id === "board-root" ? t : null;
  }

  function setLoading(on) {
    var s = screen();
    if (!s) return;
    s.classList.toggle("is-loading", on);
    var err = s.querySelector("[data-board-error]");
    if (on && err) err.hidden = true;
  }

  function showBoardError(status) {
    var s = screen();
    if (!s) return;
    var err = s.querySelector("[data-board-error]");
    if (!err) return;
    err.textContent =
      "Couldn't refresh the board (HTTP " +
      status +
      "). The last loaded board is still shown.";
    err.hidden = false;
  }

  document.addEventListener("htmx:beforeRequest", function (e) {
    if (boardTarget(e)) setLoading(true);
  });

  document.addEventListener("htmx:afterRequest", function (e) {
    if (boardTarget(e)) setLoading(false);
  });

  document.addEventListener("htmx:responseError", function (e) {
    if (boardTarget(e)) {
      setLoading(false);
      var xhr = e.detail && e.detail.xhr;
      showBoardError(xhr ? xhr.status : 0);
    }
  });

  document.addEventListener("htmx:sendError", function (e) {
    if (boardTarget(e)) {
      setLoading(false);
      showBoardError(0);
    }
  });

  /* ----- init / (re)bind ----- */

  function init() {
    var root = document.getElementById("board-root");
    if (!root || !window.PlCanvas) return;
    zones = Array.prototype.slice.call(root.querySelectorAll(".canvas-zone"));
    applyCardOrder();
    recountAll();
    if (window.PlCanvas) window.PlCanvas.requestWires();
  }

  document.addEventListener("htmx:afterSwap", function (e) {
    if (boardTarget(e)) init();
  });

  var refreshPending = false;
  window.PlBoard = {
    init: init,
    refresh: function () {
      if (!window.htmx || refreshPending) return;
      refreshPending = true;
      /* HTMX can report the POST and its redirected GET as separate
         requests; coalesce the burst into one board + outreach refresh. */
      setTimeout(function () {
        refreshPending = false;
        /* Panel posts can change a card's stage and/or a message's state.
           Refresh both regions from their own fragment endpoints so the
           overview never goes stale (spec §4/§V2.1). */
        var root = document.getElementById("board-root");
        if (root) {
          var boardUrl =
            "/prospects" + (location.pathname === "/prospects" ? location.search : "");
          htmx.ajax("GET", boardUrl, { target: root, swap: "innerHTML" });
        }
        var outreach = document.getElementById("outreach-root");
        if (outreach) {
          htmx.ajax("GET", "/outreach", { target: outreach, swap: "outerHTML" });
        }
      }, 60);
    },
  };

  init();
})();
