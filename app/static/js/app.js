/* Prospectly UI behaviors — CSP-safe (no inline handlers; app/main.py CSP).
   External only: script-src 'self'. */
(function () {
  "use strict";

  /* ----- Mobile nav: data-nav-toggle -> body.nav-open ----- */
  var toggle = document.querySelector("[data-nav-toggle]");
  if (toggle) {
    toggle.addEventListener("click", function (e) {
      e.stopPropagation();
      document.body.classList.toggle("nav-open");
    });

    // Close when clicking outside the topbar.
    document.addEventListener("click", function (e) {
      if (!document.body.classList.contains("nav-open")) return;
      if (e.target.closest(".topbar")) return;
      document.body.classList.remove("nav-open");
    });

    // Close on Escape.
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") document.body.classList.remove("nav-open");
    });

    // Close after choosing a section.
    document.querySelectorAll(".topbar-right .nav-link").forEach(function (link) {
      link.addEventListener("click", function () {
        document.body.classList.remove("nav-open");
      });
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
