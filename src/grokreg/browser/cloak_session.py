from __future__ import annotations

import json
import random
import time
from typing import Any

from grokreg.browser.cloak_register import random_fingerprint_args, _LOCALES, _TIMEZONES, _VIEWPORTS
from grokreg.mint.auth_code import normalize_sso
from grokreg.util.log import LogFn, default_log


class CloakSessionError(RuntimeError):
    pass


class CloakSession:
    """Post-register session: inject SSO into CloakBrowser → web warm-up → hand off for Build mint."""

    def __init__(self, cfg: dict[str, Any], log: LogFn | None = None) -> None:
        self.cfg = cfg
        self.log = log or default_log
        self._browser = None
        self._page = None
        self._fingerprint: dict[str, Any] = {}

    def start(self) -> None:
        from cloakbrowser import launch

        headless = bool(self.cfg.get("headless", False))
        humanize = bool(self.cfg.get("cloak_humanize", True))
        stealth_args = bool(self.cfg.get("cloak_stealth_args", True))
        tz = str(self.cfg.get("cloak_timezone") or random.choice(_TIMEZONES))
        locale = str(self.cfg.get("cloak_locale") or random.choice(_LOCALES))
        fp_args = random_fingerprint_args()
        extra = list(self.cfg.get("cloak_extra_args") or [])
        args = fp_args + extra
        viewport = random.choice(_VIEWPORTS)

        proxy = str(self.cfg.get("browser_proxy") or "").strip()
        if not proxy and bool(self.cfg.get("cloak_use_proxy", False)):
            proxy = str(self.cfg.get("proxy") or "").strip()

        self._fingerprint = {
            "args": args,
            "timezone": tz,
            "locale": locale,
            "viewport": viewport,
            "proxy": bool(proxy),
            "humanize": humanize,
        }
        self.log(
            f"[cloak-sess] launch humanize={humanize} tz={tz} locale={locale} "
            f"fp={args} proxy={bool(proxy)}"
        )
        kw: dict[str, Any] = {
            "headless": headless,
            "stealth_args": stealth_args,
            "args": args,
            "timezone": tz,
            "locale": locale,
            "humanize": humanize,
            "human_preset": str(self.cfg.get("cloak_human_preset") or "default"),
            "geoip": bool(self.cfg.get("cloak_geoip", False)),
        }
        if proxy:
            kw["proxy"] = proxy
        lic = str(self.cfg.get("cloak_license_key") or "").strip()
        if lic:
            kw["license_key"] = lic

        self._browser = launch(**kw)
        self._page = self._browser.new_page(viewport=viewport, locale=locale)

    def stop(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        self._browser = None
        self._page = None

    def _eval(self, script: str) -> Any:
        if self._page is None:
            return None
        try:
            return self._page.evaluate(script)
        except Exception as exc:
            self.log(f"[cloak-sess] js: {exc}")
            return None

    def login_with_sso(self, sso: str, *, cf_clearance: str = "") -> None:
        """Inject sso cookies then open grok.com (simulates logged-in human browser)."""
        if self._page is None:
            self.start()
        sso = normalize_sso(sso)
        if not sso:
            raise CloakSessionError("empty sso")

        cookies: list[dict[str, Any]] = []
        for domain in (".x.ai", "accounts.x.ai", "auth.x.ai", ".grok.com", "grok.com"):
            cookies.append(
                {
                    "name": "sso",
                    "value": sso,
                    "domain": domain,
                    "path": "/",
                    "secure": True,
                    "httpOnly": False,
                    "sameSite": "None",
                }
            )
            cookies.append(
                {
                    "name": "sso-rw",
                    "value": sso,
                    "domain": domain,
                    "path": "/",
                    "secure": True,
                    "httpOnly": False,
                    "sameSite": "None",
                }
            )
        if cf_clearance:
            for domain in (".grok.com", "grok.com", ".x.ai"):
                cookies.append(
                    {
                        "name": "cf_clearance",
                        "value": cf_clearance,
                        "domain": domain,
                        "path": "/",
                        "secure": True,
                        "httpOnly": True,
                        "sameSite": "None",
                    }
                )

        # Playwright requires a context URL before add_cookies for some domains — warm blank first
        try:
            self._page.goto("https://accounts.x.ai/", wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:
            self.log(f"[cloak-sess] warm accounts.x.ai: {exc}")
        try:
            self._page.context.add_cookies(cookies)
            self.log(f"[cloak-sess] injected sso cookies n={len(cookies)}")
        except Exception as exc:
            raise CloakSessionError(f"add_cookies failed: {exc}") from exc

        # re-open with cookies
        try:
            self._page.goto("https://grok.com/", wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            self.log(f"[cloak-sess] open grok.com: {exc}")
        settle = float(self.cfg.get("cloak_login_settle_sec") or 3)
        time.sleep(settle)
        # wait out CF interstitial if present ("Just a moment...")
        self._wait_cf_interstitial(timeout=float(self.cfg.get("cloak_cf_wait_sec") or 45))

        # verify session roughly
        url = str(self._page.url or "")
        has_sso = False
        try:
            for c in self._page.context.cookies() or []:
                if c.get("name") == "sso" and c.get("value"):
                    has_sso = True
                    break
        except Exception:
            pass
        self.log(f"[cloak-sess] after login url={url[:80]} sso_cookie={has_sso}")

    def _wait_cf_interstitial(self, timeout: float = 45) -> bool:
        """Wait until CF 'Just a moment' challenge leaves (manual-like)."""
        if self._page is None:
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                title = (self._page.title() or "").lower()
                body = ""
                try:
                    body = str(
                        self._page.evaluate(
                            "() => (document.body && document.body.innerText || '').slice(0, 200)"
                        )
                        or ""
                    ).lower()
                except Exception:
                    pass
                if "just a moment" not in title and "just a moment" not in body and "checking your browser" not in body:
                    return True
                self.log("[cloak-sess] waiting CF interstitial…")
            except Exception:
                return False
            time.sleep(1.5)
        self.log("[cloak-sess] CF interstitial wait timeout")
        return False

    def extract_cf_and_ua(self) -> tuple[str, str]:
        cf = ""
        ua = ""
        try:
            for c in self._page.context.cookies() or []:
                if c.get("name") == "cf_clearance" and c.get("value"):
                    cf = str(c["value"])
                    break
            ua = str(self._eval("() => navigator.userAgent") or "")
        except Exception as exc:
            self.log(f"[cloak-sess] extract: {exc}")
        return cf, ua

    def web_default_chat(self) -> dict[str, Any]:
        """Web default model probe inside Cloak (closer to manual browser)."""
        model = str(self.cfg.get("web_default_model") or "grok-3")
        prompt = str(self.cfg.get("web_probe_prompt") or "1+1=? Reply with one number only.")
        if self._page is None:
            return {"ok": False, "code": "web_no_page", "status": 0, "model": model}

        # light human settle
        try:
            self._page.mouse.move(random.randint(100, 400), random.randint(100, 300))
            time.sleep(random.uniform(0.3, 0.8))
        except Exception:
            pass

        result = self._eval(
            f"""async () => {{
const model = {json.dumps(model)};
const prompt = {json.dumps(prompt)};
const tries = [
  {{
    url: 'https://grok.com/rest/app-chat/conversations/new',
    body: {{
      temporary: true, modelName: model, message: prompt,
      fileAttachments: [], imageAttachments: [], disableSearch: true,
      enableImageGeneration: false, forceConcise: true, toolOverrides: {{}},
      enableSideBySide: false, isPreset: false, sendFinalMetadata: true
    }}
  }},
  {{ url: 'https://grok.com/rest/app-chat/conversations/new', body: {{ temporary: true, model: model, message: prompt }} }}
];
for (const t of tries) {{
  try {{
    const r = await fetch(t.url, {{
      method: 'POST', credentials: 'include',
      headers: {{
        'content-type': 'application/json',
        'origin': 'https://grok.com',
        'referer': 'https://grok.com/'
      }},
      body: JSON.stringify(t.body)
    }});
    const text = (await r.text()).slice(0, 1000);
    const low = text.toLowerCase();
    const ok = r.status >= 200 && r.status < 300 && text
      && !low.includes('permission-denied')
      && !low.includes('anti-bot')
      && (text.length > 20 || /\\b2\\b/.test(text) || low.includes('message') || low.includes('token') || low.includes('result'));
    if (ok) return {{ ok: true, status: r.status, text: text.slice(0, 400), model }};
    // keep last failure
    window.__lastWeb = {{ ok: false, status: r.status, text: text.slice(0, 400), model }};
  }} catch (e) {{
    window.__lastWeb = {{ ok: false, status: 0, text: String(e).slice(0, 200), model }};
  }}
}}
return window.__lastWeb || {{ ok: false, status: 0, text: 'no-try', model }};
}}"""
        )
        if not isinstance(result, dict):
            result = {"ok": False, "status": 0, "text": str(result)[:200], "model": model}
        ok = bool(result.get("ok"))
        self.log(
            f"[cloak-sess][web] ok={ok} status={result.get('status')} "
            f"model={result.get('model')} text={(str(result.get('text') or ''))[:120]!r}"
        )
        return {
            "ok": ok,
            "status": int(result.get("status") or 0),
            "code": "web_ok" if ok else "web_fail",
            "model": result.get("model") or model,
            "text": str(result.get("text") or "")[:400],
            "endpoint": "grok.com/rest/app-chat",
            "engine": "cloak",
        }

    def warm_and_web(self, sso: str, *, cf_clearance: str = "") -> dict[str, Any]:
        """Full post-register Cloak path: login → settle → web probe → return artifacts."""
        try:
            self.start()
            self.login_with_sso(sso, cf_clearance=cf_clearance)
            # extra human dwell before API (manual-like)
            dwell = float(self.cfg.get("cloak_web_dwell_sec") or 2)
            if dwell > 0:
                time.sleep(dwell)
            web = self.web_default_chat()
            cf2, ua = self.extract_cf_and_ua()
            return {
                "web": web,
                "cf_clearance": cf2 or cf_clearance,
                "user_agent": ua,
                "fingerprint": self._fingerprint,
            }
        finally:
            # keep browser only if configured (default close to free resources)
            if not bool(self.cfg.get("cloak_keep_open", False)):
                self.stop()


def cloak_post_register(
    sso: str,
    cfg: dict[str, Any],
    *,
    cf_clearance: str = "",
    log: LogFn | None = None,
) -> dict[str, Any]:
    """Convenience: Drission SSO → Cloak login → web test artifacts."""
    sess = CloakSession(cfg, log=log)
    return sess.warm_and_web(sso, cf_clearance=cf_clearance)
