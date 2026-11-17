/* Prospectly slide-over panel system (docs/design/pipeline-canvas.md §4).
   Modal dialog at every breakpoint: slide-over + backdrop, HTMX fragment
   loading into the panel body, focus trap, Escape/X/backdrop close with
   invoker focus restore, dirty-form confirm, two-level stacking >=1400px,
   in-place replace with cached "← Back" below that.
   CSP: script-src 'self' — no inline handlers; geometry via CSSOM only. */
(function () {
  "use strict";

  var root = document.getElementById("panel-root");
  if (!root) return;

  var stack = []; // topmost panel = stack[stack.length - 1]
  var titleSeq = 0;
  var STACK_BREAKPOINT = 1400;
  /* Panel-originated requests keyed by their trigger element. The element may
     be detached by the time htmx:afterRequest fires (the panel body was
     swapped), so a WeakMap, not a DOM lookup, ties the response to its panel.
     Background board/outreach refreshes never enter this map. */
  var panelRequests = new WeakMap();

  /* ----- helpers ----- */

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : "";
  }

  function canvasViewport() {
    return document.querySelector("[data-canvas-viewport]");
  }

  function panelFor(elt) {
    if (!elt || !elt.closest) return null;
    var el = elt.closest(".panel");
    if (!el) return null;
    for (var i = 0; i < stack.length; i++) {
      if (stack[i].el === el) return stack[i];
    }
    return null;
  }

  function summarizeError(text, status) {
    var stripped = String(text || "")
      .replace(/<[^>]*>/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    if (!stripped) return "Request failed (HTTP " + status + ").";
    return stripped.length > 240 ? stripped.slice(0, 240) + "…" : stripped;
  }

  /* Toast used for transport/validation errors (spec §6.3 aria-live).
     Markup mirrors partials/flashes.html so the visual language matches. */
  function toast(type, text) {
    var host = document.querySelector(".toasts");
    if (!host) {
      host = document.createElement("div");
      host.className = "toasts";
      host.setAttribute("aria-live", "polite");
      host.setAttribute("aria-label", "Notifications");
      document.body.appendChild(host);
    }
    var t = document.createElement("div");
    t.className = "toast " + type;
    var span = document.createElement("span");
    span.textContent = text;
    t.appendChild(span);
    var close = document.createElement("button");
    close.className = "toast-close";
    close.setAttribute("data-dismiss", "");
    close.setAttribute("aria-label", "Dismiss");
    close.textContent = "×";
    t.appendChild(close);
    host.appendChild(t);
    setTimeout(function () {
      t.classList.add("dismiss");
    }, 6000);
  }

  function widthExpr(px) {
    /* spec §4: width min(Wpx, 100vw − 72px) — via CSSOM (CSP-safe). */
    return "min(" + px + "px, calc(100vw - 72px))";
  }

  function panelBodyError(panel) {
    panel.body.setAttribute("aria-busy", "false");
    panel.body.innerHTML = "";
    var wrap = document.createElement("div");
    wrap.className = "panel-error";
    var p = document.createElement("p");
    p.className = "muted";
    p.textContent = "Couldn't load this panel. Check your connection and try again.";
    var retry = document.createElement("button");
    retry.className = "btn";
    retry.type = "button";
    retry.textContent = "Retry";
    retry.addEventListener("click", function () {
      load(panel, panel.url);
    });
    wrap.appendChild(p);
    wrap.appendChild(retry);
    panel.body.appendChild(wrap);
  }

  /* ----- panel lifecycle ----- */

  function load(panel, url) {
    panel.url = url;
    panel.body.setAttribute("aria-busy", "true");
    panel.body.innerHTML = '<div class="panel-loading" role="status">Loading…</div>';
    htmx.ajax("GET", url, { target: panel.body, swap: "innerHTML" });
  }

  function assignTitle(panel) {
    var title = panel.body.querySelector("[data-panel-title]");
    if (!title) return;
    title.id = "panel-title-" + ++titleSeq;
    panel.el.setAttribute("aria-labelledby", title.id);
  }

  /* Keep anchored section labels clear of the sticky header and show the
     header shadow only once the body has scrolled. */
  function syncPanelScroll(panel) {
    if (!panel || !panel.body) return;
    var header = panel.body.querySelector(".detail-head, .message-head, .panel-heading");
    if (header) panel.body.style.scrollPaddingTop = header.offsetHeight + 8 + "px";
    panel.el.classList.toggle("panel-scrolled", panel.body.scrollTop > 0);
  }

  function setInert(on) {
    var vp = canvasViewport();
    if (!vp) return;
    if (on) vp.setAttribute("inert", "");
    else vp.removeAttribute("inert");
  }

  function buildPanel(opts) {
    var backdrop = document.createElement("div");
    backdrop.className = "panel-backdrop";
    backdrop.setAttribute("data-panel-close", "");

    var el = document.createElement("div");
    el.className = "panel";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "true");
    el.style.width = widthExpr(opts.width || 560);

    var body = document.createElement("div");
    body.className = "panel-body";

    var close = document.createElement("button");
    close.className = "panel-close";
    close.type = "button";
    close.setAttribute("data-panel-close", "");
    close.setAttribute("aria-label", "Close panel");
    close.innerHTML =
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"' +
      ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg>';

    el.appendChild(body);
    el.appendChild(close);
    root.appendChild(backdrop);
    root.appendChild(el);

    var panel = {
      el: el,
      body: body,
      backdrop: backdrop,
      closeBtn: close,
      invoker: opts.invoker || null,
      width: opts.width || 560,
      url: "",
      dirty: false,
      dirtyCheck: !!opts.dirtyCheck,
      back: null, // {source:'open'|'post', html?, url, dirtyCheck} — restore
      prePostUrl: null,
    };

    /* Dirty tracking: any input inside an editor panel arms the confirm
       (spec §4 beforeinput flag). */
    body.addEventListener(
      "beforeinput",
      function (e) {
        if (!panel.dirtyCheck) return;
        var t = e.target;
        if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) {
          panel.dirty = true;
        }
      },
      true
    );

    requestAnimationFrame(function () {
      el.classList.add("open");
    });
    close.focus();
    return panel;
  }

  function shiftForStack(top) {
    /* Park the lower panel left by the top panel's resolved width. */
    var w = top.el.getBoundingClientRect().width;
    var lower = stack[0];
    lower.el.style.right = Math.round(w) + "px";
    lower.el.classList.add("stacked");
  }

  function unstack() {
    if (stack.length === 1) {
      stack[0].el.classList.remove("stacked");
      stack[0].el.style.right = "";
    }
  }

  function replaceInPlace(panel, url, opts) {
    /* <1400px: the new surface replaces the current one in place; the old
       content is cached for the fragment's ← Back link, no refetch (spec §4). */
    panel.back = {
      source: "open",
      html: panel.body.innerHTML,
      url: panel.url,
      dirtyCheck: panel.dirtyCheck,
    };
    panel.dirty = false;
    panel.dirtyCheck = !!opts.dirtyCheck;
    panel.invoker = opts.invoker || panel.invoker;
    panel.width = opts.width || panel.width;
    panel.el.style.width = widthExpr(panel.width);
    load(panel, url);
  }

  function goBack(panel) {
    if (!panel.back) return false;
    var back = panel.back;
    panel.back = null;
    panel.dirty = false;
    panel.dirtyCheck = !!back.dirtyCheck;
    if (back.html != null) {
      panel.url = back.url;
      panel.body.innerHTML = back.html;
      panel.body.setAttribute("aria-busy", "false");
      assignTitle(panel);
      panel.closeBtn.focus();
      return true;
    }
    /* Post-redirect fallback: the cached content is gone — refetch the
       pre-action surface so the shown state is never stale. */
    load(panel, back.url);
    return true;
  }

  function open(url, opts) {
    opts = opts || {};
    if (stack.length >= 2) {
      if (!closeTop()) return; // topmost editor refused to close (dirty)
    }
    if (stack.length === 1) {
      if (window.innerWidth >= STACK_BREAKPOINT) {
        var top = buildPanel(opts);
        stack.push(top);
        shiftForStack(top);
        setInert(true);
        load(top, url);
        return;
      }
      replaceInPlace(stack[0], url, opts);
      return;
    }
    var panel = buildPanel(opts);
    stack.push(panel);
    setInert(true);
    load(panel, url);
  }

  function closeTop() {
    var panel = stack[stack.length - 1];
    if (!panel) return true;
    if (panel.dirty && !window.confirm("Discard changes?")) return false;
    stack.pop();
    unstack();
    panel.el.classList.remove("open");
    var el = panel.el;
    var backdrop = panel.backdrop;
    setTimeout(function () {
      if (el.parentNode) el.parentNode.removeChild(el);
      if (backdrop.parentNode) backdrop.parentNode.removeChild(backdrop);
    }, 200);
    if (!stack.length) {
      setInert(false);
      document.body.classList.remove("panel-open");
    }
    if (panel.adopted) {
      /* Deep link (§V2.2): closing returns the URL to the plain canvas so a
         reload does not re-open the card panel. */
      try {
        if (window.location.pathname !== "/prospects") {
          window.history.replaceState(null, "", "/prospects");
        }
      } catch (e) {
        /* history unavailable — harmless */
      }
    }
    var invoker = panel.invoker;
    if (invoker && document.contains(invoker)) invoker.focus();
    else if (stack.length) stack[stack.length - 1].closeBtn.focus();
    document.dispatchEvent(new CustomEvent("pl:panel-closed", { detail: { panel: panel } }));
    return true;
  }

  /* ----- events ----- */

  document.addEventListener("click", function (e) {
    var closeEl = e.target.closest && e.target.closest("[data-panel-close]");
    if (closeEl) {
      e.preventDefault();
      closeTop();
      return;
    }
    /* Fragment-level ← Back (spec §4): cached restore when available,
       otherwise pop the stack; last resort, open the target fresh. */
    var backEl = e.target.closest && e.target.closest("[data-panel-back]");
    if (backEl) {
      e.preventDefault();
      var bp = panelFor(backEl);
      if (!bp) return;
      if (bp.back && goBack(bp)) return;
      if (stack.length > 1) {
        closeTop();
        return;
      }
      open(backEl.getAttribute("data-panel-back"), {
        width: 560,
        invoker: bp.invoker,
      });
      return;
    }
    var link = e.target.closest && e.target.closest("[data-panel-href]");
    if (!link) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    open(link.getAttribute("data-panel-href"), {
      width: parseInt(link.getAttribute("data-panel-width") || "560", 10),
      invoker: link,
      dirtyCheck: link.getAttribute("data-panel-dirty") === "1",
    });
  });

  /* Escape: panel beats move menu, help and drawer (spec §6.2 priority) —
     capture phase + stopImmediatePropagation, same pattern as the drawer. */
  document.addEventListener(
    "keydown",
    function (e) {
      if (e.key === "Escape" && stack.length) {
        e.stopImmediatePropagation();
        closeTop();
        return;
      }
      if (e.key !== "Tab" || !stack.length) return;
      var panel = stack[stack.length - 1];
      var items = panel.el.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]),' +
          ' textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      );
      if (!items.length) {
        e.preventDefault();
        panel.closeBtn.focus();
        return;
      }
      var first = items[0];
      var last = items[items.length - 1];
      var active = document.activeElement;
      if (e.shiftKey && (active === first || !panel.el.contains(active))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (active === last || !panel.el.contains(active))) {
        e.preventDefault();
        first.focus();
      }
    },
    true
  );

  /* HTMX integration. */

  document.addEventListener("htmx:beforeRequest", function (e) {
    var panel = panelFor(e.detail.elt);
    if (!panel || !e.detail.xhr) return;
    panelRequests.set(e.detail.xhr, panel);
    if (e.detail.elt && e.detail.elt.tagName === "FORM") {
      panel.prePostUrl = panel.url;
    }
    /* Board-affecting actions swap the panel body, which detaches the form
       before htmx:afterRequest runs. Capture the intent while the element is
       still in the document. */
    if (e.detail.elt.closest && e.detail.elt.closest("[data-refresh-board]")) {
      panel.refreshBoard = true;
    }
  });

  document.addEventListener("htmx:beforeSwap", function (e) {
    var xhr = e.detail.xhr;
    if (!xhr || xhr.status < 400) return;
    var text = xhr.responseText || "";
    var isFragment = /<\s*(div|section|header|article|form|ul|dl|p|h[1-6])[\s>]/i.test(text) &&
      !/^\s*<(!doctype|html)/i.test(text);
    if (isFragment) return; // validation re-render lands in the panel (spec §4)
    e.detail.shouldSwap = false;
    toast("error", summarizeError(text, xhr.status));
  });

  document.addEventListener("htmx:afterSwap", function (e) {
    var panel = panelFor(e.detail.target);
    if (!panel) return;
    panel.body.setAttribute("aria-busy", "false");
    assignTitle(panel);
    syncPanelScroll(panel);
  });

  document.addEventListener(
    "scroll",
    function (e) {
      var body = e.target;
      if (!body || !body.classList || !body.classList.contains("panel-body")) return;
      var panel = panelFor(body);
      if (panel) panel.el.classList.toggle("panel-scrolled", body.scrollTop > 0);
    },
    true
  );

  document.addEventListener("htmx:responseError", function (e) {
    var panel = panelFor(e.detail.target);
    if (!panel) return;
    /* beforeSwap already handled the HTTP case: an HTML fragment swaps in
       (validation re-render) and anything else surfaced the server's reason
       as a toast. Wiping the panel here replaced a real business error
       ("A reason is required…", "ungrounded acknowledgement required") with a
       misleading "Couldn't load this panel. Check your connection" and threw
       away the form the user was filling in. Only genuine transport failures
       (no response) still get the retry state. */
    if (e.detail.xhr && e.detail.xhr.status >= 400) return;
    panelBodyError(panel);
  });

  document.addEventListener("htmx:sendError", function (e) {
    var panel = panelFor(e.detail.elt);
    if (!panel) return;
    panelBodyError(panel);
  });

  document.addEventListener("htmx:afterRequest", function (e) {
    var panel = e.detail.xhr ? panelRequests.get(e.detail.xhr) : null;
    if (e.detail.xhr) panelRequests.delete(e.detail.xhr);
    if (!panel) return;
    var xhr = e.detail.xhr;
    var status = xhr ? xhr.status : 0;
    if (status >= 200 && status < 400) {
      panel.dirty = false;
      /* A success redirect may point at a different resource (e.g. draft
         generate → message editor): track it and offer ← Back. */
      var finalUrl = xhr.responseURL || "";
      if (finalUrl) {
        var finalPath = finalUrl.replace(/^https?:\/\/[^/]+/, "");
        var currentPath = panel.url.replace(/^https?:\/\/[^/]+/, "");
        if (finalPath.split("?")[0] !== currentPath.split("?")[0]) {
          if (!panel.back) {
            panel.back = { source: "post", url: panel.prePostUrl || panel.url };
          }
          panel.url = finalUrl;
          /* Landing in the draft editor arms the dirty confirm (spec §4). */
          if (/^\/messages\/\d+\/edit/.test(finalPath)) panel.dirtyCheck = true;
        } else {
          panel.url = finalUrl;
        }
      }
      /* Spec §4: a successful panel post refreshes the board card in place. */
      if (panel.refreshBoard) {
        panel.refreshBoard = false;
        if (window.PlBoard && typeof window.PlBoard.refresh === "function") {
          window.PlBoard.refresh();
        }
      }
    } else {
      /* Failed action: nothing changed on the board. */
      panel.refreshBoard = false;
    }
  });

  /* Stack collapses when the viewport drops below the stacking breakpoint
     (spec §4: two levels ONLY >=1400px). Topmost closes; dirty confirm runs. */
  window.addEventListener("resize", function () {
    if (stack.length > 1 && window.innerWidth < STACK_BREAKPOINT) closeTop();
  });

  window.PlPanel = {
    open: open,
    close: closeTop,
    isOpen: function () {
      return stack.length > 0;
    },
    toast: toast,
    csrfToken: csrfToken,
  };

  /* Deep-link panels (§V2.2): a route may pre-render the open panel so the
     canvas + card content exist without JS. Adopt it into the normal stack
     (focus trap, Escape, backdrop, dirty confirm) instead of duplicating it. */
  (function adoptServerPanel() {
    var el = root.querySelector(".panel[data-panel-open]");
    if (!el) return;
    var body = el.querySelector("[data-panel-body]");
    if (!body) return;
    var panel = {
      el: el,
      body: body,
      backdrop: root.querySelector(".panel-backdrop"),
      closeBtn: el.querySelector(".panel-close"),
      invoker: null,
      width: 560,
      url: window.location.pathname + window.location.search,
      dirty: false,
      dirtyCheck: !!body.querySelector("form"),
      back: null,
      prePostUrl: null,
      adopted: true,
    };
    stack.push(panel);
    document.body.classList.add("panel-open");
    setInert(true);
    body.setAttribute("aria-busy", "false");
    assignTitle(panel);
    syncPanelScroll(panel);
    if (panel.closeBtn) panel.closeBtn.focus();
  })();
})();
