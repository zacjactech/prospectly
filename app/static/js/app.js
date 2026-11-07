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

  /* ----- Destructive confirms: <form data-confirm="..."> ----- */
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.getAttribute("data-confirm") || "Are you sure?")) {
        e.preventDefault();
      }
    });
  });

  /* ----- Whole-row navigation: <tr data-href="/prospects/1"> ----- */
  document.querySelectorAll("tr[data-href]").forEach(function (row) {
    row.addEventListener("click", function (e) {
      // Let real interactive elements inside the row behave normally.
      if (e.target.closest("a, button, input, select, textarea, label, form")) return;
      window.location.href = row.getAttribute("data-href");
    });
  });
})();
