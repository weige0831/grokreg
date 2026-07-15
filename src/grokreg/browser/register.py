from __future__ import annotations

import json
import random
import secrets
import string
import time
from pathlib import Path
from typing import Any

from grokreg.mail.tempmail import TempMailClient, TempMailError
from grokreg.util.log import LogFn, default_log

# sample: https://github.com/Git-creat7/grokRegister-cpa
SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"


def _rand_name() -> tuple[str, str]:
    given = random.choice(
        [
            "Neo", "Ethan", "Liam", "Noah", "Lucas", "Mason", "Ryan", "Leo",
            "Owen", "Aiden", "Elio", "Aron", "Ivan", "Nolan", "Evan", "Kai",
            "Caleb", "Adam", "Ezra", "Miles", "Logan", "Carter", "Hunter", "Jason",
            "Alex", "Colin", "Blake", "Gavin", "Henry", "Julian", "Kevin", "Louis",
        ]
    )
    family = random.choice(
        [
            "Lin", "Wang", "Zhao", "Liu", "Chen", "Zhang", "Xu", "Sun",
            "Guo", "He", "Yang", "Wu", "Zhou", "Tang", "Qin", "Shi",
            "Fang", "Peng", "Cao", "Deng", "Fan", "Fu", "Gao", "Han",
            "Hu", "Jiang", "Kong", "Lu", "Ma", "Nie", "Pan", "Qiao",
        ]
    )
    return given, family


def _rand_password() -> str:
    return "N" + secrets.token_hex(4) + "!a7#" + secrets.token_urlsafe(6)


class BrowserRegisterError(RuntimeError):
    pass


class BrowserRegistrar:
    """accounts.x.ai signup rewritten from Git-creat7/grokRegister-cpa."""

    def __init__(self, cfg: dict[str, Any], log: LogFn | None = None) -> None:
        self.cfg = cfg
        self.log = log or default_log
        self._browser = None
        self._page = None
        self._diag_dir = Path(str(cfg.get("diag_dir") or "diag"))
        self._phase_started = time.time()
        self._phase_name = "init"
        self._last_diag_at = 0.0
        self._net_hooked = False
        self._mail = TempMailClient(
            base_url=str(cfg.get("tempmail_base_url") or "https://mail.minecraft-cn.net"),
            domains=list(cfg.get("tempmail_domains") or []),
            proxy=str(cfg.get("proxy") or ""),
            poll_interval=float(cfg.get("tempmail_poll_interval") or cfg.get("mail_poll_interval") or 2),
            timeout=float(cfg.get("tempmail_timeout") or cfg.get("mail_timeout") or 150),
            list_limit=int(cfg.get("tempmail_list_limit") or 30),
            log=self.log,
        )

    def _begin_phase(self, name: str) -> None:
        self._phase_name = name
        self._phase_started = time.time()
        self._last_diag_at = 0.0
        self.log(f"[reg] phase={name}")

    def _sleep(self, sec: float) -> None:
        time.sleep(max(0.0, float(sec)))

    def _js(self, script: str, *args: Any) -> Any:
        page = self.page
        try:
            if args:
                return page.run_js(script, *args)
            return page.run_js(script)
        except TypeError:
            if not args:
                return page.run_js(script)
            payload = json.dumps(list(args), ensure_ascii=False)
            body = script.strip()
            wrapped = f"(function(){{ var arguments = {payload}; {body} }})()"
            return page.run_js(wrapped)
        except Exception as exc:
            self.log(f"[reg] js error: {exc}")
            return None

    @property
    def page(self):
        if self._page is None:
            raise BrowserRegisterError("browser not started")
        return self._page

    def _create_browser_options(self):
        """Strict copy of sample create_browser_options (Git-creat7/grokRegister-cpa).

        Sample only does:
          ChromiumOptions(); auto_port(); set_timeouts(base=1); add_extension if exists
        No proxy / UA / browser_path / headless / stealth / extra flags on browser.
        Proxy is for HTTP mail/mint only (get_proxies), not Chromium.
        """
        from DrissionPage import ChromiumOptions

        options = ChromiumOptions()
        options.auto_port()
        options.set_timeouts(base=1)

        # optional overrides — off by default to match sample environment
        if bool(self.cfg.get("headless", False)):
            try:
                options.headless(True)
            except Exception:
                options.set_argument("--headless=new")

        browser_path = str(self.cfg.get("browser_path") or "").strip()
        if browser_path:
            options.set_browser_path(browser_path)

        # sample does NOT put proxy on browser. Only if browser_proxy explicitly set.
        browser_proxy = str(
            self.cfg.get("browser_proxy")
            if self.cfg.get("browser_proxy") is not None
            else (self.cfg.get("proxy") if self.cfg.get("browser_use_proxy") else "")
        ).strip()
        if browser_proxy:
            try:
                from urllib.parse import urlparse

                u = urlparse(browser_proxy if "://" in browser_proxy else f"http://{browser_proxy}")
                host = u.hostname or ""
                if host:
                    port = u.port or (443 if (u.scheme or "http") == "https" else 80)
                    scheme = u.scheme or "http"
                    options.set_argument(f"--proxy-server={scheme}://{host}:{port}")
                else:
                    options.set_proxy(browser_proxy)
            except Exception:
                options.set_proxy(browser_proxy)

        # sample config has user_agent key but create_browser_options never applies it
        if bool(self.cfg.get("browser_set_user_agent", False)):
            ua = str(self.cfg.get("user_agent") or "").strip()
            if ua:
                options.set_user_agent(ua)

        # sample: only if turnstilePatch dir exists next to script
        use_ext = self.cfg.get("turnstile_extension")
        if use_ext is None:
            use_ext = True
        if use_ext:
            ext = Path(__file__).resolve().parent / "turnstilePatch"
            if ext.is_dir() and (ext / "manifest.json").is_file():
                try:
                    options.add_extension(str(ext))
                    self.log("[browser] loaded turnstilePatch extension")
                except Exception as exc:
                    self.log(f"[browser] extension load skip: {exc}")
            else:
                self.log("[browser] turnstilePatch missing (sample also skips if absent)")

        return options, bool(browser_proxy)

    def start(self) -> None:
        from DrissionPage import Chromium

        cdp = str(self.cfg.get("fingerprint_cdp") or self.cfg.get("cdp_url") or "").strip()
        if cdp:
            self._start_fingerprint_cdp(cdp)
            return

        # exact sample start_browser loop
        last_exc: Exception | None = None
        for attempt in range(1, 5):
            try:
                co, has_browser_proxy = self._create_browser_options()
                self.log(
                    f"[browser] start sample-mode attempt={attempt} "
                    f"browser_proxy={has_browser_proxy} headless={bool(self.cfg.get('headless', False))}"
                )
                self._browser = Chromium(co)
                try:
                    tabs = self._browser.get_tabs()
                    self._page = tabs[-1] if tabs else self._browser.new_tab()
                except Exception:
                    self._page = self._browser.latest_tab
                # sample: NO stealth JS, NO CDP network inject on start
                if bool(self.cfg.get("stealth_inject", False)):
                    self._inject_stealth()
                # soft network hook only if explicitly enabled (can affect bot score)
                if bool(self.cfg.get("network_diag_hook", False)):
                    self._enable_network_diag()
                if attempt > 1:
                    self.log(f"[browser] started on attempt {attempt}")
                return
            except Exception as exc:
                last_exc = exc
                self.log(f"[browser] start fail {attempt}/4: {exc}")
                try:
                    if self._browser is not None:
                        try:
                            self._browser.quit(del_data=True)
                        except TypeError:
                            self._browser.quit()
                except Exception:
                    pass
                self._browser = None
                self._page = None
                self._sleep(min(1.5 * attempt, 4))
        raise BrowserRegisterError(f"browser start failed: {last_exc}")

    def _start_fingerprint_cdp(self, cdp: str) -> None:
        from DrissionPage import Chromium

        addr = cdp.replace("http://", "").replace("https://", "").strip().rstrip("/")
        self.log(f"[browser] attach fingerprint CDP {addr}")
        try:
            self._browser = Chromium(addr)
        except TypeError:
            from DrissionPage import ChromiumOptions

            co = ChromiumOptions()
            co.set_address(addr)
            self._browser = Chromium(co)
        try:
            tabs = self._browser.get_tabs()
            self._page = tabs[-1] if tabs else self._browser.latest_tab
        except Exception:
            self._page = self._browser.latest_tab
        self._inject_stealth()
        self._enable_network_diag()

    def _inject_stealth(self) -> None:
        page = self._page
        if page is None:
            return
        stealth_js = r"""
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
try { window.chrome = window.chrome || { runtime: {} }; } catch(e) {}
try { Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] }); } catch(e) {}
try { Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] }); } catch(e) {}
"""
        for call in (
            lambda: page.run_cdp("Page.addScriptToEvaluateOnNewDocument", source=stealth_js),
            lambda: page.run_cdp("Page.addScriptToEvaluateOnNewDocument", {"source": stealth_js}),
            lambda: page.run_js(stealth_js),
        ):
            try:
                call()
                self.log("[browser] stealth injected")
                break
            except Exception:
                continue

    def _enable_network_diag(self) -> None:
        """Enable CDP Network + page-level fetch/XHR log (F12 Network equivalent)."""
        page = self._page
        if page is None:
            return
        try:
            page.run_cdp("Network.enable")
        except Exception:
            try:
                page.run_cdp("Network.enable", {})
            except Exception:
                pass
        # persist network hooks across navigations
        hook = r"""
(function(){
  if (window.__grokNetHooked) return;
  window.__grokNetHooked = true;
  window.__netlog = window.__netlog || [];
  function push(item){
    try {
      window.__netlog.push(item);
      if (window.__netlog.length > 200) window.__netlog = window.__netlog.slice(-200);
    } catch(e) {}
  }
  try {
    const ofetch = window.fetch;
    if (ofetch) {
      window.fetch = async function(){
        const args = arguments;
        const url = String((args[0] && args[0].url) || args[0] || '');
        try {
          const res = await ofetch.apply(this, args);
          push({t: Date.now(), type:'fetch', url: url.slice(0,300), status: res.status, ok: res.ok});
          return res;
        } catch(err) {
          push({t: Date.now(), type:'fetch', url: url.slice(0,300), status: 0, err: String(err).slice(0,120)});
          throw err;
        }
      };
    }
  } catch(e) {}
  try {
    const open = XMLHttpRequest.prototype.open;
    const send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function(method, url){
      this.__grokUrl = url;
      this.__grokMethod = method;
      return open.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function(){
      this.addEventListener('loadend', function(){
        push({t: Date.now(), type:'xhr', url: String(this.__grokUrl||'').slice(0,300), status: this.status, method: this.__grokMethod||''});
      });
      return send.apply(this, arguments);
    };
  } catch(e) {}
})();
"""
        for call in (
            lambda: page.run_cdp("Page.addScriptToEvaluateOnNewDocument", source=hook),
            lambda: page.run_cdp("Page.addScriptToEvaluateOnNewDocument", {"source": hook}),
            lambda: page.run_js(hook),
        ):
            try:
                call()
                self._net_hooked = True
                break
            except Exception:
                continue
        try:
            page.run_js(hook)
            self._net_hooked = True
        except Exception:
            pass

    def stop(self) -> None:
        try:
            if self._browser is not None:
                try:
                    self._browser.quit(del_data=True)
                except TypeError:
                    self._browser.quit()
        except Exception:
            pass
        self._browser = None
        self._page = None
        self._net_hooked = False

    def restart(self) -> None:
        self.stop()
        self._sleep(1)
        self.start()

    def refresh_active_page(self) -> None:
        if self._browser is None:
            self.start()
            return
        try:
            tabs = self._browser.get_tabs()
            if tabs:
                self._page = tabs[-1]
            else:
                self._page = self._browser.new_tab()
        except Exception:
            try:
                self._page = self._browser.latest_tab
            except Exception:
                self.restart()

    def _maybe_diag(self, force: bool = False, tag: str = "") -> None:
        stall = float(self.cfg.get("diag_stall_sec") or 30)
        now = time.time()
        elapsed = now - self._phase_started
        if not force and elapsed < stall:
            return
        if not force and self._last_diag_at and (now - self._last_diag_at) < stall:
            return
        self._last_diag_at = now
        self.dump_diagnostics(tag or self._phase_name or "stall", elapsed=elapsed)

    def dump_diagnostics(self, tag: str, *, elapsed: float | None = None) -> Path | None:
        """Screenshot + F12-like Network dump; classify 403/CF block from evidence."""
        page = self._page
        if page is None:
            self.log(f"[diag] no page for {tag}")
            return None
        try:
            self._diag_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return None
        ts = time.strftime("%Y%m%d_%H%M%S")
        base = self._diag_dir / f"{ts}_{tag}"
        shot = Path(str(base) + ".png")
        try:
            page.get_screenshot(path=str(shot), full_page=True)
        except Exception:
            try:
                page.get_screenshot(path=str(shot))
            except Exception as exc:
                self.log(f"[diag] screenshot failed: {exc}")
                shot = Path("")

        state: dict[str, Any] = {
            "tag": tag,
            "elapsed": elapsed,
            "url": str(getattr(page, "url", "") or ""),
            "title": "",
            "readyState": "",
            "cf_token_len": 0,
            "turnstile_present": False,
            "visible_text": "",
            "network": [],
            "network_403": [],
            "cookies": [],
            "verdict": "unknown",
        }

        try:
            page.run_js(
                r"""
(function(){
  if (window.__grokNetHooked) return;
  window.__netlog = window.__netlog || [];
})();
"""
            )
        except Exception:
            pass

        try:
            info = self._js(
                r"""
const cf = document.querySelector('input[name="cf-turnstile-response"]');
const tok = String((cf && cf.value) || '').trim();
const present = !!cf || !!document.querySelector('iframe[src*="turnstile"], iframe[src*="challenges.cloudflare.com"], div.cf-turnstile, [data-sitekey]');
const net = (window.__netlog || []).slice(-60);
const perf = [];
try {
  const list = performance.getEntriesByType('resource') || [];
  for (const e of list.slice(-50)) {
    perf.push({
      name: String(e.name || '').slice(0, 220),
      type: e.initiatorType || '',
      status: e.responseStatus || 0,
      dur: Math.round(e.duration || 0),
      transfer: e.transferSize || 0
    });
  }
} catch(e) {}
return {
  title: document.title || '',
  readyState: document.readyState || '',
  cf_token_len: tok.length,
  turnstile_present: present,
  text: (document.body && document.body.innerText || '').slice(0, 1000),
  net: net,
  perf: perf
};
"""
            )
            if isinstance(info, dict):
                state["title"] = info.get("title") or ""
                state["readyState"] = info.get("readyState") or ""
                state["cf_token_len"] = int(info.get("cf_token_len") or 0)
                state["turnstile_present"] = bool(info.get("turnstile_present"))
                state["visible_text"] = str(info.get("text") or "")[:1000]
                net = list(info.get("net") or [])
                perf = list(info.get("perf") or [])
                # merge, prefer xhr/fetch statuses
                merged: list[dict[str, Any]] = []
                for e in net:
                    if isinstance(e, dict):
                        merged.append(
                            {
                                "name": str(e.get("url") or "")[:220],
                                "type": str(e.get("type") or "xhr"),
                                "status": int(e.get("status") or 0),
                                "dur": 0,
                                "transfer": 0,
                                "src": "page",
                            }
                        )
                for e in perf:
                    if isinstance(e, dict):
                        merged.append(
                            {
                                "name": str(e.get("name") or "")[:220],
                                "type": str(e.get("type") or "resource"),
                                "status": int(e.get("status") or 0),
                                "dur": int(e.get("dur") or 0),
                                "transfer": int(e.get("transfer") or 0),
                                "src": "perf",
                            }
                        )
                state["network"] = merged[-80:]
        except Exception as exc:
            state["js_error"] = str(exc)[:200]

        # cookies names
        try:
            cookies = []
            try:
                cookies = page.cookies(all_domains=True) or []
            except Exception:
                cookies = page.cookies() or []
            names = []
            for c in cookies:
                if isinstance(c, dict):
                    names.append(str(c.get("name") or ""))
                else:
                    names.append(str(getattr(c, "name", "") or ""))
            state["cookies"] = [n for n in names if n]
        except Exception:
            pass

        nets = state.get("network") or []
        blocked = []
        for e in nets:
            if not isinstance(e, dict):
                continue
            st = int(e.get("status") or 0)
            name = str(e.get("name") or "")
            if st in (403, 429, 503) or "cf-error" in name.lower() or "__cf_chl" in name.lower():
                blocked.append(e)
        state["network_403"] = blocked[-20:]

        text_l = (state.get("visible_text") or "").lower()
        url_l = (state.get("url") or "").lower()
        has_403 = any(int(e.get("status") or 0) == 403 for e in blocked if isinstance(e, dict))
        has_429 = any(int(e.get("status") or 0) == 429 for e in blocked if isinstance(e, dict))
        if has_403 or "access denied" in text_l or "cf-error" in text_l or "sorry, you have been blocked" in text_l:
            verdict = "cf_blocked_403"
        elif has_429:
            verdict = "rate_limited_429"
        elif "just a moment" in text_l or "checking your browser" in text_l:
            verdict = "cf_challenge_interstitial"
        elif state.get("turnstile_present") and int(state.get("cf_token_len") or 0) < 80:
            verdict = "turnstile_pending"
        elif any(str(n).lower() == "sso" for n in state.get("cookies") or []):
            verdict = "sso_cookie_present"
        elif "sign-up" in url_l:
            verdict = "still_on_signup"
        else:
            verdict = "unknown"
        state["verdict"] = verdict

        meta_path = Path(str(base) + ".json")
        try:
            meta_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            self.log(f"[diag] write meta failed: {exc}")

        self.log(
            f"[diag] tag={tag} elapsed={(elapsed or 0):.0f}s url={state.get('url','')[:90]} "
            f"cf_token_len={state.get('cf_token_len')} turnstile={state.get('turnstile_present')} "
            f"verdict={verdict} shot={shot.name if shot else '-'}"
        )
        if blocked:
            self.log(f"[diag] network blocked/4xx count={len(blocked)} (403=CF block)")
            for e in blocked[-8:]:
                self.log(
                    f"[diag][net] HTTP {e.get('status')} {e.get('type')} {str(e.get('name') or '')[:120]}"
                )
        else:
            for e in (nets or [])[-6:]:
                if isinstance(e, dict) and e.get("status"):
                    self.log(
                        f"[diag][net] HTTP {e.get('status')} {e.get('type')} {str(e.get('name') or '')[:120]}"
                    )
        return meta_path

    # ── sample: open signup / click email ─────────────────────────────

    def open_signup(self) -> None:
        self._begin_phase("open_signup")
        self.log("[reg] open signup")
        self.refresh_active_page()
        try:
            self.page.get(SIGNUP_URL)
        except Exception as exc:
            self.log(f"[reg] open signup nav error: {exc}")
            try:
                self._page = self._browser.new_tab(SIGNUP_URL) if self._browser else None
            except Exception as exc2:
                self.log(f"[reg] new tab fail: {exc2}")
                self.restart()
                self.page.get(SIGNUP_URL)
        try:
            self.page.wait.doc_loaded()
        except Exception:
            pass
        self._sleep(2)
        if bool(self.cfg.get("network_diag_hook", False)):
            try:
                self._enable_network_diag()
            except Exception:
                pass
        url = str(getattr(self.page, "url", "") or "")
        self.log(f"[reg] url={url}")
        if not url or "x.ai" not in url:
            self.dump_diagnostics("open_signup_bad_url")
            raise BrowserRegisterError(f"signup page not loaded: {url}")
        self._click_email_signup()
        self._maybe_diag()

    def _click_email_signup(self, timeout: float | None = None) -> bool:
        """sample click_email_signup_button with score ranking."""
        timeout = float(timeout if timeout is not None else self.cfg.get("nav_email_button_timeout") or 15)
        deadline = time.time() + timeout
        while time.time() < deadline:
            ready = self._js(
                r"""
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}
const input = Array.from(document.querySelectorAll(
  'input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"]'
)).find((node) => isVisible(node) && !node.disabled);
return !!input;
"""
            )
            if ready:
                self.log("[reg] email form already visible")
                return True
            clicked = self._js(
                r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function nodeText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('href'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function scoreEntry(node) {
    const compact = nodeText(node).replace(/\s+/g, '');
    const lower = compact.toLowerCase();
    if (compact.includes('使用邮箱注册')) return 100;
    if (lower.includes('signupwithemail')) return 95;
    if (lower.includes('continuewithemail')) return 90;
    if (lower.includes('email') && (lower.includes('sign') || lower.includes('continue') || lower.includes('use') || lower.includes('with'))) return 80;
    if (lower === 'email' || lower.includes('邮箱')) return 70;
    return 0;
}
const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
    .map((node) => ({ node, score: scoreEntry(node), text: nodeText(node) }))
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score);
const target = candidates[0]?.node || null;
if (!target) return false;
target.click();
return candidates[0].text || true;
"""
            )
            if clicked:
                self.log(f"[reg] clicked email signup: {clicked}")
                self._sleep(2)
                return True
            self._sleep(1)
        self.log("[reg] email signup button not found")
        return False

    def _email_page_advanced_once(self) -> bool:
        """sample: after email submit, true if code input appears or email field gone."""
        try:
            return bool(
                self._js(
                    r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
    return [
        node.getAttribute('aria-label'),
        node.getAttribute('placeholder'),
        node.getAttribute('name'),
        node.getAttribute('id'),
        node.getAttribute('autocomplete'),
        node.getAttribute('data-testid'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim().toLowerCase();
}
const codeInput = Array.from(document.querySelectorAll('input')).find((node) => {
    if (!isVisible(node)) return false;
    const type = (node.getAttribute('type') || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'checkbox', 'radio', 'file'].includes(type)) return false;
    const meta = textOf(node);
    const inMode = (node.getAttribute('inputmode') || '').toLowerCase();
    return (
        meta.includes('code') || meta.includes('otp') || meta.includes('verif') ||
        meta.includes('验证') || meta.includes('one-time') || inMode === 'numeric' ||
        node.getAttribute('autocomplete') === 'one-time-code'
    );
});
if (codeInput) return true;
const emailInput = Array.from(document.querySelectorAll(
  'input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i]'
)).find((node) => isVisible(node) && !node.disabled && !node.readOnly);
if (!emailInput) return true;
return false;
"""
                )
            )
        except Exception:
            return False

    def _wait_email_page_advanced(self, wait: float = 4.0) -> bool:
        deadline = time.time() + wait
        while time.time() < deadline:
            if self._email_page_advanced_once():
                return True
            self._sleep(0.4)
        return False

    def fill_email(self, email: str) -> None:
        """sample fill_email_and_submit: only return after page actually advances."""
        self._begin_phase("fill_email")
        deadline = time.time() + float(self.cfg.get("email_form_timeout") or 45)
        last_diag = 0.0
        last_reclick = 0.0
        last_snapshot: dict[str, Any] | None = None
        email_js = json.dumps(email)

        while time.time() < deadline:
            self._maybe_diag()
            filled = self._js(
                f"""
const email = {email_js};
function isVisible(node) {{
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}}
function textOf(node) {{
    return [
        node.innerText, node.textContent,
        node.getAttribute('aria-label'), node.getAttribute('title'),
        node.getAttribute('placeholder'), node.getAttribute('data-testid'),
        node.getAttribute('name'), node.getAttribute('id'), node.getAttribute('autocomplete'),
    ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
}}
function describeInput(node) {{
    return [
        `type=${{node.getAttribute('type') || ''}}`,
        `name=${{node.getAttribute('name') || ''}}`,
        `id=${{node.getAttribute('id') || ''}}`,
        `placeholder=${{node.getAttribute('placeholder') || ''}}`,
        `aria=${{node.getAttribute('aria-label') || ''}}`,
        `testid=${{node.getAttribute('data-testid') || ''}}`,
    ].join(' ').replace(/\\s+/g, ' ').trim().slice(0, 160);
}}
function emailCandidates() {{
    const direct = Array.from(document.querySelectorAll(
      'input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i]'
    ));
    const all = Array.from(document.querySelectorAll('input, textarea'));
    for (const node of all) {{
        const type = (node.getAttribute('type') || '').toLowerCase();
        if (['hidden', 'submit', 'button', 'checkbox', 'radio', 'file', 'search'].includes(type)) continue;
        const meta = textOf(node).toLowerCase();
        if (meta.includes('email') || meta.includes('e-mail') || meta.includes('mail') || meta.includes('邮箱') || meta.includes('电子邮件')) {{
            direct.push(node);
        }}
    }}
    return Array.from(new Set(direct));
}}
const visibleInputs = Array.from(document.querySelectorAll('input, textarea'))
    .filter((node) => isVisible(node) && !node.disabled && !node.readOnly)
    .map(describeInput).slice(0, 8);
const visibleActions = Array.from(document.querySelectorAll('button, a, [role="button"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
    .map((n) => textOf(n).slice(0, 80)).filter(Boolean).slice(0, 10);
const input = emailCandidates().find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
if (!input) {{
    return {{ state: 'not-ready', url: location.href, title: document.title, inputs: visibleInputs, buttons: visibleActions }};
}}
input.focus(); input.click();
const valueProto = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
const valueSetter = Object.getOwnPropertyDescriptor(valueProto, 'value')?.set;
const tracker = input._valueTracker;
if (tracker) tracker.setValue('');
if (valueSetter) valueSetter.call(input, email); else input.value = email;
input.dispatchEvent(new InputEvent('beforeinput', {{ bubbles: true, data: email, inputType: 'insertText' }}));
input.dispatchEvent(new InputEvent('input', {{ bubbles: true, data: email, inputType: 'insertText' }}));
input.dispatchEvent(new Event('change', {{ bubbles: true }}));
const inputType = (input.getAttribute('type') || '').toLowerCase();
const isValid = inputType !== 'email' || input.checkValidity();
if ((input.value || '').trim() !== email || !isValid) {{
    return {{ state: 'fill-failed', value: input.value || '', valid: isValid, input: describeInput(input), url: location.href }};
}}
input.blur();
return {{ state: 'filled', input: describeInput(input), url: location.href }};
"""
            )
            state = filled.get("state") if isinstance(filled, dict) else filled
            if isinstance(filled, dict):
                last_snapshot = filled
            if state == "not-ready":
                now = time.time()
                if now - last_reclick >= 3:
                    self._click_email_signup(timeout=3)
                    last_reclick = now
                if now - last_diag >= 5:
                    last_diag = now
                    inputs = " | ".join((filled or {}).get("inputs", [])[:6]) if isinstance(filled, dict) else ""
                    buttons = " | ".join((filled or {}).get("buttons", [])[:8]) if isinstance(filled, dict) else ""
                    self.log(f"[reg] wait email input url={getattr(self.page,'url','')} inputs={inputs or 'none'} buttons={buttons or 'none'}")
                self._sleep(0.5)
                continue
            if state != "filled":
                self.log(f"[reg] email fill failed: {filled}")
                self._sleep(0.5)
                continue

            self._sleep(0.8)
            clicked = self._js(
                r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
    return [
        node.innerText, node.textContent,
        node.getAttribute('aria-label'), node.getAttribute('title'),
        node.getAttribute('placeholder'), node.getAttribute('data-testid'),
        node.getAttribute('name'), node.getAttribute('id'), node.getAttribute('autocomplete'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function emailCandidates() {
    const direct = Array.from(document.querySelectorAll(
      'input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i]'
    ));
    const all = Array.from(document.querySelectorAll('input, textarea'));
    for (const node of all) {
        const type = (node.getAttribute('type') || '').toLowerCase();
        if (['hidden', 'submit', 'button', 'checkbox', 'radio', 'file', 'search'].includes(type)) continue;
        const meta = textOf(node).toLowerCase();
        if (meta.includes('email') || meta.includes('e-mail') || meta.includes('mail') || meta.includes('邮箱')) direct.push(node);
    }
    return Array.from(new Set(direct));
}
const input = emailCandidates().find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
if (!input || !(input.value || '').trim()) return false;
const inputType = (input.getAttribute('type') || '').toLowerCase();
if (inputType === 'email' && !input.checkValidity()) return false;
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true');
const submitButton = buttons.find((node) => {
    const text = textOf(node).replace(/\s+/g, '');
    const lower = text.toLowerCase();
    return (
        text === '注册' || text.includes('注册') || text.includes('继续') || text.includes('下一步') || text.includes('确认') ||
        lower.includes('signup') || lower.includes('sign up') || lower.includes('continue') ||
        lower.includes('next') || lower.includes('createaccount') || lower.includes('submit')
    );
});
if (submitButton) {
    submitButton.click();
    return textOf(submitButton) || true;
}
const form = input.closest('form');
if (form) {
    if (form.requestSubmit) form.requestSubmit();
    else form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    return 'form-submit';
}
input.focus();
input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }));
input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }));
return 'enter';
"""
            )
            if clicked:
                # sample critical: click != submit success; wait page advance
                if self._wait_email_page_advanced(wait=4.0):
                    detail = f" ({clicked})" if isinstance(clicked, str) else ""
                    self.log(f"[reg] email submitted and advanced: {email}{detail}")
                    self._sleep(1)
                    self._log_recent_network("after_email_submit")
                    return
                # F12 Network: CreateEmailValidationCode 403 = CF block — fail fast
                if self._has_create_code_403():
                    self.dump_diagnostics("email_cf_403")
                    raise BrowserRegisterError(
                        "CF blocked CreateEmailValidationCode HTTP 403 "
                        "(email API rejected — use residential proxy / fingerprint browser CDP)"
                    )
                now = time.time()
                if now - last_diag >= 5:
                    last_diag = now
                    self.log(f"[reg] clicked submit but page not advanced, retry: {email}")
                    self._log_recent_network("email_not_advanced")
            self._sleep(0.5)

        if last_snapshot:
            self.dump_diagnostics("email_form_fail")
            raise BrowserRegisterError(
                f"email form fail url={last_snapshot.get('url')} inputs={last_snapshot.get('inputs')} buttons={last_snapshot.get('buttons')}"
            )
        self.dump_diagnostics("email_form_fail")
        raise BrowserRegisterError("email input/submit not found")

    def _collect_network_rows(self) -> list[dict[str, Any]]:
        try:
            net = self._js(
                r"""
const net = (window.__netlog || []).slice(-40);
const perf = [];
try {
  for (const e of (performance.getEntriesByType('resource') || []).slice(-40)) {
    perf.push({url: String(e.name||'').slice(0,220), status: e.responseStatus||0, type: e.initiatorType||''});
  }
} catch(e) {}
return {net, perf};
"""
            )
        except Exception:
            return []
        if not isinstance(net, dict):
            return []
        rows: list[dict[str, Any]] = []
        for e in list(net.get("net") or []) + list(net.get("perf") or []):
            if isinstance(e, dict):
                rows.append(e)
        return rows

    def _has_create_code_403(self) -> bool:
        """True if F12 Network shows CreateEmailValidationCode -> 403 (CF block)."""
        for e in self._collect_network_rows():
            url = str(e.get("url") or e.get("name") or "")
            st = int(e.get("status") or 0)
            if st == 403 and "CreateEmailValidationCode" in url:
                return True
        return False

    def _log_recent_network(self, tag: str) -> None:
        """Print recent F12 Network entries; flag 403 as CF block."""
        rows = self._collect_network_rows()
        bad = [e for e in rows if int(e.get("status") or 0) in (403, 429, 503)]
        if bad:
            self.log(f"[net][{tag}] CF/block responses: {len(bad)}")
            for e in bad[-8:]:
                st = int(e.get("status") or 0)
                url = str(e.get("url") or e.get("name") or "")
                mark = ""
                if st == 403 and "CreateEmailValidationCode" in url:
                    mark = " << CF 403 block (email send API)"
                elif st == 403:
                    mark = " << CF 403 block"
                self.log(f"[net] HTTP {st} {e.get('type') or ''} {url[:140]}{mark}")
        else:
            shown = 0
            for e in rows[-8:]:
                st = int(e.get("status") or 0)
                if st:
                    self.log(
                        f"[net] HTTP {st} {e.get('type') or ''} {str(e.get('url') or e.get('name') or '')[:140]}"
                    )
                    shown += 1
            if not shown:
                self.log(f"[net][{tag}] no statused requests yet")

    def _resend_code(self) -> None:
        self._js(
            r"""
const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'));
const target = nodes.find((node) => {
  const t = (node.innerText || node.textContent || '').replace(/\s+/g, '').toLowerCase();
  return t.includes('重新发送') || t.includes('resend') || t.includes('再次发送');
});
if (target && !target.disabled) { target.click(); return true; }
return false;
"""
        )

    def fill_code(self, code: str) -> None:
        """sample fill_code_and_submit."""
        self._begin_phase("fill_code")
        clean = str(code).replace("-", "").strip()
        display = str(code).strip()
        deadline = time.time() + float(self.cfg.get("code_form_timeout") or 60)
        while time.time() < deadline:
            self._maybe_diag()
            filled = self._js(
                r"""
const code = String(arguments[0] || '').trim();
if (!code) return 'empty-code';
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function setInputValue(input, value) {
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    const tracker = input._valueTracker;
    if (tracker) tracker.setValue('');
    if (nativeSetter) nativeSetter.call(input, value);
    else input.value = value;
    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
}
const aggregate = Array.from(document.querySelectorAll(
  'input[data-input-otp="true"], input[name="code"], input[autocomplete="one-time-code"], input[inputmode="numeric"], input[inputmode="text"]'
)).find((node) => isVisible(node) && !node.disabled && !node.readOnly && Number(node.maxLength || 6) > 1);
if (aggregate) {
    aggregate.focus(); aggregate.click();
    setInputValue(aggregate, code);
    return String(aggregate.value || '').replace(/\s+/g, '') ? 'filled-aggregate' : 'aggregate-failed';
}
const otpBoxes = Array.from(document.querySelectorAll('input')).filter((node) => {
    if (!isVisible(node) || node.disabled || node.readOnly) return false;
    const maxLength = Number(node.maxLength || 0);
    const ac = String(node.autocomplete || '').toLowerCase();
    return maxLength === 1 || ac === 'one-time-code';
});
if (otpBoxes.length >= code.length) {
    for (let i = 0; i < code.length; i += 1) {
        const ch = code[i] || '';
        const box = otpBoxes[i];
        box.focus(); box.click();
        setInputValue(box, ch);
        box.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: ch }));
        box.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: ch }));
    }
    const merged = otpBoxes.slice(0, code.length).map((x) => String(x.value || '').trim()).join('');
    return merged.length ? 'filled-boxes' : 'boxes-failed';
}
return 'not-ready';
""",
                clean,
            )
            if filled == "not-ready":
                # also try with hyphen form for aggregate
                if "-" in display:
                    filled2 = self._js(
                        r"""
const raw = String(arguments[0] || '').trim();
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}
const aggregate = Array.from(document.querySelectorAll(
  'input[data-input-otp="true"], input[name="code"], input[autocomplete="one-time-code"], input[inputmode="numeric"], input[inputmode="text"], input[type="text"]'
)).find((node) => isVisible(node) && !node.disabled && !node.readOnly && Number(node.maxLength || 20) > 1);
if (!aggregate) return 'not-ready';
aggregate.focus(); aggregate.click();
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
const tracker = aggregate._valueTracker;
if (tracker) tracker.setValue('');
if (nativeSetter) nativeSetter.call(aggregate, raw); else aggregate.value = raw;
aggregate.dispatchEvent(new InputEvent('input', { bubbles: true, data: raw, inputType: 'insertText' }));
aggregate.dispatchEvent(new Event('change', { bubbles: true }));
return String(aggregate.value || '').replace(/\s+/g, '') ? 'filled-aggregate' : 'not-ready';
""",
                        display,
                    )
                    filled = filled2
                if filled == "not-ready":
                    self._sleep(0.5)
                    continue
            if filled and "failed" in str(filled):
                self.log(f"[reg] code fill failed: {filled}")
                self._sleep(0.5)
                continue
            clicked = self._js(
                r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const btn = buttons.find((node) => {
    const t = (node.innerText || node.textContent || '').replace(/\s+/g, '').toLowerCase();
    return (
        t.includes('确认邮箱') || t.includes('继续') || t.includes('下一步') ||
        t.includes('confirm') || t.includes('continue') || t.includes('next')
    );
});
if (!btn) return 'no-button';
btn.focus(); btn.click();
return 'clicked';
"""
            )
            if clicked in ("clicked", "no-button"):
                self.log(f"[reg] code submitted: {display} click={clicked}")
                self._sleep(1.5)
                self._log_recent_network("after_code_submit")
                return
            self._sleep(0.5)
        self.dump_diagnostics("code_fail")
        raise BrowserRegisterError("code fill/submit failed")

    # ── sample Turnstile ──────────────────────────────────────────────

    def _sync_cf_token(self, token: str) -> int:
        tjs = json.dumps(token)
        n = self._js(
            f"""
const token = {tjs};
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!cfInput || !token) return 0;
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(cfInput, token); else cfInput.value = token;
cfInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
cfInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
return String(cfInput.value || '').trim().length;
"""
        )
        try:
            return int(n or 0)
        except Exception:
            return 0

    def _dismiss_cookie_banner(self) -> None:
        """OneTrust cookie bar can cover Turnstile checkbox."""
        try:
            self._js(
                r"""
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}
const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'));
const btn = nodes.find((n) => {
  if (!isVisible(n) || n.disabled) return false;
  const t = (n.innerText || n.textContent || '').replace(/\s+/g, '');
  const lower = t.toLowerCase();
  return (
    t.includes('接受所有') || t.includes('全部接受') || t.includes('同意') ||
    lower.includes('accept all') || lower.includes('acceptall') || lower.includes('allow all') ||
    n.id === 'onetrust-accept-btn-handler' || (n.className && String(n.className).includes('accept'))
  );
});
if (btn) { btn.click(); return true; }
const ot = document.querySelector('#onetrust-accept-btn-handler, #accept-recommended-btn-handler');
if (ot) { ot.click(); return true; }
return false;
"""
            )
        except Exception:
            pass

    def _click_turnstile_checkbox(self) -> bool:
        """sample getTurnstileToken shadow-root checkbox path + real mouse coords."""
        page = self.page
        self._dismiss_cookie_banner()
        clicked = False
        try:
            challenge = page.ele("@name=cf-turnstile-response", timeout=0.8)
        except Exception:
            challenge = None
        if challenge is not None:
            try:
                wrapper = challenge.parent()
                iframe = None
                try:
                    iframe = wrapper.shadow_root.ele("tag:iframe", timeout=0.5)
                except Exception:
                    try:
                        iframe = wrapper.shadow_root.ele("css:iframe", timeout=0.5)
                    except Exception:
                        iframe = None
                if iframe is not None:
                    try:
                        iframe.run_js(
                            """
window.dtp = 1;
function getRandomInt(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }
let sx = getRandomInt(800, 1200);
let sy = getRandomInt(400, 700);
Object.defineProperty(MouseEvent.prototype, 'screenX', { value: sx });
Object.defineProperty(MouseEvent.prototype, 'screenY', { value: sy });
"""
                        )
                    except Exception:
                        pass
                    for getter in (
                        lambda: iframe.ele("tag:body", timeout=0.5).shadow_root.ele("tag:input", timeout=0.5),
                        lambda: iframe.ele("tag:body", timeout=0.5).shadow_root.ele(
                            "css:input[type=checkbox]", timeout=0.5
                        ),
                        lambda: iframe.ele("tag:body", timeout=0.5).shadow_root.ele("css:.mark", timeout=0.5),
                        lambda: iframe.ele("tag:body", timeout=0.5).shadow_root.ele("css:label", timeout=0.5),
                    ):
                        try:
                            btn = getter()
                            if btn is not None:
                                try:
                                    btn.click(by_js=False)
                                except Exception:
                                    try:
                                        btn.click()
                                    except Exception:
                                        pass
                                self.log("[reg] clicked turnstile checkbox (shadow)")
                                clicked = True
                                break
                        except Exception:
                            continue
                    # real mouse click left side of widget (checkbox)
                    if not clicked:
                        try:
                            rect = self._js(
                                r"""
const cf = document.querySelector('input[name="cf-turnstile-response"]');
const wrap = cf ? cf.parentElement : null;
const iframe = wrap && (wrap.querySelector('iframe') || (wrap.shadowRoot && wrap.shadowRoot.querySelector('iframe')));
const el = iframe || document.querySelector('iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"]');
if (!el) return null;
el.scrollIntoView({block:'center', inline:'center'});
const r = el.getBoundingClientRect();
return {x: Math.round(r.left + 28), y: Math.round(r.top + r.height/2), w: Math.round(r.width), h: Math.round(r.height)};
"""
                            )
                            if isinstance(rect, dict) and rect.get("x") is not None:
                                x = int(rect["x"]) + random.randint(-2, 2)
                                y = int(rect["y"]) + random.randint(-2, 2)
                                try:
                                    page.actions.move_to(x, y).click()
                                except Exception:
                                    try:
                                        page.run_cdp(
                                            "Input.dispatchMouseEvent",
                                            type="mousePressed",
                                            x=x,
                                            y=y,
                                            button="left",
                                            clickCount=1,
                                        )
                                        page.run_cdp(
                                            "Input.dispatchMouseEvent",
                                            type="mouseReleased",
                                            x=x,
                                            y=y,
                                            button="left",
                                            clickCount=1,
                                        )
                                    except Exception:
                                        pass
                                self.log(f"[reg] clicked turnstile coords ({x},{y})")
                                clicked = True
                        except Exception as exc:
                            self.log(f"[reg] turnstile coord click: {exc}")
            except Exception as exc:
                self.log(f"[reg] turnstile shadow path: {exc}")

        if not clicked:
            try:
                self._js(
                    r"""
const nodes = Array.from(document.querySelectorAll('div,span,iframe')).filter((n) => {
  const txt = (n.className || '') + ' ' + (n.id || '') + ' ' + (n.getAttribute?.('src') || '');
  return String(txt).toLowerCase().includes('turnstile');
});
if (nodes.length && typeof nodes[0].click === 'function') nodes[0].click();
return nodes.length;
"""
                )
            except Exception:
                pass
        return clicked

    def get_turnstile_token(self, rounds: int = 20, *, reset: bool = False) -> str:
        """sample getTurnstileToken — do NOT reset every call (kills pending solve)."""
        if reset:
            try:
                self._js(
                    "try { if (window.turnstile && typeof turnstile.reset === 'function') turnstile.reset(); } catch(e) {}"
                )
            except Exception:
                pass
        for i in range(rounds):
            try:
                token = self._js(
                    r"""
try {
  const byInput = String((document.querySelector('input[name="cf-turnstile-response"]') || {}).value || '').trim();
  if (byInput) return byInput;
  if (window.turnstile && typeof turnstile.getResponse === 'function') {
    return String(turnstile.getResponse() || '').trim();
  }
  return '';
} catch(e) { return ''; }
"""
                )
                token = str(token or "").strip()
                if len(token) >= 80:
                    self.log(f"[reg] turnstile token len={len(token)}")
                    return token
            except Exception:
                pass
            # sample: only click, wait 1s; avoid hammering
            if i % 2 == 0:
                try:
                    self._click_turnstile_checkbox()
                except Exception:
                    pass
            self._sleep(1)
        return ""

    def fill_profile(self) -> dict[str, str]:
        """sample fill_profile_and_submit — wait CF token, no blind force-submit."""
        self._begin_phase("profile_turnstile")
        first, last = _rand_name()
        password = _rand_password()
        deadline = time.time() + float(self.cfg.get("profile_timeout") or 180)
        form_filled = False
        wait_cf_since: float | None = None
        last_cf_retry = 0.0
        self._dismiss_cookie_banner()
        self._sleep(0.5)

        while time.time() < deadline:
            self._maybe_diag()
            if not form_filled:
                filled = self._js(
                    r"""
const givenName = arguments[0];
const familyName = arguments[1];
const password = arguments[2];
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function pickInput(selector) {
    return Array.from(document.querySelectorAll(selector)).find((node) => {
        return isVisible(node) && !node.disabled && !node.readOnly;
    }) || null;
}
function setInputValue(input, value) {
    if (!input) return false;
    input.focus(); input.click();
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    const tracker = input._valueTracker;
    if (tracker) tracker.setValue('');
    if (nativeSetter) nativeSetter.call(input, value); else input.value = value;
    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.blur();
    return String(input.value || '').trim() === String(value || '').trim();
}
const givenInput = pickInput('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"], input[aria-label*="名"]');
const familyInput = pickInput('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"], input[aria-label*="姓"]');
const passwordInput = pickInput('input[data-testid="password"], input[name="password"], input[type="password"], input[autocomplete="new-password"]');
if (!givenInput || !familyInput || !passwordInput) return 'not-ready';
const ok1 = setInputValue(givenInput, givenName);
const ok2 = setInputValue(familyInput, familyName);
const ok3 = setInputValue(passwordInput, password);
if (!ok1 || !ok2 || !ok3) return 'fill-failed';
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const t = (node.innerText || node.textContent || '').replace(/\s+/g, '').toLowerCase();
    return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount');
});
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput
  || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]');
if (cfPresent) {
    const token = String((cfInput && cfInput.value) || '').trim();
    if (token.length < 80) return 'wait-cloudflare:' + token.length;
}
if (submitBtn) return 'ready-to-submit';
return 'filled-no-submit';
""",
                    first,
                    last,
                    password,
                )
                self.log(f"[reg] profile fill state={filled}")
                if isinstance(filled, str) and filled.startswith("wait-cloudflare"):
                    form_filled = True
                    token_len = filled.split(":", 1)[1] if ":" in filled else "0"
                    self.log(f"[reg] profile filled, wait CF token_len={token_len}")
                    if token_len == "0":
                        self._sleep(random.uniform(1, 3))
                    now = time.time()
                    if wait_cf_since is None:
                        wait_cf_since = now
                    if now - wait_cf_since >= 12 and now - last_cf_retry >= 8:
                        self.log("[reg] CF stuck, reuse Turnstile...")
                        tok = self.get_turnstile_token(rounds=12, reset=False)
                        if tok:
                            n = self._sync_cf_token(tok)
                            self.log(f"[reg] turnstile synced len={n}")
                        last_cf_retry = now
                    self._sleep(0.8)
                    continue
                if filled in ("ready-to-submit", "filled-no-submit"):
                    form_filled = True
                elif filled == "fill-failed":
                    self._sleep(0.5)
                    continue
                elif filled == "not-ready":
                    self._sleep(0.5)
                    continue

            submit_state = self._js(
                r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput
  || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]');
if (cfPresent) {
    const token = String((cfInput && cfInput.value) || '').trim();
    if (token.length < 80) return 'wait-cloudflare:' + token.length;
}
function buttonText(node) {
    return [
        node.innerText, node.textContent, node.getAttribute('value'),
        node.getAttribute('aria-label'), node.getAttribute('title'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const t = buttonText(node).replace(/\s+/g, '').toLowerCase();
    return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount');
});
if (!submitBtn) {
    const visibleTexts = buttons.map(buttonText).filter(Boolean).slice(0, 8).join(' | ');
    return 'no-submit-button:' + visibleTexts;
}
submitBtn.focus();
submitBtn.click();
return 'submitted';
"""
            )
            self.log(f"[reg] profile submit={submit_state}")
            if isinstance(submit_state, str) and submit_state.startswith("wait-cloudflare"):
                now = time.time()
                if wait_cf_since is None:
                    wait_cf_since = now
                if now - wait_cf_since >= 12 and now - last_cf_retry >= 8:
                    self.log("[reg] pre-submit CF stuck, reuse Turnstile...")
                    tok = self.get_turnstile_token(rounds=12, reset=False)
                    if tok:
                        n = self._sync_cf_token(tok)
                        self.log(f"[reg] turnstile synced len={n}")
                    last_cf_retry = now
                else:
                    # light poll token without reset
                    tok = self.get_turnstile_token(rounds=2, reset=False)
                    if tok:
                        self._sync_cf_token(tok)
                self._sleep(0.8)
                continue
            if submit_state == "submitted":
                self.log(f"[reg] profile submitted: {first} {last}")
                self._log_recent_network("after_profile_submit")
                self._sleep(2)
                return {"given_name": first, "family_name": last, "password": password}
            wait_cf_since = None
            if isinstance(submit_state, str) and submit_state.startswith("no-submit-button"):
                self.log(f"[reg] no submit button yet: {submit_state}")
            self._sleep(0.5)

        self.dump_diagnostics("profile_fail")
        raise BrowserRegisterError("profile fill/submit failed")

    def wait_sso(self, timeout: float | None = None) -> str:
        """sample wait_for_sso_cookie."""
        timeout = float(timeout if timeout is not None else self.cfg.get("sso_timeout") or 180)
        deadline = time.time() + timeout
        last_seen: set[str] = set()
        last_submit = 0.0
        last_cf_retry = 0.0
        final_no_submit_state = ""
        final_no_submit_since: float | None = None
        final_no_submit_timeout = 25
        self._begin_phase("wait_sso")
        self.log("[reg] wait sso cookie")

        while time.time() < deadline:
            self._maybe_diag()
            try:
                self.refresh_active_page()
                now = time.time()
                if now - last_submit >= 2.5:
                    retried = self._js(
                        r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
const titleHit = !!Array.from(document.querySelectorAll('h1,h2,div,span')).find((el) => {
    const t = (el.textContent || '').replace(/\s+/g, '');
    const lower = t.toLowerCase();
    return t.includes('完成注册') || lower.includes('completeyoursignup') || lower.includes('completesignup');
});
if (!titleHit) return 'not-final-page';
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput
  || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]');
if (cfPresent) {
    const token = String((cfInput && cfInput.value) || '').trim();
    if (token.length < 80) return 'final-page-wait-cf:' + token.length;
}
function buttonText(node) {
    return [
        node.innerText, node.textContent, node.getAttribute('value'),
        node.getAttribute('aria-label'), node.getAttribute('title'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const t = buttonText(node).replace(/\s+/g, '').toLowerCase();
    return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount');
});
if (!submitBtn) {
    const visibleTexts = buttons.map(buttonText).filter(Boolean).slice(0, 8).join(' | ');
    return 'final-page-no-submit:' + visibleTexts;
}
submitBtn.focus();
submitBtn.click();
return 'final-page-clicked-submit';
"""
                    )
                    last_submit = now
                    if retried and retried != "not-final-page":
                        self.log(f"[reg] final page: {retried}")
                    if isinstance(retried, str) and retried.startswith("final-page-no-submit"):
                        if retried != final_no_submit_state:
                            final_no_submit_state = retried
                            final_no_submit_since = now
                        elif final_no_submit_since and now - final_no_submit_since >= final_no_submit_timeout:
                            self.dump_diagnostics("final_no_submit")
                            raise BrowserRegisterError(
                                f"final page no submit for {final_no_submit_timeout}s: {retried}"
                            )
                    else:
                        final_no_submit_state = ""
                        final_no_submit_since = None
                    if isinstance(retried, str) and retried.startswith("final-page-wait-cf"):
                        if now - last_cf_retry >= 10:
                            self.log("[reg] final page CF stuck, reuse Turnstile...")
                            tok = self.get_turnstile_token(rounds=12)
                            if tok:
                                n = self._sync_cf_token(tok)
                                self.log(f"[reg] final turnstile synced len={n}")
                            last_cf_retry = now

                try:
                    cookies = self.page.cookies(all_domains=True, all_info=True) or []
                except TypeError:
                    try:
                        cookies = self.page.cookies(all_domains=True) or []
                    except Exception:
                        cookies = self.page.cookies() or []
                except Exception:
                    cookies = self.page.cookies() or []

                for item in cookies:
                    if isinstance(item, dict):
                        name = str(item.get("name", "")).strip()
                        value = str(item.get("value", "")).strip()
                    else:
                        name = str(getattr(item, "name", "")).strip()
                        value = str(getattr(item, "value", "")).strip()
                    if name:
                        last_seen.add(name)
                    if name == "sso" and value and len(value) > 20:
                        self.log("[reg] sso obtained")
                        return value

                js_sso = self._js(
                    "return (document.cookie || '').split(';').map(s=>s.trim()).find(s=>s.startsWith('sso='))"
                )
                if js_sso and str(js_sso).startswith("sso="):
                    sso = str(js_sso)[4:].strip()
                    if sso:
                        self.log("[reg] sso obtained (js)")
                        return sso
            except BrowserRegisterError:
                raise
            except Exception as exc:
                self.log(f"[reg] wait_sso tick: {exc}")
            self._sleep(1)

        self.dump_diagnostics("sso_timeout")
        raise BrowserRegisterError(f"sso timeout; cookies seen={sorted(last_seen)}")

    def _browser_activate_build(self) -> None:
        """In headed browser (has CF cookies): TOS page + set birth date like sample NSFW prep.

        HTTP-only activate often CF-blocks grok.com; browser session after register works better.
        """
        import random
        from datetime import date

        page = self.page
        # TOS accept page
        try:
            page.get("https://accounts.x.ai/accept-tos")
            self._sleep(2)
            clicked = self._js(
                r"""
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
const btns = Array.from(document.querySelectorAll('button, [role="button"], a'));
const t = btns.find((n) => {
  if (!isVisible(n) || n.disabled) return false;
  const s = (n.innerText || n.textContent || '').replace(/\s+/g, '').toLowerCase();
  return s.includes('accept') || s.includes('agree') || s.includes('同意') || s.includes('接受') || s.includes('继续') || s.includes('continue');
});
if (t) { t.click(); return true; }
return false;
"""
            )
            self.log(f"[reg] accept-tos click={clicked}")
            self._sleep(1.5)
        except Exception as exc:
            self.log(f"[reg] accept-tos: {exc}")

        # birth via fetch in page context (uses browser cookies / cf_clearance)
        today = date.today()
        age = random.randint(20, 40)
        birth = f"{today.year - age}-{random.randint(1,12):02d}-{random.randint(1,28):02d}T16:00:00.000Z"
        try:
            page.get("https://grok.com/")
            self._sleep(2)
            res = self._js(
                f"""
return fetch('https://grok.com/rest/auth/set-birth-date', {{
  method: 'POST',
  credentials: 'include',
  headers: {{ 'content-type': 'application/json', 'origin': 'https://grok.com', 'referer': 'https://grok.com/' }},
  body: JSON.stringify({{ birthDate: {json.dumps(birth)} }})
}}).then(async (r) => ({{ status: r.status, text: (await r.text()).slice(0, 200) }})).catch((e) => ({{ status: 0, text: String(e) }}));
"""
            )
            # DrissionPage may not await promise — poll briefly
            if res is None or (isinstance(res, dict) and res.get("status") is None and "then" in str(type(res)).lower()):
                self._sleep(2)
                res = self._js(
                    r"""
return window.__birthRes || null;
"""
                )
            # better: use async run if available
            try:
                res2 = page.run_js(
                    f"""
async function go() {{
  const r = await fetch('https://grok.com/rest/auth/set-birth-date', {{
    method: 'POST',
    credentials: 'include',
    headers: {{ 'content-type': 'application/json', 'origin': 'https://grok.com', 'referer': 'https://grok.com/' }},
    body: JSON.stringify({{ birthDate: {json.dumps(birth)} }})
  }});
  const text = (await r.text()).slice(0, 200);
  return {{ status: r.status, text }};
}}
return go();
"""
                )
                if res2:
                    res = res2
            except Exception:
                pass
            self.log(f"[reg] set-birth-date via browser: {res}")
        except Exception as exc:
            self.log(f"[reg] set-birth-date browser: {exc}")

    def extract_cf_clearance_and_ua(self) -> tuple[str, str]:
        """sample extract_cf_clearance_and_ua — for TOS/birth after register."""
        cf_clearance = ""
        user_agent = ""
        try:
            self.refresh_active_page()
            page = self._page
            if page is None:
                return "", ""
            try:
                cookies = page.cookies(all_domains=True, all_info=True) or []
            except TypeError:
                try:
                    cookies = page.cookies(all_domains=True) or []
                except Exception:
                    cookies = page.cookies() or []
            for item in cookies:
                if isinstance(item, dict):
                    name = str(item.get("name", "")).strip()
                    value = str(item.get("value", "")).strip()
                else:
                    name = str(getattr(item, "name", "")).strip()
                    value = str(getattr(item, "value", "")).strip()
                if name == "cf_clearance" and value:
                    cf_clearance = value
                    break
            try:
                ua = page.run_js("return navigator.userAgent;")
                if ua:
                    user_agent = str(ua).strip()
            except Exception:
                pass
        except Exception as exc:
            self.log(f"[reg] extract cf_clearance: {exc}")
        return cf_clearance, user_agent


    def login_with_password(self, email: str, password: str) -> str:
        """Password login on accounts.x.ai; return fresh sso cookie."""
        signin_url = "https://accounts.x.ai/sign-in"
        self._begin_phase("password_login")
        self.log(f"[login] open sign-in for {email}")
        try:
            self.page.get(signin_url)
        except Exception as exc:
            raise BrowserRegisterError(f"open sign-in failed: {exc}") from exc
        self._sleep(2)

        # prefer email path
        self._js(
            r"""
function isVisible(node) {
  if (!node) return false;
  const s = window.getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]')).filter(isVisible);
const hit = nodes.find((n) => {
  const t = (n.innerText || n.textContent || '').replace(/\s+/g, '').toLowerCase();
  return t.includes('email') || t.includes('邮箱') || t.includes('password') || t.includes('密码')
    || t.includes('continuewithemail') || t.includes('useyouremail');
});
if (hit) { hit.click(); return true; }
return false;
"""
        )
        self._sleep(1.2)

        email_js = json.dumps(email)
        pwd_js = json.dumps(password)
        filled = self._js(
            f"""
const email = {email_js};
function isVisible(node) {{
  if (!node) return false;
  const s = window.getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}}
const input = Array.from(document.querySelectorAll(
  'input[type="email"], input[name="email"], input[autocomplete="email"], input[data-testid="email"], input[type="text"]'
)).find((n) => isVisible(n) && !n.disabled);
if (!input) return 'no-email';
input.focus(); input.click();
const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
if (setter) setter.call(input, email); else input.value = email;
input.dispatchEvent(new InputEvent('input', {{bubbles:true, data:email, inputType:'insertText'}}));
input.dispatchEvent(new Event('change', {{bubbles:true}}));
return 'ok';
"""
        )
        self.log(f"[login] email fill={filled}")
        if filled != "ok":
            try:
                el = self.page.ele("css:input[type=email]", timeout=2) or self.page.ele(
                    "css:input[name=email]", timeout=1
                )
                if el:
                    el.input(email)
                    filled = "ok"
            except Exception:
                pass
        if filled != "ok":
            raise BrowserRegisterError("login: email input not found")

        self._js(
            r"""
function isVisible(node) {
  if (!node) return false;
  const s = window.getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
const btns = Array.from(document.querySelectorAll('button, [role="button"]')).filter((n) => isVisible(n) && !n.disabled);
const cont = btns.find((n) => {
  const t = (n.innerText || '').replace(/\s+/g, '').toLowerCase();
  return t.includes('continue') || t.includes('next') || t.includes('继续') || t.includes('下一步');
});
if (cont) { cont.click(); return true; }
return false;
"""
        )
        self._sleep(1.5)

        pwd_ok = self._js(
            f"""
const password = {pwd_js};
function isVisible(node) {{
  if (!node) return false;
  const s = window.getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}}
const input = Array.from(document.querySelectorAll(
  'input[type="password"], input[name="password"], input[autocomplete="current-password"], input[data-testid="password"]'
)).find((n) => isVisible(n) && !n.disabled);
if (!input) return 'no-password';
input.focus(); input.click();
const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
if (setter) setter.call(input, password); else input.value = password;
input.dispatchEvent(new InputEvent('input', {{bubbles:true, data:password, inputType:'insertText'}}));
input.dispatchEvent(new Event('change', {{bubbles:true}}));
return 'ok';
"""
        )
        self.log(f"[login] password fill={pwd_ok}")
        if pwd_ok != "ok":
            try:
                el = self.page.ele("css:input[type=password]", timeout=3)
                if el:
                    el.input(password)
                    pwd_ok = "ok"
            except Exception:
                pass
        if pwd_ok != "ok":
            raise BrowserRegisterError("login: password input not found")

        try:
            self._dismiss_cookie_banner()
            cf_len = int(
                self._js(
                    r"""
const cf = document.querySelector('input[name="cf-turnstile-response"]');
return String((cf && cf.value) || '').trim().length;
"""
                )
                or 0
            )
            if cf_len < 80:
                self._click_turnstile_checkbox()
                for _ in range(24):
                    cf_len = int(
                        self._js(
                            r"""
const cf = document.querySelector('input[name="cf-turnstile-response"]');
return String((cf && cf.value) || '').trim().length;
"""
                        )
                        or 0
                    )
                    if cf_len >= 80:
                        break
                    self._sleep(0.5)
                    if _ in (6, 12, 18):
                        tok = self.get_turnstile_token(rounds=8, reset=False)
                        if tok:
                            self._sync_cf_token(tok)
                    else:
                        self._click_turnstile_checkbox()
                self.log(f"[login] turnstile token_len={cf_len}")
        except Exception as exc:
            self.log(f"[login] turnstile: {exc}")

        submitted = self._js(
            r"""
function isVisible(node) {
  if (!node) return false;
  const s = window.getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
const btns = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"]'))
  .filter((n) => isVisible(n) && !n.disabled);
const btn = btns.find((n) => {
  const t = (n.innerText || n.textContent || '').replace(/\s+/g, '').toLowerCase();
  return t.includes('log in') || t.includes('login') || t.includes('sign in') || t.includes('登录')
    || t.includes('continue') || t.includes('继续') || t.includes('submit') || t === 'next';
}) || btns.find((n) => (n.getAttribute('type') || '') === 'submit');
if (btn) { btn.click(); return (btn.innerText || 'submit').slice(0, 30); }
return false;
"""
        )
        self.log(f"[login] submit={submitted}")
        if not submitted:
            try:
                self.page.actions.key_down("ENTER").key_up("ENTER")
            except Exception:
                pass

        sso = self.wait_sso(timeout=float(self.cfg.get("sso_timeout") or 180))
        self.log("[login] password login got fresh sso")
        self.ensure_grok_session(sso)
        return sso

    def _inject_sso_cookies(self, sso: str) -> int:
        """Mirror sso/sso-rw onto x.ai + grok.com so grok.com sees the session."""
        sso = (sso or "").strip()
        if not sso:
            return 0
        cookies: list[dict[str, Any]] = []
        for domain in (".x.ai", "accounts.x.ai", "auth.x.ai", ".grok.com", "grok.com"):
            for name in ("sso", "sso-rw"):
                cookies.append(
                    {
                        "name": name,
                        "value": sso,
                        "domain": domain,
                        "path": "/",
                        "secure": True,
                        "httpOnly": False,
                        "sameSite": "None",
                    }
                )
        n = 0
        try:
            # warm a page so cookie setter has a context
            cur = str(getattr(self.page, "url", "") or "")
            if "x.ai" not in cur and "grok.com" not in cur:
                try:
                    self.page.get("https://accounts.x.ai/")
                    self._sleep(0.8)
                except Exception:
                    pass
            self.page.set.cookies(cookies)
            n = len(cookies)
            self.log(f"[login] injected sso cookies n={n}")
        except Exception as exc:
            self.log(f"[login] inject sso cookies failed: {exc}")
            # fallback: set document.cookie on grok.com after open
        return n

    def _page_looks_logged_in(self) -> bool:
        info = self._js(
            r"""
const body = (document.body && document.body.innerText || '').slice(0, 2500);
const low = body.toLowerCase();
const hasComposer = !!document.querySelector(
  'textarea, [contenteditable="true"], [role="textbox"]'
);
const loginCta = /免费注册|注册以无缝|sign up to|continue with google|登录\s*注册/.test(body)
  || (body.includes('登录') && body.includes('注册') && body.includes('免费注册'));
const hasCookieBanner = body.includes('接受所有 Cookie') || body.includes('全部拒绝')
  || low.includes('accept all cookies');
return {
  url: location.href,
  hasComposer: hasComposer,
  loginCta: loginCta,
  hasCookieBanner: hasCookieBanner,
  hasSso: document.cookie.split(';').some((s) => s.trim().startsWith('sso=')),
  bodyHead: body.slice(0, 180)
};
"""
        )
        if not isinstance(info, dict):
            return False
        self.log(
            f"[login] session check composer={info.get('hasComposer')} loginCta={info.get('loginCta')} "
            f"banner={info.get('hasCookieBanner')} sso={info.get('hasSso')} url={str(info.get('url') or '')[:80]}"
        )
        if info.get("loginCta"):
            return False
        if info.get("hasComposer") and info.get("hasSso"):
            return True
        # composer may load late; no login CTA + sso is good enough
        return bool(info.get("hasSso")) and not info.get("loginCta")

    def ensure_grok_session(self, sso: str, *, timeout: float = 45) -> bool:
        """After password login: inject cookies, open grok.com, clear banners, verify session."""
        self._begin_phase("ensure_grok_session")
        self._inject_sso_cookies(sso)
        try:
            self.page.get("https://grok.com/")
        except Exception as exc:
            self.log(f"[login] open grok.com: {exc}")
        self._sleep(2.5)
        # cookie banner first (blocks UI)
        for _ in range(5):
            try:
                self._dismiss_age_and_banners()
            except Exception as exc:
                self.log(f"[login] dismiss: {exc}")
            # extra direct click for OneTrust CN labels
            hit = self._js(
                r"""
function isVisible(node) {
  if (!node) return false;
  const s = window.getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
const nodes = Array.from(document.querySelectorAll('button, [role="button"], a')).filter(isVisible);
const hit = nodes.find((n) => {
  const t = (n.innerText || n.textContent || '').replace(/\s+/g, '');
  const low = t.toLowerCase();
  return t.includes('接受所有Cookie') || t.includes('接受所有') || low.includes('acceptallcookies')
    || low.includes('accept all cookies') || n.id === 'onetrust-accept-btn-handler';
});
if (hit) { hit.click(); return (hit.innerText||'').replace(/\s+/g,'').slice(0,30); }
const ot = document.querySelector('#onetrust-accept-btn-handler');
if (ot) { ot.click(); return 'onetrust'; }
return false;
"""
            )
            if hit:
                self.log(f"[login] cookie accept={hit}")
                self._sleep(0.8)
            if self._page_looks_logged_in():
                self.log("[login] grok session ready")
                return True
            self._sleep(1.2)
            # re-inject mid-wait in case navigation dropped cookies
            if _ in (1, 3):
                self._inject_sso_cookies(sso)
                try:
                    self.page.refresh()
                except Exception:
                    try:
                        self.page.get("https://grok.com/")
                    except Exception:
                        pass
                self._sleep(2)
        ok = self._page_looks_logged_in()
        if not ok:
            self.dump_diagnostics("login_session_not_ready")
            self.log("[login] WARN grok session not fully ready (will still try web)")
        return ok

    def _page_has_age_gate(self) -> bool:
        body = str(self._js("return (document.body&&document.body.innerText||'')") or "")
        low = body.lower()
        markers = (
            "确认你的年龄",
            "请确认你的年龄",
            "出生年份",
            "选择你的出生年份",
            "confirm your age",
            "birth year",
            "select your birth",
            "verify your age",
        )
        return any(m in body or m in low for m in markers)

    def _dismiss_age_and_banners(self) -> None:
        """Clear cookie banner + age modal. NEVER click file/attach (opens OS file picker)."""
        # 1) cookie only (safe labels)
        for _ in range(3):
            hit = self._js(
                r"""
function isVisible(node) {
  if (!node) return false;
  const s = window.getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
const btns = Array.from(document.querySelectorAll('button, [role="button"]')).filter((n) => isVisible(n) && !n.disabled);
const hit = btns.find((n) => {
  const t = (n.innerText || n.textContent || '').replace(/\s+/g, '');
  const low = t.toLowerCase();
  // never touch attach/upload/file
  if (low.includes('attach') || low.includes('upload') || low.includes('file') || t.includes('附件') || t.includes('上传')) return false;
  return t.includes('接受所有Cookie') || t.includes('接受所有') || low.includes('accept all')
    || low.includes('acceptall') || low.includes('got it')
    || t === '忽略' || low.includes('dismiss') || low.includes('not now')
    || n.id === 'onetrust-accept-btn-handler';
});
if (hit) { hit.click(); return (hit.innerText||'').replace(/\s+/g,'').slice(0,24); }
const ot = document.querySelector('#onetrust-accept-btn-handler, #accept-recommended-btn-handler');
if (ot) { ot.click(); return 'onetrust'; }
return false;
"""
            )
            if not hit:
                break
            self.log(f"[web] banner={hit}")
            self._sleep(0.5)

        # 2) wait for age modal text
        for _ in range(10):
            if self._page_has_age_gate():
                break
            self._sleep(0.4)
        if not self._page_has_age_gate():
            return
        self.log("[web] age gate detected")

        # 3) ONLY: fill year input (never input[type=file]) then click 确定
        # Prefer native REST birth set first (already works in activate path)
        try:
            import random as _r
            from datetime import date

            today = date.today()
            age = _r.randint(22, 35)
            birth = f"{today.year - age}-{_r.randint(1,12):02d}-{_r.randint(1,28):02d}T16:00:00.000Z"
            api = self._js(
                f"""
return fetch('https://grok.com/rest/auth/set-birth-date', {{
  method: 'POST', credentials: 'include',
  headers: {{'content-type':'application/json','origin':'https://grok.com','referer':'https://grok.com/'}},
  body: JSON.stringify({{birthDate: {json.dumps(birth)}}})
}}).then(async (r) => ({{status:r.status, text:(await r.text()).slice(0,120)}}))
  .catch((e) => ({{status:0, text:String(e)}}));
"""
            )
            # poll promise-like result
            for _ in range(10):
                self._sleep(0.3)
                if isinstance(api, dict) and api.get("status") is not None:
                    break
                api = self._js("return window.__ageApi || null;")
            # Drission may resolve async differently — also try run_js async
            try:
                api2 = self.page.run_js(
                    f"""
async function go() {{
  const r = await fetch('https://grok.com/rest/auth/set-birth-date', {{
    method: 'POST', credentials: 'include',
    headers: {{'content-type':'application/json','origin':'https://grok.com','referer':'https://grok.com/'}},
    body: JSON.stringify({{birthDate: {json.dumps(birth)}}})
  }});
  return {{status: r.status, text: (await r.text()).slice(0,120)}};
}}
return go();
"""
                )
                if api2:
                    api = api2
            except Exception:
                pass
            self.log(f"[web] age api set-birth-date={api}")
            # if API ok OR already locked, reload chat so modal goes away
            st = 0
            txt = ""
            try:
                if isinstance(api, dict):
                    st = int(api.get("status") or 0)
                    txt = str(api.get("text") or "")
            except Exception:
                st = 0
            locked = "birth-date" in txt.lower() or "locked" in txt.lower() or "already" in txt.lower()
            if 200 <= st < 300 or st in (400, 409, 429) or locked:
                try:
                    self.page.get("https://grok.com/")
                    self._sleep(2.5)
                except Exception:
                    pass
                # locked means age already set — treat gate as done even if residual text
                if locked or not self._page_has_age_gate():
                    self.log("[web] age gate cleared via API/locked")
                    return
        except Exception as exc:
            self.log(f"[web] age api: {exc}")

        # 4) UI path: type year into visible non-file input, then 确定
        year_ok = self._js(
            r"""
function isVisible(node) {
  if (!node) return false;
  const s = window.getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
// NEVER touch file inputs
const inputs = Array.from(document.querySelectorAll('input')).filter((n) => {
  if (!isVisible(n) || n.disabled) return false;
  const ty = (n.getAttribute('type') || 'text').toLowerCase();
  if (ty === 'file' || ty === 'hidden' || ty === 'checkbox' || ty === 'radio' || ty === 'submit' || ty === 'button') return false;
  return true;
});
let inp = inputs.find((n) => {
  const meta = [n.getAttribute('placeholder'), n.getAttribute('aria-label'), n.getAttribute('name'), n.getAttribute('id')]
    .filter(Boolean).join(' ');
  return meta.includes('出生') || meta.toLowerCase().includes('year') || meta.toLowerCase().includes('birth');
});
// if modal open, prefer input inside dialog
if (!inp) {
  const dlg = document.querySelector('[role="dialog"], [aria-modal="true"]');
  if (dlg) {
    inp = Array.from(dlg.querySelectorAll('input')).find((n) => {
      const ty = (n.getAttribute('type') || 'text').toLowerCase();
      return ty !== 'file' && ty !== 'hidden' && isVisible(n);
    }) || null;
  }
}
if (!inp && inputs.length) inp = inputs[0];
if (!inp) return 'no-input';
inp.focus();
inp.click();
const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
if (setter) setter.call(inp, '1995'); else inp.value = '1995';
inp.dispatchEvent(new InputEvent('beforeinput', {bubbles:true, data:'1995', inputType:'insertText'}));
inp.dispatchEvent(new InputEvent('input', {bubbles:true, data:'1995', inputType:'insertText'}));
inp.dispatchEvent(new Event('change', {bubbles:true}));
inp.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', code:'Enter', bubbles:true}));
inp.dispatchEvent(new KeyboardEvent('keyup', {key:'Enter', code:'Enter', bubbles:true}));
return 'filled:' + String(inp.value||'');
"""
        )
        self.log(f"[web] age year input={year_ok}")
        self._sleep(0.5)

        # if dropdown list appeared, click 1995 option only (exact year text)
        self._js(
            r"""
function isVisible(node) {
  if (!node) return false;
  const s = window.getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
const opts = Array.from(document.querySelectorAll('[role="option"], li, button, div, span')).filter(isVisible);
const y = opts.find((n) => (n.innerText||'').trim() === '1995');
if (y) { y.click(); return true; }
return false;
"""
        )
        self._sleep(0.4)

        # click 保存 / 确定 / 继续 — user confirmed primary is 「保存」
        conf = self._js(
            r"""
function isVisible(node) {
  if (!node) return false;
  const s = window.getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
const btns = Array.from(document.querySelectorAll('button, [role="button"]')).filter((n) => isVisible(n) && !n.disabled);
const dlg = document.querySelector('[role="dialog"], [aria-modal="true"]');
const pool = dlg ? Array.from(dlg.querySelectorAll('button, [role="button"]')).filter((n) => isVisible(n) && !n.disabled) : btns;
// 保存 first (age modal primary on current grok.com UI)
const order = ['保存', 'Save', '确定', '确认', '继续', 'Confirm', 'Continue', 'OK'];
for (const lab of order) {
  const b = pool.find((n) => {
    const t = (n.innerText || n.textContent || '').replace(/\s+/g, '').trim();
    const low = t.toLowerCase();
    if (t.includes('取消') || low.includes('cancel')) return false;
    if (low.includes('attach') || low.includes('upload') || t.includes('附件') || t.includes('上传')) return false;
    return t === lab || t.toLowerCase() === lab.toLowerCase();
  });
  if (b) {
    if (b.disabled || b.getAttribute('aria-disabled') === 'true') return 'disabled:' + lab;
    b.click();
    return 'click:' + lab;
  }
}
// fallback: full-width filled primary button in dialog
const primary = pool.find((n) => {
  const cls = String(n.className || '');
  const t = (n.innerText || '').replace(/\s+/g, '').trim();
  return (cls.includes('button-filled') || cls.includes('bg-button-filled'))
    && t && !t.includes('取消') && t.length < 12;
});
if (primary) { primary.click(); return 'click-primary:' + (primary.innerText||'').replace(/\s+/g,'').slice(0,12); }
return 'no-confirm';
"""
        )
        self.log(f"[web] age confirm={conf}")
        if conf == "no-confirm" or (isinstance(conf, str) and conf.startswith("disabled")):
            for lab in ("保存", "Save", "确定", "确认", "继续"):
                try:
                    btn = self.page.ele(f"text={lab}", timeout=0.5)
                    if btn:
                        btn.click()
                        self.log(f"[web] age confirm ele={lab}")
                        break
                except Exception:
                    continue
        self._sleep(1.5)

        if self._page_has_age_gate():
            self.log("[web] age gate still present after dismiss")
            self.dump_diagnostics("web_age_gate")
        else:
            self.log("[web] age gate cleared")

    def _ui_type_and_send(self, prompt: str) -> bool:
        """Type into composer and send (Enter). Never clicks attach/file."""
        filled = self._js(
            f"""
const prompt = {json.dumps(prompt)};
function isVisible(node) {{
  if (!node) return false;
  const s = window.getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}}
const boxes = Array.from(document.querySelectorAll(
  'div[contenteditable="true"], [role="textbox"], textarea'
)).filter(isVisible);
const box = boxes.length ? boxes[boxes.length - 1] : null;
if (!box) return false;
box.focus(); box.click();
if (box.isContentEditable || box.getAttribute('contenteditable') === 'true') {{
  box.innerText = prompt;
  box.textContent = prompt;
}} else {{
  const setter = Object.getOwnPropertyDescriptor(
    box.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value'
  )?.set;
  if (setter) setter.call(box, prompt); else box.value = prompt;
}}
box.dispatchEvent(new InputEvent('input', {{ bubbles: true, data: prompt, inputType: 'insertText' }}));
box.dispatchEvent(new Event('change', {{ bubbles: true }}));
const opts = {{ key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true }};
box.dispatchEvent(new KeyboardEvent('keydown', opts));
box.dispatchEvent(new KeyboardEvent('keypress', opts));
box.dispatchEvent(new KeyboardEvent('keyup', opts));
return true;
"""
        )
        if filled:
            try:
                self.page.actions.key_down("ENTER").key_up("ENTER")
            except Exception:
                pass
            return True
        # ele fallback
        try:
            for sel in ("css:textarea", "css:[contenteditable=true]", "css:[role=textbox]"):
                el = self.page.ele(sel, timeout=0.8)
                if not el:
                    continue
                try:
                    el.click()
                except Exception:
                    pass
                try:
                    el.clear()
                except Exception:
                    pass
                el.input(prompt)
                try:
                    self.page.actions.key_down("ENTER").key_up("ENTER")
                except Exception:
                    pass
                return True
        except Exception:
            pass
        return False

    def browser_web_default_chat(self) -> dict[str, Any]:
        """UI probe: open grok.com chat box, type message, wait for assistant reply.

        Prefer real composer interaction over REST (REST often hits anti-bot).
        Auto-sends after age gate clear — no manual typing required.
        """
        self._begin_phase("web_default_chat")
        model = str(self.cfg.get("web_default_model") or "default-ui")
        prompt = str(self.cfg.get("web_probe_prompt") or "你好")
        timeout = float(self.cfg.get("web_probe_timeout") or 60)
        try:
            cur = str(getattr(self.page, "url", "") or "")
            if "grok.com" not in cur:
                self.page.get("https://grok.com/")
            self._sleep(2)
        except Exception as exc:
            self.log(f"[web] open grok.com: {exc}")

        # Age gate + cookie banners (may appear after paint / after focusing composer)
        try:
            self._dismiss_age_and_banners()
        except Exception as exc:
            self.log(f"[web] dismiss err: {exc}")

        # snapshot message count / assistant texts before send
        before_info = self._js(
            r"""
function isVisible(node) {
  if (!node) return false;
  const s = window.getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
const msgs = Array.from(document.querySelectorAll(
  '[data-message-author-role="assistant"], [data-testid*="assistant"], [data-testid*="message"]'
)).filter(isVisible);
return {
  msgCount: msgs.length,
  bodyLen: (document.body && document.body.innerText || '').length
};
"""
        )
        before_count = int((before_info or {}).get("msgCount") or 0) if isinstance(before_info, dict) else 0
        before_len = int((before_info or {}).get("bodyLen") or 0) if isinstance(before_info, dict) else 0

        # 1) find composer and type prompt (contenteditable / textarea)
        filled = self._js(
            f"""
const prompt = {json.dumps(prompt)};
function isVisible(node) {{
  if (!node) return false;
  const s = window.getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}}
function setNative(el, value) {{
  el.focus();
  if (el.isContentEditable || el.getAttribute('contenteditable') === 'true') {{
    el.innerText = value;
    el.textContent = value;
    el.dispatchEvent(new InputEvent('input', {{ bubbles: true, data: value, inputType: 'insertText' }}));
    return (el.innerText || el.textContent || '').includes(value.slice(0, Math.min(4, value.length)));
  }}
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
  const tracker = el._valueTracker;
  if (tracker) tracker.setValue('');
  if (setter) setter.call(el, value); else el.value = value;
  el.dispatchEvent(new InputEvent('input', {{ bubbles: true, data: value, inputType: 'insertText' }}));
  el.dispatchEvent(new Event('change', {{ bubbles: true }}));
  return String(el.value || '').length > 0;
}}
const selectors = [
  'textarea[aria-label]',
  'textarea[placeholder]',
  'div[contenteditable="true"]',
  '[role="textbox"]',
  'textarea',
  'div[data-testid*="chat"] textarea',
  'div[data-testid*="composer"] textarea',
  'form textarea',
];
let box = null;
for (const sel of selectors) {{
  const nodes = Array.from(document.querySelectorAll(sel)).filter((n) => {{
    if (!isVisible(n)) return false;
    // never treat file inputs as composer
    if (n.tagName === 'INPUT' && (n.type || '').toLowerCase() === 'file') return false;
    return true;
  }});
  if (nodes.length) {{ box = nodes[nodes.length - 1]; break; }}
}}
if (!box) {{
  const all = Array.from(document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]'));
  box = all.find((n) => {{
    if (!isVisible(n)) return false;
    const meta = [n.getAttribute('placeholder'), n.getAttribute('aria-label'), n.getAttribute('data-testid')]
      .filter(Boolean).join(' ').toLowerCase();
    return meta.includes('ask') || meta.includes('message') || meta.includes('chat')
      || meta.includes('grok') || meta.includes('输入') || meta.includes('说点') || meta.includes('帮助');
  }}) || null;
}}
if (!box) return {{ ok: false, reason: 'no-composer' }};
const ok = setNative(box, prompt);
box.scrollIntoView({{ block: 'center' }});
return {{ ok: !!ok, tag: box.tagName, ce: !!box.isContentEditable, preview: (box.value || box.innerText || '').slice(0, 40) }};
"""
        )
        self.log(f"[web] composer fill={filled}")
        # focusing composer often triggers age modal — clear again before send
        try:
            if self._page_has_age_gate():
                self.log("[web] age gate after composer focus, dismiss again")
                self._dismiss_age_and_banners()
        except Exception as exc:
            self.log(f"[web] dismiss after composer: {exc}")
        if self._page_has_age_gate():
            self.dump_diagnostics("web_age_gate_block")
            return {
                "ok": False,
                "code": "web_age_gate",
                "status": 0,
                "model": model,
                "text": "age gate still present",
                "endpoint": "grok.com/ui",
                "method": "composer",
            }
        if not (isinstance(filled, dict) and filled.get("ok")):
            # Drission element fallback (never file inputs)
            try:
                for sel in (
                    "css:textarea",
                    "css:[contenteditable=true]",
                    "css:[role=textbox]",
                    "tag:textarea",
                ):
                    el = self.page.ele(sel, timeout=1)
                    if el:
                        try:
                            el.click()
                        except Exception:
                            pass
                        try:
                            el.clear()
                        except Exception:
                            pass
                        el.input(prompt)
                        filled = {"ok": True, "via": "ele", "sel": sel}
                        self.log(f"[web] composer via ele {sel}")
                        break
            except Exception as exc:
                self.log(f"[web] ele fill: {exc}")
        if not (isinstance(filled, dict) and filled.get("ok")):
            # dump visible controls for diagnosis
            snap = self._js(
                r"""
function isVisible(node) {
  if (!node) return false;
  const s = window.getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
const inputs = Array.from(document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"], input'))
  .filter(isVisible).slice(0, 12).map((n) => ({
    tag: n.tagName, ph: n.getAttribute('placeholder')||'', aria: n.getAttribute('aria-label')||'',
    testid: n.getAttribute('data-testid')||'', ce: !!n.isContentEditable
  }));
return { url: location.href, title: document.title, inputs, text: (document.body&&document.body.innerText||'').slice(0, 500) };
"""
            )
            self.log(f"[web] no composer snap={snap}")
            self.dump_diagnostics("web_no_composer")
            return {
                "ok": False,
                "code": "web_no_composer",
                "status": 0,
                "model": model,
                "text": str(snap)[:400],
                "endpoint": "grok.com/ui",
            }

        self._sleep(0.4)
        # 2) Prefer Enter on composer (avoid wrong toolbar buttons like model switch)
        sent = self._js(
            r"""
function isVisible(node) {
  if (!node) return false;
  const s = window.getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
// focus composer again
const boxes = Array.from(document.querySelectorAll(
  'div[contenteditable="true"], [role="textbox"], textarea'
)).filter(isVisible);
const box = boxes.length ? boxes[boxes.length - 1] : document.activeElement;
if (box) {
  box.focus();
  const opts = { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true };
  box.dispatchEvent(new KeyboardEvent('keydown', opts));
  box.dispatchEvent(new KeyboardEvent('keypress', opts));
  box.dispatchEvent(new KeyboardEvent('keyup', opts));
  // some UIs want Ctrl/Meta+Enter; try plain Enter first
  return 'enter';
}
// strict send button only — NEVER attach/upload/file (opens OS file picker)
const buttons = Array.from(document.querySelectorAll('button, [role="button"]')).filter((n) => isVisible(n) && !n.disabled);
const send = buttons.find((n) => {
  const t = [n.getAttribute('aria-label'), n.getAttribute('title'), n.getAttribute('data-testid'), n.innerText, n.textContent]
    .filter(Boolean).join(' ').replace(/\s+/g, ' ').trim().toLowerCase();
  if (!t) return false;
  if (t.includes('switch') || t.includes('model') || t.includes('切换') || t.includes('attach')
    || t.includes('upload') || t.includes('file') || t.includes('附件') || t.includes('上传')
    || t.includes('menu') || t.includes('settings') || t.includes('voice') || t.includes('image')) return false;
  // also skip if button wraps a file input
  if (n.querySelector('input[type="file"]')) return false;
  return t === 'send' || t === '发送' || t === '提交' || t.startsWith('send ') || t.includes('send message')
    || t.includes('发送消息') || (t.includes('send') && t.length < 24);
});
if (send) {
  send.click();
  const lab = (send.getAttribute('aria-label') || send.innerText || 'send').slice(0, 40);
  return 'clicked:' + lab;
}
return 'no-send';
"""
        )
        if sent in (None, "no-send", False):
            try:
                self.page.actions.key_down("ENTER").key_up("ENTER")
                sent = "actions-enter"
            except Exception:
                pass
        try:
            self.log(f"[web] send={sent}")
        except Exception:
            self.log("[web] send=ok")

        # 3) wait for real assistant reply (not chrome UI shell)
        # if age modal appears mid-wait, clear it and resend once
        resend_after_age = False
        chrome_noise = (
            "切换侧边栏", "新建聊天", "私密模式", "你想知道什么", "确认你的年龄", "出生年份",
            "关联你的", "解锁进阶", "技能和连接器", "switch sidebar", "new chat", "what do you want",
            "ctrl+k", "ctrl+j", "imagine", "fast", "今天需要我如何帮助", "请确认你的年龄",
            "选择你的出生年份", "解锁进阶功能",
        )
        deadline = time.time() + timeout
        last_text = ""
        while time.time() < deadline:
            self._sleep(1.2)
            if self._page_has_age_gate():
                self.log("[web] age gate during wait, dismiss + resend")
                try:
                    self._dismiss_age_and_banners()
                except Exception as exc:
                    self.log(f"[web] age during wait: {exc}")
                if not self._page_has_age_gate() and not resend_after_age:
                    resend_after_age = True
                    ok_send = self._ui_type_and_send(prompt)
                    self.log(f"[web] auto resend after age clear ok={ok_send}")
                continue
            info = self._js(
                r"""
function isVisible(node) {
  if (!node) return false;
  const s = window.getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
const body = (document.body && document.body.innerText || '');
// prefer explicit assistant role nodes
let assistant = Array.from(document.querySelectorAll(
  '[data-message-author-role="assistant"], [data-testid*="assistant-message"], [data-testid*="bot-message"]'
)).filter(isVisible).map((n) => (n.innerText || '').trim()).filter(Boolean);
// fallback: message list nodes that are not the composer
if (!assistant.length) {
  const msgs = Array.from(document.querySelectorAll(
    '[data-testid*="message"], [data-message-author-role], [class*="message-bubble"], [class*="Message"]'
  )).filter(isVisible).map((n) => (n.innerText || '').trim()).filter((t) => t.length > 1);
  assistant = msgs;
}
// streaming indicator
const streaming = !!(document.querySelector('[data-testid*="stop"], button[aria-label*="Stop"], button[aria-label*="停止"]'));
return {
  body: body.slice(0, 2500),
  assistant: assistant.slice(-3),
  msgCount: assistant.length,
  streaming: streaming,
  bodyLen: body.length,
  url: location.href
};
"""
            )
            if not isinstance(info, dict):
                continue
            assistants = info.get("assistant") or []
            if not isinstance(assistants, list):
                assistants = []
            body = str(info.get("body") or "")
            last_text = body
            msg_count = int(info.get("msgCount") or 0)
            # pick last assistant-like bubble that is not chrome noise / not pure prompt echo
            reply = ""
            for cand in reversed([str(x) for x in assistants if x]):
                c = cand.strip()
                if not c or c == prompt:
                    continue
                low = c.lower()
                if any(n in c or n in low for n in chrome_noise):
                    continue
                if "just a moment" in low or "anti-bot" in low:
                    continue
                # must contain something beyond UI chrome: length + not only nav words
                if len(c) < 2:
                    continue
                # if candidate still mostly chrome, skip
                chrome_hits = sum(1 for n in chrome_noise if n in c or n in low)
                if chrome_hits >= 2 and len(c) < 80:
                    continue
                reply = c[:400]
                break
            # also accept: msg count increased and last bubble looks like reply after our prompt
            if not reply and msg_count > before_count and assistants:
                tail = str(assistants[-1] or "").strip()
                if tail and tail != prompt and len(tail) >= 2:
                    low = tail.lower()
                    if not any(n in tail or n in low for n in chrome_noise[:8]):
                        reply = tail[:400]
            if reply:
                # still streaming? wait a bit for full text
                if info.get("streaming"):
                    self._sleep(1.5)
                    continue
                preview = reply[:120].encode("ascii", errors="replace").decode("ascii")
                self.log(f"[web] UI reply ok text={preview!r}")
                return {
                    "ok": True,
                    "code": "web_ok",
                    "status": 200,
                    "model": model,
                    "text": reply[:400],
                    "endpoint": "grok.com/ui",
                    "method": "composer",
                }

        self.dump_diagnostics("web_ui_timeout")
        snap = (last_text or "")[:300]
        preview = snap.encode("ascii", errors="replace").decode("ascii")
        self.log(f"[web] UI timeout no reply snap={preview!r}")
        return {
            "ok": False,
            "code": "web_ui_timeout",
            "status": 0,
            "model": model,
            "text": snap,
            "endpoint": "grok.com/ui",
            "method": "composer",
        }


    def browser_build_authorize(self) -> dict[str, Any]:
        """In same browser: open Build OAuth authorize, capture page/URL code -> token.

        After consent, xAI may show the OAuth code on-page (redirect_uri localhost).
        Scrape that code + exchange with PKCE. Also listen on 127.0.0.1:56121.
        """
        import base64
        import hashlib
        import os
        import re
        import threading
        import urllib.parse
        from http.server import BaseHTTPRequestHandler, HTTPServer

        from grokreg.mint.auth_code import (
            CLIENT_ID,
            GROK_PLAN,
            GROK_REFERRER,
            GROK_TOKEN_UA,
            GROK_VERSION,
            OIDC_ISSUER,
            REDIRECT_URI,
            SCOPES,
            decode_jwt_payload,
        )

        self._begin_phase("browser_build_authorize")
        verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()
        nonce = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()
        params = urllib.parse.urlencode(
            {
                "client_id": CLIENT_ID,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "nonce": nonce,
                "plan": GROK_PLAN,
                "redirect_uri": REDIRECT_URI,
                "referrer": GROK_REFERRER,
                "response_type": "code",
                "scope": SCOPES,
                "state": state,
            }
        )
        auth_url = f"{OIDC_ISSUER}/oauth2/authorize?{params}"

        captured: dict[str, str] = {}
        httpd_holder: dict[str, Any] = {"srv": None}

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                try:
                    q = urllib.parse.urlparse(self.path).query
                    qs = urllib.parse.parse_qs(q)
                    if qs.get("code"):
                        captured["code"] = str(qs["code"][0])
                    body = b"<html><body>OK</body></html>"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception:
                    pass

            def log_message(self, fmt, *args):  # noqa: A003
                return

        def _serve() -> None:
            try:
                srv = HTTPServer(("127.0.0.1", 56121), _Handler)
                httpd_holder["srv"] = srv
                srv.handle_request()
            except Exception as exc:
                self.log(f"[build-web] local callback listen: {exc}")

        th = threading.Thread(target=_serve, daemon=True)
        th.start()
        self._sleep(0.3)

        self.log(f"[build-web] open authorize referrer={GROK_REFERRER}")
        try:
            self.page.get(auth_url)
        except Exception as exc:
            self.log(f"[build-web] navigate note: {exc}")
        self._sleep(2)

        def _scrape_page_code() -> str:
            raw = self._js(
                r"""
const body = (document.body && document.body.innerText) || '';
const looks = body.includes('输入此代码') || body.includes('请勿刷新此页面')
  || body.includes('将下面的代码') || body.includes('复制到 Grok Build')
  || body.replace(/\s+/g,'').includes('复制到GrokBuild')
  || body.toLowerCase().includes('enter this code')
  || body.toLowerCase().includes('copy the code below');
function isVisible(node) {
  if (!node) return false;
  const s = window.getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
const out = [];
const nodes = Array.from(document.querySelectorAll(
  'code, pre, input, textarea, span, div, p, h1, h2, h3, strong, b'
)).filter(isVisible);
for (const n of nodes) {
  let t = (n.value != null && n.value !== '' ? String(n.value) : (n.innerText || n.textContent || ''));
  t = t.replace(/\s+/g, '').trim();
  // real OAuth code sample ~50 chars, mixed alnum + hyphen
  if (t.length >= 40 && t.length <= 120 && /^[A-Za-z0-9_\-\.]+$/.test(t)) {
    if (!/http|accounts|authorize|callback|openid|offline|profile|identity|authentic/i.test(t)) {
      out.push(t);
    }
  }
}
// full body: prefer hyphen-containing long tokens (like LBsjN4eh...QyY9b9AAY)
const re = /[A-Za-z0-9_\-]{40,120}/g;
let m;
while ((m = re.exec(body)) !== null) {
  const t = m[0];
  if (/http|accounts|authorize|callback|openid|offline|profile|identity|authentic|makeauth/i.test(t)) continue;
  out.push(t);
}
const uniq = Array.from(new Set(out)).sort((a,b) => b.length - a.length);
return { looks: !!looks, codes: uniq.slice(0, 8), bodyPreview: body.replace(/\s+/g,' ').slice(0, 240) };
"""
            )
            if not isinstance(raw, dict):
                return ""
            if raw.get("looks"):
                self.log(f"[build-web] code-page body={str(raw.get('bodyPreview') or '')[:140]!r}")
            codes = raw.get("codes") or []
            if not isinstance(codes, list):
                return ""
            for c in codes:
                c = str(c or "").strip()
                if len(c) < 40 or len(c) > 120:
                    continue
                if not re.search(r"[A-Za-z]", c) or not re.search(r"[0-9]", c):
                    continue
                low = c.lower()
                if any(
                    x in low
                    for x in (
                        "make",
                        "authenti",
                        "confirm",
                        "identity",
                        "profile",
                        "readyour",
                        "verify",
                        "offline",
                        "openid",
                        "access",
                    )
                ):
                    continue
                return c
            return ""

        deadline = time.time() + float(self.cfg.get("build_browser_timeout") or 90)
        code = ""
        last_url = ""
        consent_clicks = 0
        while time.time() < deadline:
            if captured.get("code"):
                code = captured["code"]
                self.log("[build-web] got auth code from local callback :56121")
                break

            url = str(getattr(self.page, "url", "") or "")
            if url != last_url:
                self.log(f"[build-web] url={url[:140]}")
                last_url = url

            if "code=" in url:
                try:
                    q = urllib.parse.urlparse(url).query
                    code = str(urllib.parse.parse_qs(q).get("code", [""])[0] or "")
                except Exception:
                    code = ""
                if code:
                    self.log("[build-web] got auth code from redirect URL")
                    break

            body_hint = str(
                self._js("return ((document.body&&document.body.innerText)||'').slice(0,800)") or ""
            )
            # IMPORTANT: consent page also mentions "Grok Build" in permissions — do NOT use that alone
            on_code_page = (
                "输入此代码" in body_hint
                or "请勿刷新此页面" in body_hint
                or "将下面的代码" in body_hint
                or "复制到 Grok Build" in body_hint
                or "复制到GrokBuild" in body_hint.replace(" ", "")
                or "enter this code" in body_hint.lower()
                or "copy the code below" in body_hint.lower()
            )
            # only scrape when code-display page markers are present
            if on_code_page:
                page_code = _scrape_page_code()
                if page_code and len(page_code) >= 40:
                    code = page_code
                    self.log(
                        f"[build-web] got auth code from page len={len(code)} preview={code[:12]}..."
                    )
                    break
                self._sleep(0.6)
                continue

            if consent_clicks < 8 and (
                "/oauth2/consent" in url or "consent" in url.lower() or "authorize" in url.lower()
            ):
                # Prefer Drission text click for 允许 (most reliable on this page)
                clicked: Any = False
                for lab in ("允许", "授权", "Allow", "Authorize", "同意", "Accept"):
                    try:
                        btn = self.page.ele(f"text={lab}", timeout=0.6)
                        if not btn:
                            btn = self.page.ele(f"text:{lab}", timeout=0.4)
                        if btn:
                            try:
                                btn.click(by_js=True)
                            except Exception:
                                btn.click()
                            clicked = f"ele:{lab}"
                            break
                    except Exception:
                        continue
                if not clicked:
                    clicked = self._js(
                        r"""
function isVisible(node) {
  if (!node) return false;
  const s = window.getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
const body = (document.body && document.body.innerText) || '';
if (body.includes('输入此代码') || body.includes('请勿刷新此页面') || body.includes('将下面的代码')) {
  return 'code-page';
}
const btns = Array.from(document.querySelectorAll('button, [role="button"], a, input[type="submit"]'))
  .filter((n) => isVisible(n) && !n.disabled);
const labels = btns.map((n) => (n.innerText||n.textContent||'').replace(/\s+/g,'').trim()).filter(Boolean).slice(0,16);
const allow = btns.find((n) => {
  const t = [n.innerText, n.textContent, n.getAttribute('value'), n.getAttribute('aria-label')]
    .filter(Boolean).join(' ').replace(/\s+/g, '').trim();
  const low = t.toLowerCase();
  if (low.includes('cancel') || t.includes('取消') || low.includes('deny') || t.includes('拒绝')) return false;
  return t === '允许' || t === '授权' || t === '同意' || low === 'allow' || low === 'authorize'
    || low === 'accept' || t.includes('允许') || t.includes('授权') || low.includes('allow');
});
if (allow) { allow.click(); return 'allow:' + ((allow.innerText||'').replace(/\s+/g,'').slice(0,20)); }
const primary = btns.find((n) => {
  const cls = String(n.className||'');
  const t = (n.innerText||'').replace(/\s+/g,'').trim();
  if (!t || t.includes('拒绝') || t.includes('取消') || t.toLowerCase().includes('deny')) return false;
  return cls.includes('button-filled') || cls.includes('bg-button-filled') || n.type === 'submit';
});
if (primary) { primary.click(); return 'primary:' + (primary.innerText||'').replace(/\s+/g,'').slice(0,20); }
return 'no-btn:' + labels.join('|');
"""
                    )
                if clicked and clicked != "code-page":
                    consent_clicks += 1
                    self.log(f"[build-web] consent click={clicked} n={consent_clicks}")
                    if isinstance(clicked, str) and clicked.startswith("no-btn"):
                        self._sleep(1)
                        continue
                    self._sleep(2.0)
                    # wait for code page to render after allow
                    for _wait in range(12):
                        bh = str(
                            self._js(
                                "return ((document.body&&document.body.innerText)||'').slice(0,500)"
                            )
                            or ""
                        )
                        if (
                            "输入此代码" in bh
                            or "请勿刷新此页面" in bh
                            or "将下面的代码" in bh
                        ):
                            page_code = _scrape_page_code()
                            if page_code and len(page_code) >= 40:
                                code = page_code
                                self.log(
                                    f"[build-web] code right after consent len={len(code)}"
                                )
                                break
                        # also check local callback / URL
                        if captured.get("code"):
                            code = captured["code"]
                            break
                        u2 = str(getattr(self.page, "url", "") or "")
                        if "code=" in u2:
                            try:
                                q2 = urllib.parse.urlparse(u2).query
                                code = str(urllib.parse.parse_qs(q2).get("code", [""])[0] or "")
                            except Exception:
                                code = ""
                            if code:
                                break
                        self._sleep(0.5)
                    if code:
                        break
                    continue
            self._sleep(0.7)

        try:
            srv = httpd_holder.get("srv")
            if srv is not None:
                srv.server_close()
        except Exception:
            pass

        if not code:
            self.dump_diagnostics("build_web_no_code")
            return {"ok": False, "code": "build_web_no_code", "error": "no auth code from browser authorize"}

        from curl_cffi import requests as creq

        proxy = str(self.cfg.get("mint_proxy") or self.cfg.get("proxy") or "").strip()
        token_data = urllib.parse.urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "code_verifier": verifier,
            }
        )
        self.log(f"[build-web] exchange code len={len(code)}")
        try:
            kw: dict[str, Any] = {
                "data": token_data,
                "headers": {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": GROK_TOKEN_UA,
                    "X-Grok-Client-Version": GROK_VERSION,
                    "Accept": "*/*",
                },
                "impersonate": "chrome",
                "timeout": 20,
            }
            if proxy:
                kw["proxy"] = proxy
            r = creq.post(f"{OIDC_ISSUER}/oauth2/token", **kw)
        except Exception as exc:
            return {"ok": False, "code": "build_web_token_net", "error": str(exc)[:300]}
        if r.status_code < 200 or r.status_code >= 300:
            self.log(f"[build-web] token HTTP {r.status_code}: {(r.text or '')[:200]}")
            return {
                "ok": False,
                "code": "build_web_token_http",
                "status": r.status_code,
                "error": (r.text or "")[:300],
                "auth_code_len": len(code),
            }
        try:
            token = r.json()
        except Exception:
            return {"ok": False, "code": "build_web_token_json", "error": (r.text or "")[:200]}
        if not token.get("access_token"):
            return {"ok": False, "code": "build_web_no_access", "error": str(token)[:200]}
        ap = decode_jwt_payload(token["access_token"])
        ref = ap.get("referrer")
        self.log(
            f"[build-web] token ok referrer={ref!r} bot_flag={ap.get('bot_flag_source')} "
            f"scope={ap.get('scope')!r}"
        )
        return {
            "ok": True,
            "code": "build_web_ok",
            "token": token,
            "bot_flag": ap.get("bot_flag_source"),
            "referrer": ref,
            "method": "browser_authorize",
        }

    def register_one(self) -> dict[str, Any]:
        """signup → sso → web UI chat → (optional) browser Build authorize."""
        if self._page is None:
            self.start()
        max_mail = max(1, int(self.cfg.get("register_max_attempts") or self.cfg.get("mail_retry_count") or 3))
        last_err: Exception | None = None
        for attempt in range(1, max_mail + 1):
            try:
                self.log(f"[reg] attempt {attempt}/{max_mail} engine=drission-sample")
                email, token = self._mail.create_address()
                self.open_signup()
                self.fill_email(email)
                mail_timeout = float(self.cfg.get("mail_timeout") or self.cfg.get("tempmail_timeout") or 150)
                half = max(30.0, mail_timeout / 2)
                try:
                    code = self._mail.wait_code(token, timeout=half)
                except TempMailError:
                    self.log("[reg] resend code + continue wait")
                    self._resend_code()
                    code = self._mail.wait_code(token, timeout=max(30.0, mail_timeout - half))
                self.log(f"[reg] code={code}")
                self.fill_code(code)
                profile = self.fill_profile()
                sso = self.wait_sso()
                try:
                    self.page.get("https://grok.com/")
                    self._sleep(3)
                except Exception as exc:
                    self.log(f"[reg] open grok.com: {exc}")
                cf_clearance, browser_ua = self.extract_cf_clearance_and_ua()
                if cf_clearance:
                    self.log(f"[reg] cf_clearance len={len(cf_clearance)}")
                try:
                    self._browser_activate_build()
                except Exception as exc:
                    self.log(f"[reg] browser activate: {exc}")
                web = None
                if not bool(self.cfg.get("post_register_cloak", False)):
                    web = self.browser_web_default_chat()
                else:
                    self.log("[reg] skip Drission web probe (post_register_cloak=true)")
                out: dict[str, Any] = {
                    "email": email,
                    "password": profile.get("password") or "",
                    "sso": sso,
                    "profile": profile,
                    "cf_clearance": cf_clearance,
                    "user_agent": browser_ua,
                }
                if web is not None:
                    out["web"] = web
                # after web success: browser Build authorize (same session)
                if (
                    isinstance(web, dict)
                    and web.get("ok")
                    and bool(self.cfg.get("browser_build_authorize", True))
                ):
                    wait_sec = float(self.cfg.get("build_authorize_delay_sec") or 3)
                    if wait_sec > 0:
                        self.log(f"[reg] web ok, wait {wait_sec:.1f}s then browser Build authorize")
                        self._sleep(wait_sec)
                    try:
                        out["build_web"] = self.browser_build_authorize()
                    except Exception as exc:
                        self.log(f"[reg] browser Build authorize fail: {exc}")
                        out["build_web"] = {"ok": False, "code": "build_web_err", "error": str(exc)[:300]}
                return out
            except (TempMailError, BrowserRegisterError) as exc:
                last_err = exc
                self.log(f"[reg] attempt failed: {exc}")
                try:
                    self.dump_diagnostics(f"fail_{type(exc).__name__}")
                except Exception:
                    pass
                try:
                    self.restart()
                except Exception as rexc:
                    self.log(f"[reg] restart failed: {rexc}")
            except Exception as exc:
                last_err = exc
                self.log(f"[reg] attempt error: {exc}")
                try:
                    self.dump_diagnostics("fail_exception")
                except Exception:
                    pass
                try:
                    self.restart()
                except Exception:
                    pass
        raise BrowserRegisterError(f"register failed after retries: {last_err}")
