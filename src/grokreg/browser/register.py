from __future__ import annotations

import random
import string
import time
from pathlib import Path
from typing import Any

from grokreg.mail.tempmail import TempMailClient, TempMailError
from grokreg.util.log import LogFn, default_log

SIGNUP_URL = "https://accounts.x.ai/sign-up"


def _rand_name() -> tuple[str, str]:
    first = random.choice(
        ["Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Avery", "Quinn"]
    )
    last = random.choice(
        ["Smith", "Johnson", "Brown", "Davis", "Wilson", "Moore", "Taylor", "Anderson"]
    )
    return first, last


def _rand_password(n: int = 14) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%"
    pwd = [
        random.choice(string.ascii_uppercase),
        random.choice(string.ascii_lowercase),
        random.choice(string.digits),
        random.choice("!@#$%"),
    ]
    pwd += [random.choice(chars) for _ in range(max(0, n - 4))]
    random.shuffle(pwd)
    return "".join(pwd)


def _rand_birthday() -> tuple[str, str, str]:
    year = str(random.randint(1986, 2000))
    month = str(random.randint(1, 12))
    day = str(random.randint(1, 28))
    return month, day, year


class BrowserRegisterError(RuntimeError):
    pass


class BrowserRegistrar:
    """DrissionPage-based accounts.x.ai signup (aligned with sample grok_register_ttk)."""

    def __init__(self, cfg: dict[str, Any], log: LogFn | None = None) -> None:
        self.cfg = cfg
        self.log = log or default_log
        self._browser = None
        self._page = None
        self._mail = TempMailClient(
            base_url=str(cfg.get("tempmail_base_url") or "https://mail.minecraft-cn.net"),
            domains=list(cfg.get("tempmail_domains") or []),
            proxy=str(cfg.get("proxy") or ""),
            poll_interval=float(cfg.get("tempmail_poll_interval") or cfg.get("mail_poll_interval") or 2),
            timeout=float(cfg.get("tempmail_timeout") or cfg.get("mail_timeout") or 150),
            list_limit=int(cfg.get("tempmail_list_limit") or 30),
            log=self.log,
        )

    def start(self) -> None:
        from DrissionPage import Chromium, ChromiumOptions

        # Optional: attach existing fingerprint browser via CDP (BitBrowser / AdsPower / etc.)
        # config: "fingerprint_cdp": "127.0.0.1:9222"  or full "http://127.0.0.1:9222"
        cdp = str(self.cfg.get("fingerprint_cdp") or self.cfg.get("cdp_url") or "").strip()
        if cdp:
            self._start_fingerprint_cdp(cdp)
            return

        co = ChromiumOptions()
        try:
            co.auto_port()
        except Exception:
            pass
        headless = bool(self.cfg.get("headless", False))
        if headless:
            # CF/Turnstile is usually blocked in true headless; prefer headed
            try:
                co.headless(True)
            except Exception:
                co.set_argument("--headless=new")
        browser_path = str(self.cfg.get("browser_path") or "").strip()
        if browser_path:
            co.set_browser_path(browser_path)
        proxy = str(self.cfg.get("proxy") or "").strip()
        if proxy:
            # Chromium --proxy-server cannot embed user:pass; strip auth if present
            try:
                from urllib.parse import urlparse

                u = urlparse(proxy if "://" in proxy else f"http://{proxy}")
                host = u.hostname or ""
                if host:
                    port = u.port or (443 if (u.scheme or "http") == "https" else 80)
                    scheme = u.scheme or "http"
                    co.set_argument(f"--proxy-server={scheme}://{host}:{port}")
                else:
                    co.set_proxy(proxy)
            except Exception:
                co.set_proxy(proxy)
        ua = str(
            self.cfg.get("user_agent")
            or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        ).strip()
        if ua:
            co.set_user_agent(ua)

        ext = Path(__file__).resolve().parent / "turnstilePatch"
        if ext.is_dir() and (ext / "manifest.json").is_file():
            try:
                co.add_extension(str(ext))
                self.log("[browser] loaded turnstilePatch extension")
            except Exception as exc:
                self.log(f"[browser] extension load skip: {exc}")

        try:
            co.set_timeouts(base=1)
        except Exception:
            pass
        # sample CHROMIUM_SLIM_FLAGS + anti-detect (fingerprint-like)
        for flag in (
            "--disable-gpu",
            "--disable-software-rasterizer",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--mute-audio",
            "--disable-background-networking",
            "--no-first-run",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--disable-features=IsolateOrigins,site-per-process,AutomationControlled",
            "--lang=en-US",
            "--window-size=1280,900",
        ):
            try:
                co.set_argument(flag)
            except Exception:
                pass
        # Prefer non-automation binary prefs when supported
        for meth, arg in (
            ("set_pref", ("excludeSwitches", ["enable-automation"])),
            ("set_pref", ("useAutomationExtension", False)),
        ):
            try:
                getattr(co, meth)(*arg)
            except Exception:
                pass

        self.log(f"[browser] start headless={headless} proxy={bool(proxy)} fingerprint=local-stealth")
        self._browser = Chromium(co)
        self._page = self._browser.latest_tab
        self._inject_stealth()

    def _start_fingerprint_cdp(self, cdp: str) -> None:
        """Connect to an already-running fingerprint browser (BitBrowser/AdsPower/Chrome debug)."""
        from DrissionPage import Chromium

        addr = cdp.replace("http://", "").replace("https://", "").strip().rstrip("/")
        self.log(f"[browser] attach fingerprint CDP {addr}")
        # DrissionPage: Chromium(addr) or set_address
        try:
            self._browser = Chromium(addr)
        except TypeError:
            from DrissionPage import ChromiumOptions

            co = ChromiumOptions()
            co.set_address(addr)
            self._browser = Chromium(co)
        self._page = self._browser.latest_tab
        self._inject_stealth()

    def _inject_stealth(self) -> None:
        """CDP stealth + sample-like anti-detect (fingerprint environment)."""
        page = self._page
        if page is None:
            return
        stealth_js = r"""
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
try {
  window.chrome = window.chrome || { runtime: {} };
} catch(e) {}
try {
  Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
} catch(e) {}
try {
  Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
} catch(e) {}
try {
  const originalQuery = window.navigator.permissions.query;
  window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters)
  );
} catch(e) {}
"""
        # Prefer CDP addScriptToEvaluateOnNewDocument for every navigation
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

    def stop(self) -> None:
        try:
            if self._browser is not None:
                self._browser.quit()
        except Exception:
            pass
        self._browser = None
        self._page = None

    def restart(self) -> None:
        self.stop()
        time.sleep(1)
        self.start()

    @property
    def page(self):
        if self._page is None:
            raise BrowserRegisterError("browser not started")
        return self._page

    def _sleep(self, sec: float) -> None:
        time.sleep(sec)

    def _js(self, script: str, *args: Any) -> Any:
        """Run page JS; support arguments[0] style like sample (DrissionPage)."""
        import json

        try:
            if args:
                return self.page.run_js(script, *args)
            return self.page.run_js(script)
        except TypeError:
            # Fallback: inject args array so `arguments[i]` works in script body
            if not args:
                return self.page.run_js(script)
            payload = json.dumps(list(args), ensure_ascii=False)
            body = script.strip()
            wrapped = f"(function(){{ var arguments = {payload}; {body} }})()"
            return self.page.run_js(wrapped)
        except Exception as exc:
            self.log(f"[reg] js error: {exc}")
            return None

    def open_signup(self) -> None:
        self.log("[reg] open signup")
        self.page.get(SIGNUP_URL)
        try:
            self.page.wait.doc_loaded()
        except Exception:
            pass
        self._sleep(2)
        self.log(f"[reg] url={getattr(self.page, 'url', '')}")
        self._click_email_signup()

    def _click_email_signup(self) -> None:
        # sample click_email_signup_button — broad match including bare "email"
        deadline = time.time() + float(self.cfg.get("nav_email_button_timeout") or 15)
        while time.time() < deadline:
            # already on email form?
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
                return
            clicked = self._js(
                r"""
const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]'));
const target = candidates.find((node) => {
    const text = (node.innerText || node.textContent || '').replace(/\s+/g, '');
    const lower = text.toLowerCase();
    return (
        text.includes('使用邮箱注册') ||
        text.includes('使用邮箱') ||
        lower.includes('signupwithemail') ||
        lower.includes('continuewithemail') ||
        lower.includes('email')
    );
});
if (!target) return false;
target.click();
return true;
"""
            )
            if clicked:
                self.log("[reg] clicked email signup")
                self._sleep(2)
                return
            self._sleep(1)
        self.log("[reg] email signup button not found (maybe already on form)")

    def fill_email(self, email: str) -> None:
        """JS fill aligned with sample fill_email_and_submit (+ DrissionPage ele fallback)."""
        deadline = time.time() + float(self.cfg.get("email_form_timeout") or 30)
        last_status = ""
        while time.time() < deadline:
            # Prefer sample-style JS with explicit string embed (avoid arguments[] issues)
            import json as _json

            email_js = _json.dumps(email)
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
const input = Array.from(document.querySelectorAll(
  'input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[type="text"]'
)).find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
if (!input) return 'not-ready';
input.focus(); input.click();
const valueSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
const tracker = input._valueTracker;
if (tracker) tracker.setValue('');
if (valueSetter) valueSetter.call(input, email); else input.value = email;
input.dispatchEvent(new Event('focus', {{ bubbles: true }}));
input.dispatchEvent(new InputEvent('input', {{ bubbles: true, data: email, inputType: 'insertText' }}));
input.dispatchEvent(new Event('change', {{ bubbles: true }}));
input.dispatchEvent(new Event('blur', {{ bubbles: true }}));
if ((input.value || '').trim() === email) return 'filled';
return 'value=' + (input.value || '');
"""
            )
            last_status = str(filled)
            if filled == "not-ready":
                # re-click email path if form not shown
                if time.time() + 5 < deadline:
                    self._click_email_signup()
                self._sleep(0.6)
                continue
            if filled != "filled":
                # DrissionPage ele() fallback
                try:
                    for sel in (
                        'css:input[type=email]',
                        'css:input[name=email]',
                        'css:input[data-testid=email]',
                        'css:input[autocomplete=email]',
                    ):
                        el = self.page.ele(sel, timeout=1)
                        if el:
                            try:
                                el.clear()
                            except Exception:
                                pass
                            el.input(email)
                            filled = "filled"
                            break
                except Exception as exc:
                    self.log(f"[reg] ele fill fallback: {exc}")
                if filled != "filled":
                    self.log(f"[reg] email fill result={filled!r}")
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
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button')).filter(
  (node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true'
);
const submitButton = buttons.find((node) => {
    const text = (node.innerText || node.textContent || '').replace(/\s+/g, '');
    const lower = text.toLowerCase();
    return (
        text === '注册' || text.includes('注册') ||
        lower.includes('sign up') || lower.includes('signup') ||
        lower.includes('continue') || lower.includes('next') || lower.includes('submit')
    );
}) || buttons.find((node) => (node.getAttribute('type') || '') === 'submit');
if (!submitButton) return false;
submitButton.click();
return true;
"""
            )
            if clicked:
                self.log(f"[reg] email submitted: {email}")
                self._sleep(2)
                return
            # fallback: Enter key
            try:
                self.page.actions.key_down("ENTER").key_up("ENTER")
                self.log(f"[reg] email submitted via Enter: {email}")
                self._sleep(2)
                return
            except Exception:
                pass
            self._sleep(0.5)
        # debug dump
        try:
            snippet = self._js(
                "return (document.body && document.body.innerText || '').slice(0, 400)"
            )
            self.log(f"[reg] page text: {snippet!r}")
        except Exception:
            pass
        raise BrowserRegisterError(f"email input/submit not found (last={last_status})")

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
        """Fill ABC-DEF / digit OTP (sample fill_code_and_submit JS)."""
        clean = str(code).replace("-", "").strip()
        display = str(code).strip()
        deadline = time.time() + float(self.cfg.get("code_form_timeout") or 60)
        while time.time() < deadline:
            filled = self._js(
                r"""
const raw = String(arguments[0] || '').trim();
const code = String(arguments[1] || raw).replace(/-/g, '').trim();
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

// single aggregate input (may accept ABC-DEF with hyphen)
const aggregate = Array.from(document.querySelectorAll(
  'input[data-input-otp="true"], input[name="code"], input[autocomplete="one-time-code"], input[inputmode="numeric"], input[inputmode="text"], input[type="text"]'
)).find((node) => isVisible(node) && !node.disabled && !node.readOnly && Number(node.maxLength || 20) > 1);

if (aggregate) {
    aggregate.focus();
    aggregate.click();
    // try with hyphen first (xAI), then without
    setInputValue(aggregate, raw.includes('-') ? raw : code);
    if (String(aggregate.value || '').replace(/\s+/g, '')) return 'filled-aggregate';
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
        box.focus();
        box.click();
        setInputValue(box, ch);
        box.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: ch }));
        box.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: ch }));
    }
    const merged = otpBoxes.slice(0, code.length).map((x) => String(x.value || '').trim()).join('');
    return merged.length ? 'filled-boxes' : 'boxes-failed';
}
return 'not-ready';
""",
                display,
                clean,
            )
            if filled == "not-ready":
                self._sleep(0.5)
                continue
            if filled and "failed" in str(filled):
                self.log(f"[reg] code fill failed: {filled}")
                self._sleep(0.5)
                continue
            if filled and str(filled).startswith("filled"):
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
        t.includes('confirm') || t.includes('continue') || t.includes('next') || t.includes('verify')
    );
});
if (!btn) return 'no-button';
btn.focus();
btn.click();
return 'clicked';
"""
                )
                self.log(f"[reg] code submitted: {display} click={clicked}")
                self._sleep(1.5)
                return
            self._sleep(0.5)
        raise BrowserRegisterError("code fill/submit failed")

    def fill_profile(self) -> dict[str, str]:
        """Sample fill_profile_and_submit: fill names/password, wait CF, then submit."""
        import json as _json

        first, last = _rand_name()
        password = "N" + "".join(random.choices(string.hexdigits.lower(), k=8)) + "!a7#" + _rand_password(8)
        deadline = time.time() + float(self.cfg.get("profile_timeout") or 180)
        form_filled = False
        wait_cf_since = 0.0
        # Prefer solving Turnstile (click red-box checkbox) before force-submit
        cf_force_after = float(self.cfg.get("turnstile_force_submit_sec") or 55)
        self.log("[reg] preheat turnstile…")
        self._sleep(1.5)
        # actively click checkbox while form loads
        try:
            self._click_turnstile_checkbox()
        except Exception:
            pass

        while time.time() < deadline:
            force_cf = bool(wait_cf_since and (time.time() - wait_cf_since) >= cf_force_after)
            if not form_filled:
                g, f, p = _json.dumps(first), _json.dumps(last), _json.dumps(password)
                filled = self._js(
                    f"""
const givenName = {g};
const familyName = {f};
const password = {p};
const forceCf = {str(force_cf).lower()};
function isVisible(node) {{
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}}
function pickInput(selector) {{
    return Array.from(document.querySelectorAll(selector)).find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
}}
function setInputValue(input, value) {{
    if (!input) return false;
    input.focus(); input.click();
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    const tracker = input._valueTracker;
    if (tracker) tracker.setValue('');
    if (nativeSetter) nativeSetter.call(input, value); else input.value = value;
    input.dispatchEvent(new InputEvent('input', {{ bubbles: true, data: value, inputType: 'insertText' }}));
    input.dispatchEvent(new Event('change', {{ bubbles: true }}));
    input.blur();
    return String(input.value || '').trim() === String(value || '').trim();
}}
const givenInput = pickInput('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"], input[name="given_name"]');
const familyInput = pickInput('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"], input[name="family_name"]');
const passwordInput = pickInput('input[data-testid="password"], input[name="password"], input[type="password"], input[autocomplete="new-password"]');
if (!givenInput || !familyInput || !passwordInput) return 'not-ready';
const ok1 = setInputValue(givenInput, givenName);
const ok2 = setInputValue(familyInput, familyName);
const ok3 = setInputValue(passwordInput, password);
if (!ok1 || !ok2 || !ok3) return 'fill-failed';
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]');
if (cfPresent && !forceCf) {{
  const token = String((cfInput && cfInput.value) || '').trim();
  if (token.length < 80) return 'wait-cloudflare:' + token.length;
}}
return forceCf ? 'force-submit' : 'ready-to-submit';
"""
                )
                self.log(f"[reg] profile fill state={filled}")
                if filled == "not-ready" or filled == "fill-failed":
                    self._sleep(0.6)
                    continue
                if isinstance(filled, str) and filled.startswith("wait-cloudflare"):
                    form_filled = True
                    if not wait_cf_since:
                        wait_cf_since = time.time()
                    # sample: keep clicking red-box + inject token when ready
                    tok = self.get_turnstile_token(rounds=6)
                    if tok:
                        tjs = _json.dumps(tok)
                        self._js(
                            f"""
const token = {tjs};
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!cfInput || !token) return false;
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(cfInput, token); else cfInput.value = token;
cfInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
cfInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
return String(cfInput.value || '').trim().length;
"""
                        )
                    else:
                        self._click_turnstile_checkbox()
                    self._sleep(0.8)
                    continue
                if filled in ("ready-to-submit", "filled-no-submit", "force-submit"):
                    form_filled = True

            force_cf = bool(wait_cf_since and (time.time() - wait_cf_since) >= cf_force_after)
            # if CF still empty, try solve before force
            if not force_cf:
                tok = self.get_turnstile_token(rounds=3)
                if tok:
                    tjs = _json.dumps(tok)
                    self._js(
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
            submit_state = self._js(
                f"""
const forceCf = {str(force_cf).lower()};
function isVisible(node) {{
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}}
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput
  || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]');
if (cfPresent && !forceCf) {{
    const token = String((cfInput && cfInput.value) || '').trim();
    if (token.length < 80) return 'wait-cloudflare:' + token.length;
}}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button')).filter((node) => {{
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
}});
const submitBtn = buttons.find((node) => {{
    const t = (node.innerText || node.textContent || '').replace(/\\s+/g, '').toLowerCase();
    return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') ||
           t.includes('sign up') || t.includes('createaccount') || t.includes('create') || t.includes('注册');
}});
if (!submitBtn) return 'no-submit-button';
submitBtn.focus();
submitBtn.click();
return forceCf ? 'submitted-force' : 'submitted';
"""
            )
            self.log(f"[reg] profile submit={submit_state}")
            if isinstance(submit_state, str) and submit_state.startswith("wait-cloudflare"):
                if not wait_cf_since:
                    wait_cf_since = time.time()
                self._sleep(1)
                continue
            if submit_state in ("submitted", "submitted-force"):
                self._sleep(2)
                return {"given_name": first, "family_name": last, "password": password}
            self._sleep(0.6)
        raise BrowserRegisterError("profile fill/submit failed (turnstile or form)")

    def wait_sso(self, timeout: float | None = None) -> str:
        timeout = float(
            timeout
            if timeout is not None
            else self.cfg.get("sso_timeout") or self.cfg.get("sso_timeout_base") or 240
        )
        deadline = time.time() + timeout
        last_submit = 0.0
        first_final = 0.0
        cf_force_after = float(self.cfg.get("turnstile_force_submit_sec") or 25)
        self.log("[reg] wait sso cookie")
        while time.time() < deadline:
            now = time.time()
            # sample: if still on final signup page, re-click submit after CF
            if now - last_submit >= 2.5:
                force_cf = bool(first_final and (now - first_final) >= cf_force_after)
                retried = self._js(
                    f"""
const forceCf = {str(force_cf).lower()};
function isVisible(node) {{
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}}
const titleHit = !!Array.from(document.querySelectorAll('h1,h2,div,span,button')).find((el) => {{
    const t = (el.textContent || '').replace(/\\s+/g, '');
    const lower = t.toLowerCase();
    return t.includes('完成注册') || lower.includes('complete') || lower.includes('create your') ||
           lower.includes('sign up') || t.includes('注册');
}});
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput
  || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]');
if (cfPresent && !forceCf) {{
    const token = String((cfInput && cfInput.value) || '').trim();
    if (token.length < 80) return 'final-page-wait-cf:' + token.length;
}}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button')).filter((node) => {{
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
}});
const submitBtn = buttons.find((node) => {{
    const t = (node.innerText || node.textContent || '').replace(/\\s+/g, '').toLowerCase();
    return t.includes('完成注册') || t.includes('创建账户') || t.includes('sign up') ||
           t.includes('signup') || t.includes('createaccount') || t.includes('create') || t.includes('注册');
}}) || buttons[0];
if (!submitBtn) return titleHit ? 'final-page-no-submit' : 'not-final-page';
submitBtn.focus();
submitBtn.click();
return forceCf ? 'final-page-force-click' : 'final-page-clicked-submit';
"""
                )
                last_submit = now
                if isinstance(retried, str) and retried.startswith("final-page-wait-cf"):
                    if not first_final:
                        first_final = now
                    # try shadow-root turnstile click / token inject
                    tok = self.get_turnstile_token(rounds=6)
                    if tok:
                        import json as _json2
                        tjs = _json2.dumps(tok)
                        self._js(
                            f"""
const token = {tjs};
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!cfInput || !token) return false;
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(cfInput, token); else cfInput.value = token;
cfInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
cfInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
return String(cfInput.value || '').trim().length;
"""
                        )
                if retried and retried != "not-final-page":
                    self.log(f"[reg] final page: {retried}")

            try:
                cookies = self.page.cookies()
                try:
                    # sample uses all_domains when available
                    cookies = self.page.cookies(all_domains=True) or cookies
                except Exception:
                    pass
            except Exception:
                cookies = []
            for c in cookies or []:
                try:
                    if isinstance(c, dict):
                        name = c.get("name") or c.get("Name")
                        val = c.get("value") or c.get("Value")
                    else:
                        name = getattr(c, "name", None)
                        val = getattr(c, "value", None)
                    if str(name).lower() == "sso" and val:
                        sso = str(val).strip()
                        if len(sso) > 20:
                            self.log("[reg] sso obtained")
                            return sso
                except Exception:
                    continue
            try:
                js_sso = self._js(
                    "return (document.cookie || '').split(';').map(s=>s.trim()).find(s=>s.startsWith('sso='))"
                )
                if js_sso and str(js_sso).startswith("sso="):
                    sso = str(js_sso)[4:].strip()
                    if sso:
                        self.log("[reg] sso obtained (js)")
                        return sso
            except Exception:
                pass
            self._handle_turnstile_hint()
            self._sleep(1.2)
        raise BrowserRegisterError("sso timeout")

    def _handle_turnstile_hint(self) -> None:
        try:
            iframes = self.page.eles("tag:iframe", timeout=0.5) or []
            for fr in iframes:
                src = (fr.attr("src") or "") if hasattr(fr, "attr") else ""
                if "turnstile" in src or "cloudflare" in src:
                    self.log("[reg] turnstile iframe present")
                    break
        except Exception:
            pass

    def _click_turnstile_checkbox(self) -> bool:
        """Click the red-box checkbox inside CF Turnstile (sample getTurnstileToken path).

        DOM path (from sample / grokRegister-cpa):
          input[name=cf-turnstile-response]
            -> parent
            -> shadow_root iframe
            -> body.shadow_root input[type=checkbox]
        """
        page = self.page
        clicked = False

        # Path A: sample — @name=cf-turnstile-response → parent.shadow_root → iframe → body.shadow_root → input
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
                    # click checkbox in nested shadow root (the red-box target)
                    for getter in (
                        lambda: iframe.ele("tag:body", timeout=0.5).shadow_root.ele("tag:input", timeout=0.5),
                        lambda: iframe.ele("tag:body", timeout=0.5).shadow_root.ele("css:input[type=checkbox]", timeout=0.5),
                        lambda: iframe.ele("tag:body", timeout=0.5).shadow_root.ele("css:.mark", timeout=0.5),
                        lambda: iframe.ele("tag:body", timeout=0.5).shadow_root.ele("css:label", timeout=0.5),
                    ):
                        try:
                            btn = getter()
                            if btn is not None:
                                try:
                                    btn.click(by_js=False)
                                except Exception:
                                    btn.click()
                                self.log("[reg] clicked turnstile checkbox (shadow)")
                                clicked = True
                                break
                        except Exception:
                            continue
            except Exception as exc:
                self.log(f"[reg] turnstile shadow path: {exc}")

        # Path B: any challenges.cloudflare.com iframe → body shadow checkbox
        if not clicked:
            try:
                frames = page.eles("tag:iframe", timeout=0.5) or []
            except Exception:
                frames = []
            for fr in frames:
                try:
                    src = ""
                    try:
                        src = fr.attr("src") or ""
                    except Exception:
                        pass
                    if "challenges.cloudflare.com" not in src and "turnstile" not in src.lower():
                        continue
                    try:
                        fr.run_js(
                            """
window.dtp = 1;
function getRandomInt(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }
Object.defineProperty(MouseEvent.prototype, 'screenX', { value: getRandomInt(800,1200) });
Object.defineProperty(MouseEvent.prototype, 'screenY', { value: getRandomInt(400,700) });
"""
                        )
                    except Exception:
                        pass
                    try:
                        body = fr.ele("tag:body", timeout=0.5)
                        sr = body.shadow_root
                        for sel in ("tag:input", "css:input[type=checkbox]", "css:.mark", "css:label"):
                            try:
                                el = sr.ele(sel, timeout=0.3)
                            except Exception:
                                el = None
                            if el is not None:
                                try:
                                    el.click(by_js=False)
                                except Exception:
                                    el.click()
                                self.log("[reg] clicked turnstile checkbox (iframe)")
                                clicked = True
                                break
                    except Exception:
                        continue
                    if clicked:
                        break
                except Exception:
                    continue

        # Path C: JS click on turnstile widgets (fallback, not the real checkbox)
        if not clicked:
            try:
                self._js(
                    r"""
const nodes = Array.from(document.querySelectorAll('iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey]'));
for (const n of nodes) {
  try {
    n.scrollIntoView({block:'center', inline:'center'});
    const r = n.getBoundingClientRect();
    // click center of widget (checkbox is usually left side of widget)
    const x = r.left + Math.min(30, r.width * 0.15);
    const y = r.top + r.height / 2;
    for (const type of ['mousemove','mousedown','mouseup','click']) {
      n.dispatchEvent(new MouseEvent(type, {bubbles:true, clientX:x, clientY:y, view:window}));
    }
    if (typeof n.click === 'function') n.click();
  } catch(e) {}
}
return nodes.length;
"""
                )
                self.log("[reg] clicked turnstile widget (js fallback)")
                clicked = True
            except Exception:
                pass
        return clicked

    def get_turnstile_token(self, rounds: int = 25) -> str:
        """Sample getTurnstileToken: click red-box checkbox until token appears."""
        try:
            self._js(
                "try { if (window.turnstile && typeof turnstile.reset === 'function') turnstile.reset(); } catch(e) {}"
            )
        except Exception:
            pass

        for i in range(rounds):
            # 1) already solved?
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
            except Exception as exc:
                if i == 0:
                    self.log(f"[reg] turnstile read: {exc}")

            # 2) click the checkbox (red box)
            try:
                self._click_turnstile_checkbox()
            except Exception as exc:
                if i == 0:
                    self.log(f"[reg] turnstile click: {exc}")

            # 3) small human-like delay
            self._sleep(0.8 + (0.15 * (i % 3)))
        return ""

    def wait_turnstile(self, timeout: float | None = None) -> bool:
        """Block until CF token ready (sample _wait_turnstile)."""
        timeout = float(timeout if timeout is not None else self.cfg.get("turnstile_stuck_timeout") or 90)
        deadline = time.time() + timeout
        while time.time() < deadline:
            tok = self.get_turnstile_token(rounds=3)
            if tok:
                return True
            self._sleep(0.5)
        self.log("[reg] turnstile wait timeout")
        return False

    def register_one(self) -> dict[str, Any]:
        """Full register once. Returns email/password/sso/profile.

        Order matches sample register_cli:
          create mailbox → open signup → fill email (when form ready) → wait code → profile → sso
        """
        if self._page is None:
            self.start()
        max_mail = max(1, int(self.cfg.get("register_max_attempts") or self.cfg.get("mail_retry_count") or 3))
        last_err: Exception | None = None
        for attempt in range(1, max_mail + 1):
            try:
                self.log(f"[reg] attempt {attempt}/{max_mail}")
                # create mailbox first so form fill is not delayed by API
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
                    code = self._mail.wait_code(token, timeout=mail_timeout - half)
                self.log(f"[reg] code={code}")
                self.fill_code(code)
                profile = self.fill_profile()
                sso = self.wait_sso()
                return {
                    "email": email,
                    "password": profile.get("password") or "",
                    "sso": sso,
                    "profile": profile,
                }
            except (TempMailError, BrowserRegisterError) as exc:
                last_err = exc
                self.log(f"[reg] attempt failed: {exc}")
                try:
                    self.restart()
                except Exception as rexc:
                    self.log(f"[reg] restart failed: {rexc}")
            except Exception as exc:
                last_err = exc
                self.log(f"[reg] attempt error: {exc}")
                try:
                    self.restart()
                except Exception:
                    pass
        raise BrowserRegisterError(f"register failed after retries: {last_err}")
