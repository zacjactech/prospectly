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

  /* ----- Whole-row navigation: <tr data-href="/prospects/1"> ----- */
  document.querySelectorAll("tr[data-href]").forEach(function (row) {
    row.addEventListener("click", function (e) {
      // Let real interactive elements inside the row behave normally.
      if (e.target.closest("a, button, input, select, textarea, label, form")) return;
      window.location.href = row.getAttribute("data-href");
    });
  });

  /* ----- Toast notifications: dismiss + auto-dismiss ----- */
  document.querySelectorAll("[data-dismiss]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var target = document.getElementById(btn.getAttribute("data-dismiss"));
      if (target) target.classList.add("dismiss");
    });
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
  document.querySelectorAll(".toast").forEach(function (toast) {
    setTimeout(function () { toast.classList.add("dismiss"); }, 6000);
  });

  /* ----- Keyboard shortcuts (UI/UX spec §8) -----
     Progressive enhancement; all have UI equivalents. Sequence keys use a
     short buffer (g then d = dashboard). Ignored when typing in an input. */
  var help = document.getElementById("shortcuts-help");
  var showHelp = function () {
    if (help) help.classList.toggle("hidden");
  };
  var keyBuffer = "";
  var bufferTimer = null;

  document.addEventListener("keydown", function (e) {
    // Ignore when the user is typing in a field.
    if (e.target.matches("input, textarea, select, [contenteditable]")) return;

    if (e.key === "?") {
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

    // '/' focuses the first search input on the page.
    if (e.key === "/") {
      e.preventDefault();
      var search = document.querySelector('input[name="q"], input[type="search"]');
      if (search) search.focus();
      return;
    }

    // j/k move a highlight down/up within row lists.
    if (e.key === "j" || e.key === "k") {
      e.preventDefault();
      var rows = document.querySelectorAll("tbody tr");
      var current = document.querySelector("tbody tr.highlight");
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

    // Sequence keys: buffer single-letter keys, clear after 1s.
    keyBuffer += e.key;
    clearTimeout(bufferTimer);
    bufferTimer = setTimeout(function () { keyBuffer = ""; }, 1000);
    if (keyBuffer === "gd") {
      keyBuffer = "";
      clearTimeout(bufferTimer);
      window.location.href = "/";
    }
  });
})();
