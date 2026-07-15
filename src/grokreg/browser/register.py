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

        co = ChromiumOptions()
        try:
            co.auto_port()
        except Exception:
            pass
        headless = bool(self.cfg.get("headless", False))
        if headless:
            co.headless(True)
        browser_path = str(self.cfg.get("browser_path") or "").strip()
        if browser_path:
            co.set_browser_path(browser_path)
        proxy = str(self.cfg.get("proxy") or "").strip()
        if proxy:
            co.set_proxy(proxy)
        ua = str(self.cfg.get("user_agent") or "").strip()
        if ua:
            co.set_user_agent(ua)

        ext = Path(__file__).resolve().parent / "turnstilePatch"
        if ext.is_dir() and (ext / "manifest.json").is_file():
            try:
                co.add_extension(str(ext))
                self.log("[browser] loaded turnstilePatch extension")
            except Exception as exc:
                self.log(f"[browser] extension load skip: {exc}")

        for flag in (
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
            "--lang=en-US",
        ):
            try:
                co.set_argument(flag)
            except Exception:
                pass

        self.log(f"[browser] start headless={headless} proxy={bool(proxy)}")
        self._browser = Chromium(co)
        self._page = self._browser.latest_tab

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
        deadline = time.time() + float(self.cfg.get("nav_email_button_timeout") or 12)
        while time.time() < deadline:
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
        (lower.includes('email') && (lower.includes('sign') || lower.includes('continue') || lower.includes('register')))
    );
});
if (!target) return false;
target.click();
return true;
"""
            )
            if clicked:
                self.log("[reg] clicked email signup")
                self._sleep(1.5)
                return
            self._sleep(0.8)
        # soft-fail: maybe already on email form
        self.log("[reg] email signup button not found (maybe already on form)")

    def fill_email(self, email: str) -> None:
        """JS fill aligned with sample fill_email_and_submit."""
        deadline = time.time() + float(self.cfg.get("email_form_timeout") or 20)
        while time.time() < deadline:
            filled = self._js(
                r"""
const email = arguments[0];
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
const input = Array.from(document.querySelectorAll(
  'input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"]'
)).find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
if (!input) return 'not-ready';
input.focus(); input.click();
const valueSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
const tracker = input._valueTracker;
if (tracker) tracker.setValue('');
if (valueSetter) valueSetter.call(input, email); else input.value = email;
input.dispatchEvent(new Event('focus', { bubbles: true }));
input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: email, inputType: 'insertText' }));
input.dispatchEvent(new InputEvent('input', { bubbles: true, data: email, inputType: 'insertText' }));
input.dispatchEvent(new Event('change', { bubbles: true }));
input.dispatchEvent(new Event('blur', { bubbles: true }));
if ((input.value || '').trim() === email) return 'filled';
return input.value;
""",
                email,
            )
            if filled == "not-ready":
                self._sleep(0.5)
                continue
            if filled != "filled":
                self.log(f"[reg] email fill result={filled!r}")
                self._sleep(0.5)
                continue
            self._sleep(0.6)
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
        text.includes('注册') || lower.includes('sign up') || lower.includes('continue') ||
        lower.includes('next') || lower.includes('submit')
    );
});
if (!submitButton) return false;
submitButton.click();
return true;
"""
            )
            if clicked:
                self.log(f"[reg] email submitted: {email}")
                self._sleep(2)
                return
            self._sleep(0.5)
        raise BrowserRegisterError("email input/submit not found")

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
        page = self.page
        first, last = _rand_name()
        password = _rand_password()
        month, day, year = _rand_birthday()

        # JS-first like sample (data-testid fields)
        ok = self._js(
            r"""
const given = arguments[0], family = arguments[1], password = arguments[2];
const month = arguments[3], day = arguments[4], year = arguments[5];
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}
function setVal(selList, value) {
  for (const sel of selList) {
    const el = Array.from(document.querySelectorAll(sel)).find(isVisible);
    if (!el) continue;
    el.focus();
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
      || Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
    if (setter) setter.call(el, value); else el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }
  return false;
}
const g = setVal(['input[data-testid="givenName"]','input[name="givenName"]','input[autocomplete="given-name"]','input[name="given_name"]'], given);
const f = setVal(['input[data-testid="familyName"]','input[name="familyName"]','input[autocomplete="family-name"]','input[name="family_name"]'], family);
const p = setVal(['input[data-testid="password"]','input[name="password"]','input[type="password"]','input[autocomplete="new-password"]'], password);
setVal(['select[name="month"]','input[name="month"]','select[data-testid="month"]'], month);
setVal(['select[name="day"]','input[name="day"]','select[data-testid="day"]'], day);
setVal(['select[name="year"]','input[name="year"]','select[data-testid="year"]'], year);
return {g,f,p};
""",
            first,
            last,
            password,
            month,
            day,
            year,
        )
        self.log(f"[reg] profile fill={ok}")
        self._sleep(0.5)
        self._js(
            r"""
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button')).filter(
  n => isVisible(n) && !n.disabled
);
const btn = buttons.find((node) => {
  const t = (node.innerText || node.textContent || '').replace(/\s+/g, '').toLowerCase();
  return t.includes('signup') || t.includes('sign up') || t.includes('create') ||
         t.includes('continue') || t.includes('注册') || t.includes('完成') || t.includes('next');
}) || buttons[0];
if (btn) { btn.click(); return true; }
return false;
"""
        )
        self._sleep(3)
        return {
            "given_name": first,
            "family_name": last,
            "password": password,
            "birthday": f"{year}-{month}-{day}",
        }

    def wait_sso(self, timeout: float | None = None) -> str:
        timeout = float(timeout if timeout is not None else self.cfg.get("sso_timeout") or self.cfg.get("sso_timeout_base") or 240)
        deadline = time.time() + timeout
        self.log("[reg] wait sso cookie")
        while time.time() < deadline:
            try:
                cookies = self.page.cookies()
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
            self._sleep(2)
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

    def register_one(self) -> dict[str, Any]:
        """Full register once. Returns email/password/sso/profile."""
        if self._page is None:
            self.start()
        max_mail = max(1, int(self.cfg.get("register_max_attempts") or self.cfg.get("mail_retry_count") or 3))
        last_err: Exception | None = None
        for attempt in range(1, max_mail + 1):
            try:
                self.log(f"[reg] attempt {attempt}/{max_mail}")
                self.open_signup()
                email, token = self._mail.create_address()
                self.fill_email(email)
                # wait for code with optional resend mid-way
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
