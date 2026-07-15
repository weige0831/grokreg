// Minimal Turnstile helper: expose hooks and reduce bot signals.
(() => {
  try {
    Object.defineProperty(navigator, "webdriver", {
      get: () => undefined,
    });
  } catch (_) {}

  const patch = () => {
    try {
      if (window.turnstile && !window.__ts_patched) {
        window.__ts_patched = true;
        const origRender = window.turnstile.render;
        if (typeof origRender === "function") {
          window.turnstile.render = function (a, b) {
            try {
              if (b && typeof b.callback === "function") {
                const cb = b.callback;
                b.callback = function (token) {
                  try {
                    window.__cf_turnstile_token = token;
                  } catch (_) {}
                  return cb.apply(this, arguments);
                };
              }
            } catch (_) {}
            return origRender.apply(this, arguments);
          };
        }
      }
    } catch (_) {}
  };

  const iv = setInterval(patch, 500);
  setTimeout(() => clearInterval(iv), 120000);
  document.addEventListener("DOMContentLoaded", patch);
})();
