/* Auth-page behaviours (spec §2.8). These pages are standalone: they do not
   load app.js, which carries canvas-only shortcuts (j/k, g-chords, "/") that
   have no business firing on a sign-in form. CSP forbids an inline script
   (script-src 'self'), so this is a file. */

/* Password visibility: toggle in place, keeping the caret where it was. */
document.addEventListener("click", function (e) {
  var btn = e.target.closest && e.target.closest("[data-pw-toggle]");
  if (!btn) return;
  var wrap = btn.closest(".pw-wrap");
  var input = wrap && wrap.querySelector("input");
  if (!input) return;
  var start = input.selectionStart;
  var end = input.selectionEnd;
  var wasHidden = input.type === "password";
  input.type = wasHidden ? "text" : "password";
  wrap.classList.toggle("showing", wasHidden);
  btn.setAttribute("aria-label", wasHidden ? "Hide password" : "Show password");
  if (wasHidden) {
    try {
      input.setSelectionRange(start, end);
    } catch (err) {
      /* some engines refuse selection on a freshly shown field */
    }
  }
});
