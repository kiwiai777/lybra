/* AIPOS-270 — auth chrome: inject a role pill + 退出 (logout) button.
 * Defensive: no-op if the mount point is absent. Styled by the board's single stylesheet
 * (readonly-pages.css: .auth-chrome / .auth-pill / .auth-logout-btn). The cookie is HttpOnly and sent
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

        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "auth-logout-btn";
        btn.textContent = "退出";
        btn.addEventListener("click", function () {
          var form = document.createElement("form");
          form.method = "POST";
          form.action = "/api/auth/logout";
          document.body.appendChild(form);
          form.submit();
        });

        var wrap = document.createElement("span");
        wrap.className = "auth-chrome";
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
