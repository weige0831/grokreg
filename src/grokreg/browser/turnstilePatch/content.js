// Turnstile Patch - hide automation signals + try auto-click (from sample)
(function () {
  "use strict";

  try {
    Object.defineProperty(navigator, "webdriver", {
      get: function () {
        return false;
      },
      configurable: true,
    });
  } catch (e) {}

  try {
    if (window.chrome && window.chrome.runtime) {
      delete window.chrome.runtime.onConnect;
      delete window.chrome.runtime.onMessage;
    }
  } catch (e) {}

  try {
    var origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = function (params) {
      if (params.name === "notifications") {
        return Promise.resolve({ state: Notification.permission });
      }
      return origQuery(params);
    };
  } catch (e) {}

  try {
    Object.defineProperty(navigator, "plugins", {
      get: function () {
        return [1, 2, 3, 4, 5];
      },
      configurable: true,
    });
  } catch (e) {}

  try {
    Object.defineProperty(navigator, "languages", {
      get: function () {
        return ["en-US", "en"];
      },
      configurable: true,
    });
  } catch (e) {}

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoClickTurnstile);
  } else {
    autoClickTurnstile();
  }

  function autoClickTurnstile() {
    var checkCount = 0;
    var maxChecks = 100;
    var timer = setInterval(function () {
      checkCount++;
      if (checkCount > maxChecks) {
        clearInterval(timer);
        return;
      }
      try {
        var iframes = document.querySelectorAll(
          'iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"]'
        );
        for (var i = 0; i < iframes.length; i++) {
          var iframe = iframes[i];
          try {
            var body = iframe.contentDocument || iframe.contentWindow.document;
            var checkbox = body.querySelector(
              'input[type="checkbox"], .mark, #cf-chl-widget-nomu1_resp'
            );
            if (checkbox && !checkbox.checked) {
              checkbox.click();
            }
          } catch (e) {
            try {
              iframe.contentWindow.postMessage({ type: "turnstile-auto-click" }, "*");
            } catch (e2) {}
          }
        }
        if (window.turnstile && typeof window.turnstile.getResponse === "function") {
          var resp = window.turnstile.getResponse();
          if (resp && resp.length > 0) {
            clearInterval(timer);
          }
        }
      } catch (e) {}
    }, 500);
  }
})();
