/* Auth-page behaviours (spec §2.8). These pages are standalone: they do not
   load app.js, which carries canvas-only shortcuts (j/k, g-chords, "/") that
   have no business firing on a sign-in form. CSP forbids an inline script
   (script-src 'self'), so this is a file. */

/* Password visibility: toggle in place, keeping the caret where it was. */
/* An autofocused field is a convenience, not a keyboard-focus signal: mark it
   so CSS suppresses the ring on load, then drop the mark on blur so the next
   keyboard focus is visible (login/signup/reset share these standalone pages). */
function quietAutofocus() {
  var input = document.querySelector("input[autofocus]");
  if (!input) return;
  input.classList.add("autofocused");
  input.removeAttribute("autofocus");
  input.addEventListener("blur", function () {
    input.classList.remove("autofocused");
  }, { once: true });
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", quietAutofocus, { once: true });
} else {
  quietAutofocus();
}

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
