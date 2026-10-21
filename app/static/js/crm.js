/* ============================================================
   DataBreakers CRM - client-side behaviour
   Toasts, confirmation dialogs, Chart.js bootstrap, loading states.
   Nothing here computes business metrics - every number comes
   from the server.
   ============================================================ */
(function () {
  "use strict";

  /* ---------------------------------------------------------
     Toast notifications
     --------------------------------------------------------- */
  window.dbToast = function (message, type) {
    type = type || "info";
    var colors = {
      success: "#16a34a", danger: "#dc3545", warning: "#d97706",
      info: "#6d3ff0"
    };
    var holder = document.querySelector(".db-toast-holder");
    if (!holder) {
      holder = document.createElement("div");
      holder.className = "db-toast-holder";
      document.body.appendChild(holder);
    }
    var el = document.createElement("div");
    el.className = "db-alert db-alert-" + type;
    el.setAttribute("role", "status");
    el.style.cssText =
      "background:#fff;border:1px solid " + (colors[type] || colors.info) +
      ";border-left:4px solid " + (colors[type] || colors.info) +
      ";border-radius:10px;padding:.7rem .9rem;margin-bottom:.5rem;" +
      "box-shadow:0 10px 30px -12px rgba(16,24,40,.35);font-size:.87rem;" +
      "animation:db-fade-in .25s ease;";
    el.textContent = message;
    holder.appendChild(el);
    setTimeout(function () {
      el.style.transition = "opacity .35s ease, transform .35s ease";
      el.style.opacity = "0";
      el.style.transform = "translateX(20px)";
      setTimeout(function () { el.remove(); }, 380);
    }, 4200);
  };

  /* Render server-side flashed messages as toasts (single place). */
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-flash]").forEach(function (el) {
      window.dbToast(el.getAttribute("data-flash"),
                     el.getAttribute("data-flash-type") || "info");
      el.remove();
    });
  });

  /* ---------------------------------------------------------
     Confirmation dialogs (no native alert())
     --------------------------------------------------------- */
  window.dbConfirm = function (message, onConfirm, options) {
    options = options || {};
    var backdrop = document.createElement("div");
    backdrop.style.cssText =
      "position:fixed;inset:0;background:rgba(7,11,28,.6);z-index:3000;" +
      "display:flex;align-items:center;justify-content:center;padding:1rem;" +
      "backdrop-filter:blur(2px);";
    var card = document.createElement("div");
    card.style.cssText =
      "background:#fff;border-radius:16px;max-width:420px;width:100%;" +
      "padding:1.4rem;box-shadow:0 30px 70px -25px rgba(0,0,0,.7);";
    card.innerHTML =
      '<h5 style="margin:0 0 .5rem;font-size:1.05rem;">' +
      (options.title || "Please confirm") + "</h5>" +
      '<p style="margin:0 0 1.1rem;color:#6b7488;font-size:.9rem;">' +
      message + "</p>";
    var row = document.createElement("div");
    row.style.cssText = "display:flex;gap:.5rem;justify-content:flex-end;";
    var cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "btn btn-light";
    cancel.textContent = "Cancel";
    var ok = document.createElement("button");
    ok.type = "button";
    ok.className = "btn " + (options.danger === false ? "btn-primary" : "btn-danger");
    ok.textContent = options.confirmLabel || "Delete";
    row.appendChild(cancel);
    row.appendChild(ok);
    card.appendChild(row);
    backdrop.appendChild(card);
    document.body.appendChild(backdrop);
    cancel.focus();

    function close() { backdrop.remove(); document.removeEventListener("keydown", esc); }
    function esc(e) { if (e.key === "Escape") close(); }
    document.addEventListener("keydown", esc);
    cancel.addEventListener("click", close);
    backdrop.addEventListener("click", function (e) {
      if (e.target === backdrop) close();
    });
    ok.addEventListener("click", function () { close(); onConfirm(); });
  };

  /* Wire up any element carrying data-confirm. */
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.getAttribute("data-confirm-handled") === "1") return;
    var message = form.getAttribute("data-confirm");
    if (!message) return;
    e.preventDefault();
    e.stopPropagation();
    form.setAttribute("data-confirm-handled", "1");
    window.dbConfirm(message, function () {
      form.removeAttribute("data-confirm-handled");
      form.requestSubmit();
    }, { danger: form.getAttribute("data-confirm-danger") !== "false" });
  }, true);

  /* Anchor-based delete buttons that post a hidden form. */
  document.addEventListener("click", function (e) {
    var trigger = e.target.closest("[data-post]");
    if (!trigger) return;
    e.preventDefault();
    var url = trigger.getAttribute("data-post");
    var message = trigger.getAttribute("data-confirm") ||
                  "Are you sure you want to do this?";
    window.dbConfirm(message, function () {
      var form = document.createElement("form");
      form.method = "POST";
      form.action = url;
      var token = document.querySelector('input[name="csrf_token"]');
      if (token) {
        var hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = "csrf_token";
        hidden.value = token.value;
        form.appendChild(hidden);
      }
      document.body.appendChild(form);
      form.submit();
    }, { confirmLabel: trigger.getAttribute("data-confirm-label") || "Confirm",
         danger: trigger.getAttribute("data-confirm-danger") !== "false" });
  });

  /* ---------------------------------------------------------
     Quick-ask Zia (topbar modal) -> POST /zia/ask (JSON)
     --------------------------------------------------------- */
  document.addEventListener("click", function (e) {
    var trigger = e.target.closest("[data-ask-zia]");
    if (!trigger) return;
    var modalEl = document.getElementById("ziaQuickAsk");
    if (!modalEl || typeof bootstrap === "undefined") return;
    var answerBox = document.getElementById("ziaQuickAskAnswer");
    if (answerBox) answerBox.hidden = true;
    var modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.show();
    var input = modalEl.querySelector('input[name="question"]');
    if (input) window.setTimeout(function () { input.focus(); }, 300);
  });

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.getAttribute("data-zia-ask") === null &&
        form.id !== "ziaQuickAskForm") return;
    e.preventDefault();
    var question = (form.querySelector('input[name="question"]') || {}).value || "";
    question = question.trim();
    var box = document.getElementById("ziaQuickAskAnswer");
    if (!box) return;
    var out = box.querySelector(".db-zia-answer");
    box.hidden = false;
    if (question.length < 2) {
      if (out) out.textContent = "Please ask a question of at least 2 characters.";
      return;
    }
    if (out) out.textContent = "Zia is thinking\u2026";
    var meta = document.querySelector('meta[name="csrf-token"]');
    fetch(form.getAttribute("action") || "/zia/ask", {
      method: "POST",
      headers: {"Content-Type": "application/json",
                "X-CSRFToken": meta ? meta.getAttribute("content") : ""},
      body: JSON.stringify({question: question})
    }).then(function (resp) {
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      return resp.json();
    }).then(function (data) {
      if (out) out.textContent = data.answer || data.error || "No answer available.";
    }).catch(function (err) {
      if (out) out.textContent = "Zia could not be reached (" + err.message +
                                 "). Open the Zia AI page to ask again.";
    });
  }, true);

  /* ---------------------------------------------------------
     Chart.js helpers - data always supplied by the server
     --------------------------------------------------------- */
  var DB_PALETTE = [
    "#8b5cf6", "#22d3ee", "#f59e0b", "#10b981", "#ef4444",
    "#6366f1", "#ec4899", "#14b8a6", "#84cc16", "#f97316"
  ];

  function cssVar(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return v ? v.trim() : fallback;
  }

  window.dbChart = function (canvasId, config) {
    var canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === "undefined") return null;

    Chart.defaults.font.family = "'Inter','Segoe UI',system-ui,sans-serif";
    Chart.defaults.color = cssVar("--db-muted", "#6b7488");

    config.options = config.options || {};
    config.options.responsive = true;
    config.options.maintainAspectRatio = false;
    config.options.plugins = config.options.plugins || {};
    if (!config.options.plugins.legend) {
      config.options.plugins.legend = { display: false };
    }

    // Sensible default palette for single-series charts.
    var ds = (config.data && config.data.datasets) || [];
    ds.forEach(function (d, i) {
      if (!d.backgroundColor) {
        d.backgroundColor = DB_PALETTE[i % DB_PALETTE.length];
      }
      if (config.type === "line" && !d.borderColor) {
        d.borderColor = DB_PALETTE[i % DB_PALETTE.length];
      }
    });

    return new Chart(canvas.getContext("2d"), config);
  };

  window.DB_PALETTE = DB_PALETTE;

  /* Gradient fills for area charts.
     Accepts either a 2D context or a <canvas> element (templates pass the
     element), and degrades to a plain color if neither can produce a
     gradient. */
  window.dbGradient = function (ctx, color) {
    try {
      if (ctx && typeof ctx.createLinearGradient !== "function" &&
          typeof ctx.getContext === "function") {
        ctx = ctx.getContext("2d");
      }
      if (!ctx || typeof ctx.createLinearGradient !== "function") return color;
      var g = ctx.createLinearGradient(0, 0, 0, 300);
      g.addColorStop(0, color + "55");
      g.addColorStop(1, color + "05");
      return g;
    } catch (e) {
      return color;
    }
  };

  /* ---------------------------------------------------------
     Mobile sidebar toggle
     --------------------------------------------------------- */
  document.addEventListener("DOMContentLoaded", function () {
    var toggler = document.querySelector("[data-sidebar-toggle]");
    var sidebar = document.querySelector(".db-sidebar");
    if (toggler && sidebar) {
      toggler.addEventListener("click", function () {
        sidebar.classList.toggle("show");
      });
      document.addEventListener("click", function (e) {
        if (sidebar.classList.contains("show") &&
            !sidebar.contains(e.target) &&
            !toggler.contains(e.target)) {
          sidebar.classList.remove("show");
        }
      });
    }
  });

  /* ---------------------------------------------------------
     Loading indicator for forms with data-loading
     --------------------------------------------------------- */
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.getAttribute("data-loading") !== "1") return;
    var btn = form.querySelector('button[type="submit"]');
    if (!btn) return;
    setTimeout(function () {
      btn.disabled = true;
      btn.dataset.originalText = btn.innerHTML;
      btn.innerHTML = '<span class="db-spinner"></span> Working…';
    }, 0);
  }, true);

  /* ---------------------------------------------------------
     Date helpers for the follow-up reschedule popovers
     --------------------------------------------------------- */
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-reschedule-toggle]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var row = btn.closest("tr");
        var input = row && row.querySelector('input[name="due_at"]');
        if (input) {
          input.style.display = input.style.display === "none" ? "inline-block" : "none";
          if (input.style.display !== "none") input.focus();
        }
      });
    });
  });
})();
