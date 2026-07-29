/* AIPOS-270 — auth chrome: inject a role pill + 退出 (logout) button.
 * Defensive: no-op if the mount point is absent. Self-contained (inline styles)
 * so it does not require edits to shared CSS. The cookie is HttpOnly and sent
 * automatically; logout posts a form so the 303 redirect is honored by browsers.
 */
(function () {
  "use strict";

  function mount() {
    var el = document.getElementById("auth-chrome-mount");
    if (!el || el.dataset.authBound === "1") return;
    el.dataset.authBound = "1";

    fetch("/api/auth/status", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (info) {
        if (!info || !info.authenticated) return;
        var pill = document.createElement("span");
        pill.className = "auth-pill";
        pill.textContent = info.is_owner ? "Owner" : (info.role || "user");
        pill.style.cssText =
          "display:inline-block;padding:4px 10px;font-size:12px;font-weight:600;" +
          "color:#fff;background:rgba(255,255,255,0.16);border-radius:999px;" +
          "letter-spacing:.02em;";

        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "auth-logout-btn";
        btn.textContent = "退出";
        btn.style.cssText =
          "margin-left:8px;padding:5px 12px;font-size:12px;font-weight:600;" +
          "font-family:inherit;color:#fff;background:rgba(255,255,255,0.12);" +
          "border:1px solid rgba(255,255,255,0.4);border-radius:7px;cursor:pointer;";
        btn.addEventListener("click", function () {
          var form = document.createElement("form");
          form.method = "POST";
          form.action = "/api/auth/logout";
          document.body.appendChild(form);
          form.submit();
        });

        var wrap = document.createElement("span");
        wrap.style.cssText = "display:inline-flex;align-items:center;";
        wrap.appendChild(pill);
        wrap.appendChild(btn);
        el.appendChild(wrap);
      })
      .catch(function () { /* status probe failed: render nothing */ });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
