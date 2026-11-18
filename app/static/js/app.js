/* Prospectly UI behaviors — CSP-safe (no inline handlers; app/main.py CSP).
   External only: script-src 'self'. */
(function () {
  "use strict";

  /* ----- Sidebar drawer (mobile <768px): data-nav-toggle / data-nav-close -----
     Same body.nav-open mechanism as the old top-bar dropdown, plus the a11y
     contract: aria-expanded on the toggle, focus moves to the first nav link
     on open, Tab is trapped inside the open drawer, Escape or a backdrop /
     close-button click closes and returns focus to the toggle. At >=768px the
     sidebar is persistent, so the drawer state is cleared on resize. */
  var toggle = document.querySelector("[data-nav-toggle]");
  var sidenav = document.getElementById("sidenav");

  var navIsOpen = function () {
    return document.body.classList.contains("nav-open");
  };

  var closeNav = function (restoreFocus) {
    if (!navIsOpen()) return;
    document.body.classList.remove("nav-open");
    if (toggle) toggle.setAttribute("aria-expanded", "false");
    if (restoreFocus && toggle) toggle.focus();
  };

  var openNav = function () {
    document.body.classList.add("nav-open");
    if (toggle) toggle.setAttribute("aria-expanded", "true");
    if (sidenav) {
      var first = sidenav.querySelector(".nav-link");
      if (first) first.focus();
    }
  };

  if (toggle && sidenav) {
    toggle.addEventListener("click", function (e) {
      e.stopPropagation();
      if (navIsOpen()) closeNav(true);
      else openNav();
    });

    // Backdrop and the close button carry data-nav-close.
    document.querySelectorAll("[data-nav-close]").forEach(function (el) {
      el.addEventListener("click", function () {
        closeNav(true);
      });
    });

    // Click outside the drawer closes it.
    document.addEventListener("click", function (e) {
      if (!navIsOpen()) return;
      if (e.target.closest(".sidenav")) return;
      closeNav(false);
    });

    document.addEventListener("keydown", function (e) {
      if (!navIsOpen()) return;
      if (e.key === "Escape") {
        // Drawer is the top layer while open — don't also close the help.
        e.stopImmediatePropagation();
        closeNav(true);
        return;
      }
      // Focus trap: Tab / Shift+Tab cycle within the open drawer.
      if (e.key === "Tab") {
        var items = sidenav.querySelectorAll("a[href], button:not([disabled])");
        if (!items.length) return;
        var first = items[0];
        var last = items[items.length - 1];
        var active = document.activeElement;
        if (e.shiftKey && (active === first || !sidenav.contains(active))) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && active === last) {
          e.preventDefault();
          first.focus();
        } else if (!sidenav.contains(active)) {
          e.preventDefault();
          first.focus();
        }
      }
    });

    // Close after choosing a section (a real href navigates; this just
    // restores the closed state for same-page/modified clicks).
    sidenav.querySelectorAll(".nav-link").forEach(function (link) {
      link.addEventListener("click", function () {
        closeNav(false);
      });
    });

    // >=768px: the sidebar is persistent — drop any leftover drawer state.
    window.addEventListener("resize", function () {
      if (navIsOpen() && window.innerWidth >= 768) closeNav(false);
    });
  }

  /* ----- Destructive confirms: <form data-confirm="..."> -----
     Delegated to document in the CAPTURE phase so that (a) forms injected
     by HTMX swaps (research page polling) are covered — one-time binding
     missed them, silently dropping the Delete confirmation — and (b) the
     confirm runs before htmx's own submit handler can fire the request. */
  document.addEventListener(
    "submit",
    function (e) {
      var form = e.target;
      if (!(form instanceof HTMLFormElement)) return;
      var message = form.getAttribute("data-confirm");
      if (message && !window.confirm(message)) {
        e.preventDefault();
        e.stopImmediatePropagation();
      }
    },
    true
  );

  /* Same confirm for controls that are NOT form submits (the canvas Arrange
     button — critique heuristic-3: one misclick discarded a hand-arranged
     canvas with no undo). Capture phase, so a cancel stops the delegated
     canvas handler before it runs. Controls inside forms are skipped: their
     form already confirms at submit, and confirming twice is its own bug. */
  document.addEventListener(
    "click",
    function (e) {
      var el = e.target.closest && e.target.closest("[data-confirm]");
      if (!el || el.closest("form")) return;
      if (!window.confirm(el.getAttribute("data-confirm"))) {
        e.preventDefault();
        e.stopImmediatePropagation();
      }
    },
    true
  );

  /* ----- Whole-row navigation: <tr data-href="/prospects/1"> ----- */
  document.querySelectorAll("tr[data-href]").forEach(function (row) {
    row.addEventListener("click", function (e) {
      // Let real interactive elements inside the row behave normally.
      if (e.target.closest("a, button, input, select, textarea, label, form")) return;
      window.location.href = row.getAttribute("data-href");
    });
  });

  /* ----- Toast notifications: dismiss + auto-dismiss -----
     Delegated + observer-armed so toasts that arrive inside HTMX panel or
     board fragments (and JS-created error toasts) behave like page toasts. */
  var armToast = function (toast) {
    if (!toast || toast.dataset.toastArmed) return;
    toast.dataset.toastArmed = "1";
    setTimeout(function () {
      toast.classList.add("dismiss");
    }, 6000);
  };
  document.querySelectorAll(".toast").forEach(armToast);

  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-dismiss]");
    if (!btn) return;
    var id = btn.getAttribute("data-dismiss");
    var target = (id && document.getElementById(id)) || btn.closest(".toast");
    if (target) target.classList.add("dismiss");
  });

  if ("MutationObserver" in window) {
    new MutationObserver(function (mutations) {
      mutations.forEach(function (m) {
        Array.prototype.forEach.call(m.addedNodes, function (n) {
          if (n.nodeType !== 1) return;
          if (n.classList && n.classList.contains("toast")) armToast(n);
          if (n.querySelectorAll) n.querySelectorAll(".toast").forEach(armToast);
        });
      });
    }).observe(document.body, { childList: true, subtree: true });
  }

  /* Saved-view picker (canvas toolbar): native select navigates to the
     existing apply endpoint, which 303s to /prospects with its filters. */
  document.addEventListener("change", function (e) {
    var sel = e.target.closest && e.target.closest("[data-saved-view]");
    if (sel && sel.value) window.location.assign(sel.value);
  });

  /* ----- Instant hide: [data-hide="id"] adds .hidden to the target -----
     (The help overlay hides via .hidden; .dismiss only styles .toast, so
     reusing data-dismiss for it was a silent no-op — browser-verified.) */
  document.querySelectorAll("[data-hide]").forEach(function (el) {
    el.addEventListener("click", function () {
      var target = document.getElementById(el.getAttribute("data-hide"));
      if (target) target.classList.add("hidden");
    });
  });

  /* Shortcuts entry points (rail Shortcuts button, spec §1.1): opening the
     help over an open drawer would hide it behind the drawer's z-stack, so
     the drawer closes first (capture phase, before the toggle handler). */
  document.addEventListener(
    "click",
    function (e) {
      var btn = e.target.closest && e.target.closest("[data-show-help]");
      if (!btn) return;
      if (typeof navIsOpen === "function" && navIsOpen()) closeNav(false);
    },
    true
  );
  document.querySelectorAll("[data-show-help]").forEach(function (el) {
    el.addEventListener("click", function () {
      showHelp();
    });
  });

  /* ----- Keyboard shortcuts (UI/UX spec §8, Pipeline Canvas §6.2) -----
     Progressive enhancement; all have UI equivalents. Sequence keys use a
     short buffer (g then d = dashboard). Ignored when typing in an input;
     board keys yield while a slide-over panel is open (the panel layer owns
     the keyboard then — Escape closes it first, spec §6.2 priority). */
  var help = document.getElementById("shortcuts-help");
  var showHelp = function () {
    if (help) help.classList.toggle("hidden");
  };
  var panelIsOpen = function () {
    if (window.PlPanel && window.PlPanel.isOpen) return window.PlPanel.isOpen();
    return !!document.querySelector("#panel-root .panel");
  };
  var keyBuffer = "";
  var bufferTimer = null;

  document.addEventListener("keydown", function (e) {
    // Ignore when the user is typing in a field (guard: e.target may be a
    // non-Element, e.g. a programmatically dispatched event).
    if (e.target && e.target.matches && e.target.matches("input, textarea, select, [contenteditable]")) return;

    if (e.key === "?") {
      if (panelIsOpen()) return;
      e.preventDefault();
      showHelp();
      return;
    }
    if (e.key === "Escape") {
      if (help && !help.classList.contains("hidden")) {
        help.classList.add("hidden");
      }
      return;
    }

    // '/' focuses the first search input on the page. The canvas has none
    // until the Filters panel opens, so fall through to opening it and focus
    // its search when the body lands (spec §6.2 — documented and dead until
    // now, critique P2 run 2).
    if (e.key === "/") {
      if (panelIsOpen()) return;
      e.preventDefault();
      var search = document.querySelector('input[name="q"], input[type="search"]');
      if (search) {
        search.focus();
        return;
      }
      var filterOpener = document.querySelector(".filter-open[data-panel-href]");
      if (filterOpener) {
        filterOpener.click();
        var tries = 0;
        var focusFilterSearch = function () {
          var field = document.querySelector(
            "#panel-root input[name='q'], #panel-root input[type='search']"
          );
          if (field) {
            field.focus();
            if (field.select) field.select();
            return;
          }
          if (++tries < 40) setTimeout(focusFilterSearch, 50); // ~2s for HTMX
        };
        setTimeout(focusFilterSearch, 50);
      }
      return;
    }

    // j/k move a highlight down/up within row lists AND across the board's
    // cards (DOM order = canonical stage order, spec §6.2).
    if (e.key === "j" || e.key === "k") {
      if (panelIsOpen()) return;
      e.preventDefault();
      var rows = document.querySelectorAll("tbody tr, .prospect-card");
      var current = document.querySelector("tbody tr.highlight, .prospect-card.highlight");
      var idx = rows.length ? Array.prototype.indexOf.call(rows, current) : -1;
      var next = idx;
      if (e.key === "j") next = Math.min(idx + 1, rows.length - 1);
      else next = Math.max(idx - 1, 0);
      if (current) current.classList.remove("highlight");
      if (rows[next]) {
        rows[next].classList.add("highlight");
        rows[next].scrollIntoView({ block: "nearest" });
      }
      return;
    }

    // Enter opens the focused/highlighted board card (spec §3.1). Only the
    // card itself (or the bare page with a highlight) is intercepted —
    // buttons, links and panel controls keep their native Enter.
    if (e.key === "Enter" && !panelIsOpen()) {
      var active = document.activeElement;
      if (active && active.classList && active.classList.contains("prospect-card")) {
        e.preventDefault();
        active.click();
        return;
      }
      if (active === document.body) {
        var highlighted = document.querySelector(".prospect-card.highlight");
        if (highlighted) {
          e.preventDefault();
          highlighted.click();
          return;
        }
      }
    }

    // Sequence keys: buffer single-letter keys, clear after 1s.
    keyBuffer += e.key;
    clearTimeout(bufferTimer);
    bufferTimer = setTimeout(function () { keyBuffer = ""; }, 1000);
    if (keyBuffer.length === 2 && keyBuffer[0] === "g") {
      var target = keyBuffer[1];
      keyBuffer = "";
      clearTimeout(bufferTimer);
      if (panelIsOpen()) return;
      if (target === "h") {
        e.preventDefault();
        if (window.PlCanvas) window.PlCanvas.fit();
      } else if (target === "p") {
        e.preventDefault();
        if (window.PlCanvas) window.PlCanvas.fitBoardReadable();
      } else if (target === "o") {
        e.preventDefault();
        if (window.PlCanvas) window.PlCanvas.fitOutreach();
      } else if (target === "s") {
        e.preventDefault();
        if (window.PlPanel) window.PlPanel.open("/status", { width: 560 });
        else window.location.href = "/status";
      }
    }
  });
  /* Chip previews for comma-separated list fields (profile + vocabulary).
     The underlying input still submits the same comma-separated value, so
     the backend contract is unchanged. */
  function initChipInputs(root) {
    var scope = root && root.querySelectorAll ? root : document;
    scope.querySelectorAll("input[data-chip-input]").forEach(function (input) {
      if (input.dataset.chipReady) return;
      input.dataset.chipReady = "1";
      var list = document.createElement("div");
      list.className = "chip-list";
      input.insertAdjacentElement("afterend", list);
      var draw = function () {
        list.innerHTML = "";
        input.value
          .split(",")
          .map(function (s) {
            return s.trim();
          })
          .filter(Boolean)
          .forEach(function (term) {
            var chip = document.createElement("span");
            chip.className = "chip";
            chip.appendChild(document.createTextNode(term));
            var remove = document.createElement("button");
            remove.type = "button";
            remove.className = "chip-remove";
            remove.setAttribute("aria-label", "Remove " + term);
            remove.textContent = "×";
            remove.addEventListener("click", function () {
              var parts = input.value
                .split(",")
                .map(function (s) {
                  return s.trim();
                })
                .filter(Boolean)
                .filter(function (t) {
                  return t !== term;
                });
              input.value = parts.join(", ");
              draw();
            });
            chip.appendChild(remove);
            list.appendChild(chip);
          });
      };
      input.addEventListener("input", draw);
      draw();
    });
  }

  initChipInputs(document);
  document.addEventListener("htmx:afterSwap", function (e) {
    initChipInputs(e.detail.target);
  });

  /* Duplicates slider: reflect the score and apply after a short pause
     instead of requiring a Refresh button. */
  var mergeTimer = null;
  document.addEventListener("input", function (e) {
    var slider = e.target.closest && e.target.closest("[data-merge-score]");
    if (!slider) return;
    var form = slider.closest("form");
    var readout = form && form.querySelector("[data-merge-readout]");
    if (readout) readout.textContent = "Score: " + Number(slider.value).toFixed(2);
    clearTimeout(mergeTimer);
    mergeTimer = setTimeout(function () {
      if (form) form.requestSubmit();
    }, 450);
  });

  /* Stage selector: destructive-by-default is impossible — the submit is
     disabled until the user picks a real transition. */
  document.addEventListener("change", function (e) {
    var sel = e.target.closest && e.target.closest("[data-stage-select]");
    if (!sel) return;
    var form = sel.closest("form");
    var btn = form && form.querySelector("[data-stage-submit]");
    if (btn) btn.disabled = !sel.value;
  });

  /* Active section indicator: canvas views are buttons, Status is a link.
     The existing .active rail treatment (accent edge + tint) is reused. */
  (function markRailSection() {
    var p = window.location.pathname;
    var selector = null;
    if (p === "/") selector = '[data-canvas-view="world"]';
    else if (p === "/status") selector = 'a[href="/status"]';
    else if (p === "/outreach") selector = '[data-canvas-view="outreach"]';
    else if (p.indexOf("/prospects") === 0) selector = '[data-canvas-view="board"]';
    if (!selector) return;
    var el = document.querySelector(".sidenav " + selector);
    if (!el) return;
    el.classList.add("active");
    el.setAttribute("aria-current", el.tagName === "A" ? "page" : "true");
  })();
})();
