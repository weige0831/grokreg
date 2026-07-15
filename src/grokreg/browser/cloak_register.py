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

SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"

_TIMEZONES = [
    "America/New_York",
    "America/Chicago",
    "America/Los_Angeles",
    "America/Denver",
    "Europe/London",
    "Europe/Berlin",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Australia/Sydney",
]
_LOCALES = ["en-US", "en-GB", "en-CA", "en-AU"]
_PLATFORMS = ["windows", "macos", "linux"]
_VIEWPORTS = [
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1920, "height": 1080},
    {"width": 1280, "height": 800},
]


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


def random_fingerprint_args() -> list[str]:
    """Random CloakBrowser fingerprint flags (source-level)."""
    fp = random.randint(10000, 99999)
    plat = random.choice(_PLATFORMS)
    args = [
        f"--fingerprint={fp}",
        f"--fingerprint-platform={plat}",
    ]
    # optional noise
    if random.random() < 0.5:
        args.append(f"--fingerprint-hardware-concurrency={random.choice([4, 6, 8, 12, 16])}")
    return args


class BrowserRegisterError(RuntimeError):
    pass


class CloakBrowserRegistrar:
    """accounts.x.ai signup via CloakBrowser (Playwright API + random fingerprint)."""

    def __init__(self, cfg: dict[str, Any], log: LogFn | None = None) -> None:
        self.cfg = cfg
        self.log = log or default_log
        self._browser = None
        self._context = None
        self._page = None
        self._fingerprint: dict[str, Any] = {}
        self._diag_dir = Path(str(cfg.get("diag_dir") or "diag"))
        self._phase_started = time.time()
        self._phase_name = "init"
        self._last_diag_at = 0.0
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

    @property
    def page(self):
        if self._page is None:
            raise BrowserRegisterError("browser not started")
        return self._page

    def _page_alive(self) -> bool:
        try:
            page = self._page
            if page is None:
                return False
            _ = page.url
            return True
        except Exception:
            return False

    def _js(self, script: str, *args: Any) -> Any:
        if not self._page_alive():
            return None
        try:
            if args:
                return self.page.evaluate(script, list(args) if len(args) != 1 else args[0])
            return self.page.evaluate(script)
        except Exception as exc:
            msg = str(exc)
            if "has been closed" in msg or "Target closed" in msg:
                self.log(f"[reg] page closed: {exc}")
            else:
                self.log(f"[reg] js error: {exc}")
            return None

    def _eval(self, script: str) -> Any:
        return self._js(script)

    def start(self) -> None:
        from cloakbrowser import launch

        headless = bool(self.cfg.get("headless", False))
        humanize = bool(self.cfg.get("cloak_humanize", True))
        geoip = bool(self.cfg.get("cloak_geoip", False))

        # random fingerprint each launch
        tz = str(self.cfg.get("cloak_timezone") or random.choice(_TIMEZONES))
        locale = str(self.cfg.get("cloak_locale") or random.choice(_LOCALES))
        fp_args = random_fingerprint_args()
        # disable built-in stealth_args defaults so we fully control fingerprint seed
        # default True: cloak built-in stealth + our random fingerprint seed
        stealth_args = bool(self.cfg.get("cloak_stealth_args", True))
        extra = list(self.cfg.get("cloak_extra_args") or [])
        # when stealth_args True, still pass our random --fingerprint=* (deduped by build_args)
        args = fp_args + extra

        proxy = str(
            self.cfg.get("browser_proxy")
            if self.cfg.get("browser_proxy") is not None and str(self.cfg.get("browser_proxy")).strip()
            else (self.cfg.get("proxy") if self.cfg.get("browser_use_proxy", True) else "")
        ).strip()
        if not self.cfg.get("browser_use_proxy", True):
            # default for cloak: still allow explicit browser_proxy only
            proxy = str(self.cfg.get("browser_proxy") or "").strip()

        ext_paths: list[str] = []
        if bool(self.cfg.get("turnstile_extension", True)):
            ext = Path(__file__).resolve().parent / "turnstilePatch"
            if ext.is_dir() and (ext / "manifest.json").is_file():
                ext_paths.append(str(ext))

        self._fingerprint = {
            "args": args,
            "timezone": tz,
            "locale": locale,
            "proxy": bool(proxy),
            "humanize": humanize,
            "viewport": random.choice(_VIEWPORTS),
        }
        self.log(
            f"[browser] cloak launch headless={headless} humanize={humanize} "
            f"tz={tz} locale={locale} fp={args} proxy={bool(proxy)}"
        )

        launch_kw: dict[str, Any] = {
            "headless": headless,
            "stealth_args": stealth_args,
            "args": args,
            "timezone": tz,
            "locale": locale,
            "geoip": geoip,
            "humanize": humanize,
            "human_preset": str(self.cfg.get("cloak_human_preset") or "default"),
        }
        if proxy:
            launch_kw["proxy"] = proxy
        if ext_paths:
            launch_kw["extension_paths"] = ext_paths
        lic = str(self.cfg.get("cloak_license_key") or "").strip()
        if lic:
            launch_kw["license_key"] = lic

        last_exc: Exception | None = None
        for attempt in range(1, 4):
            try:
                self._browser = launch(**launch_kw)
                self._context = None
                self._page = self._browser.new_page(
                    viewport=self._fingerprint["viewport"],
                    locale=locale,
                )
                if attempt > 1:
                    self.log(f"[browser] cloak started attempt {attempt}")
                return
            except Exception as exc:
                last_exc = exc
                self.log(f"[browser] cloak start fail {attempt}/3: {exc}")
                try:
                    if self._browser is not None:
                        self._browser.close()
                except Exception:
                    pass
                self._browser = None
                self._page = None
                self._sleep(min(1.5 * attempt, 4))
        raise BrowserRegisterError(f"cloakbrowser start failed: {last_exc}")

    def stop(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        self._browser = None
        self._context = None
        self._page = None

    def restart(self) -> None:
        self.stop()
        self._sleep(1)
        self.start()

    def dump_diagnostics(self, tag: str, *, elapsed: float | None = None) -> Path | None:
        page = self._page
        if page is None:
            return None
        try:
            self._diag_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return None
        ts = time.strftime("%Y%m%d_%H%M%S")
        base = self._diag_dir / f"{ts}_{tag}"
        shot = Path(str(base) + ".png")
        try:
            page.screenshot(path=str(shot), full_page=True)
        except Exception:
            try:
                page.screenshot(path=str(shot))
            except Exception as exc:
                self.log(f"[diag] screenshot failed: {exc}")
                shot = Path("")
        state: dict[str, Any] = {
            "tag": tag,
            "elapsed": elapsed,
            "url": "",
            "fingerprint": self._fingerprint,
            "cf_token_len": 0,
            "visible_text": "",
            "verdict": "unknown",
        }
        try:
            state["url"] = str(page.url or "")
        except Exception:
            pass
        try:
            info = self._eval(
                r"""() => {
  const cf = document.querySelector('input[name="cf-turnstile-response"]');
  const tok = String((cf && cf.value) || '').trim();
  return {
    title: document.title || '',
    cf_token_len: tok.length,
    text: (document.body && document.body.innerText || '').slice(0, 800)
  };
}"""
            )
            if isinstance(info, dict):
                state["title"] = info.get("title") or ""
                state["cf_token_len"] = int(info.get("cf_token_len") or 0)
                state["visible_text"] = str(info.get("text") or "")[:800]
        except Exception as exc:
            state["js_error"] = str(exc)[:200]
        meta = Path(str(base) + ".json")
        try:
            meta.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        self.log(
            f"[diag] tag={tag} url={str(state.get('url') or '')[:80]} "
            f"cf_token_len={state.get('cf_token_len')} shot={shot.name if shot else '-'}"
        )
        return meta

    def _maybe_diag(self) -> None:
        stall = float(self.cfg.get("diag_stall_sec") or 30)
        now = time.time()
        elapsed = now - self._phase_started
        if elapsed < stall:
            return
        if self._last_diag_at and (now - self._last_diag_at) < stall:
            return
        self._last_diag_at = now
        self.dump_diagnostics(self._phase_name or "stall", elapsed=elapsed)

    # ── UI helpers (Playwright evaluate, sample-aligned JS) ───────────

    def open_signup(self) -> None:
        self._begin_phase("open_signup")
        self.log("[reg] open signup")
        try:
            self.page.goto(SIGNUP_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            self.dump_diagnostics("open_signup_nav_error")
            raise BrowserRegisterError(f"open signup failed: {exc}") from exc
        self._sleep(2)
        url = str(self.page.url or "")
        self.log(f"[reg] url={url}")
        if "x.ai" not in url:
            self.dump_diagnostics("open_signup_bad_url")
            raise BrowserRegisterError(f"signup page not loaded: {url}")
        self._click_email_signup()

    def _click_email_signup(self, timeout: float = 15) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            ready = self._eval(
                r"""() => {
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
}"""
            )
            if ready:
                self.log("[reg] email form already visible")
                return True
            clicked = self._eval(
                r"""() => {
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}
function nodeText(node) {
  return [node.innerText, node.textContent, node.getAttribute('aria-label'), node.getAttribute('title'), node.getAttribute('href')]
    .filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
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
if (!candidates.length) return false;
candidates[0].node.click();
return candidates[0].text || true;
}"""
            )
            if clicked:
                self.log(f"[reg] clicked email signup: {clicked}")
                self._sleep(2)
                return True
            self._sleep(1)
        return False

    def _email_page_advanced_once(self) -> bool:
        return bool(
            self._eval(
                r"""() => {
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
  return [node.getAttribute('aria-label'), node.getAttribute('placeholder'), node.getAttribute('name'),
    node.getAttribute('id'), node.getAttribute('autocomplete'), node.getAttribute('data-testid')]
    .filter(Boolean).join(' ').toLowerCase();
}
const codeInput = Array.from(document.querySelectorAll('input')).find((node) => {
  if (!isVisible(node)) return false;
  const type = (node.getAttribute('type') || '').toLowerCase();
  if (['hidden','submit','button','checkbox','radio','file'].includes(type)) return false;
  const meta = textOf(node);
  const inMode = (node.getAttribute('inputmode') || '').toLowerCase();
  return meta.includes('code') || meta.includes('otp') || meta.includes('verif') || meta.includes('验证')
    || inMode === 'numeric' || node.getAttribute('autocomplete') === 'one-time-code';
});
if (codeInput) return true;
const emailInput = Array.from(document.querySelectorAll(
  'input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"]'
)).find((node) => isVisible(node) && !node.disabled && !node.readOnly);
return !emailInput;
}"""
            )
        )

    def _wait_email_advanced(self, wait: float = 4.0) -> bool:
        deadline = time.time() + wait
        while time.time() < deadline:
            if self._email_page_advanced_once():
                return True
            self._sleep(0.4)
        return False

    def fill_email(self, email: str) -> None:
        self._begin_phase("fill_email")
        deadline = time.time() + float(self.cfg.get("email_form_timeout") or 45)
        email_js = json.dumps(email)
        while time.time() < deadline:
            self._maybe_diag()
            filled = self._eval(
                f"""() => {{
const email = {email_js};
function isVisible(node) {{
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}}
function textOf(node) {{
  return [node.innerText, node.textContent, node.getAttribute('aria-label'), node.getAttribute('placeholder'),
    node.getAttribute('data-testid'), node.getAttribute('name'), node.getAttribute('id'), node.getAttribute('autocomplete')]
    .filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
}}
function emailCandidates() {{
  const direct = Array.from(document.querySelectorAll(
    'input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i]'
  ));
  for (const node of Array.from(document.querySelectorAll('input, textarea'))) {{
    const type = (node.getAttribute('type') || '').toLowerCase();
    if (['hidden','submit','button','checkbox','radio','file','search'].includes(type)) continue;
    const meta = textOf(node).toLowerCase();
    if (meta.includes('email') || meta.includes('mail') || meta.includes('邮箱')) direct.push(node);
  }}
  return Array.from(new Set(direct));
}}
const input = emailCandidates().find((n) => isVisible(n) && !n.disabled && !n.readOnly) || null;
if (!input) return {{state:'not-ready', url: location.href}};
input.focus(); input.click();
const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
const tracker = input._valueTracker;
if (tracker) tracker.setValue('');
if (setter) setter.call(input, email); else input.value = email;
input.dispatchEvent(new InputEvent('beforeinput', {{bubbles:true, data:email, inputType:'insertText'}}));
input.dispatchEvent(new InputEvent('input', {{bubbles:true, data:email, inputType:'insertText'}}));
input.dispatchEvent(new Event('change', {{bubbles:true}}));
input.blur();
if ((input.value || '').trim() !== email) return {{state:'fill-failed', value: input.value}};
return {{state:'filled'}};
}}"""
            )
            state = filled.get("state") if isinstance(filled, dict) else filled
            if state == "not-ready":
                self._click_email_signup(timeout=3)
                self._sleep(0.5)
                continue
            if state != "filled":
                self._sleep(0.5)
                continue
            self._sleep(0.8)
            clicked = self._eval(
                r"""() => {
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
  return [node.innerText, node.textContent, node.getAttribute('aria-label'), node.getAttribute('title')]
    .filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]'))
  .filter((n) => isVisible(n) && !n.disabled && n.getAttribute('aria-disabled') !== 'true');
const submitButton = buttons.find((node) => {
  const text = textOf(node).replace(/\s+/g, '');
  const lower = text.toLowerCase();
  return text === '注册' || text.includes('注册') || text.includes('继续') || text.includes('下一步')
    || lower.includes('signup') || lower.includes('sign up') || lower.includes('continue')
    || lower.includes('next') || lower.includes('submit');
});
if (submitButton) { submitButton.click(); return textOf(submitButton) || true; }
const input = document.querySelector('input[type="email"], input[name="email"]');
if (input) {
  const form = input.closest('form');
  if (form) { if (form.requestSubmit) form.requestSubmit(); else form.dispatchEvent(new Event('submit', {bubbles:true, cancelable:true})); return 'form-submit'; }
}
return false;
}"""
            )
            if clicked and self._wait_email_advanced(4.0):
                self.log(f"[reg] email submitted and advanced: {email} ({clicked})")
                return
            if clicked:
                # detect CreateEmailValidationCode 403 via performance if possible
                blocked = self._eval(
                    r"""() => {
try {
  const list = performance.getEntriesByType('resource') || [];
  return list.some((e) => String(e.name||'').includes('CreateEmailValidationCode') && (e.responseStatus === 403));
} catch(e) { return false; }
}"""
                )
                if blocked:
                    self.dump_diagnostics("email_cf_403")
                    raise BrowserRegisterError("CF blocked CreateEmailValidationCode HTTP 403")
                self.log(f"[reg] clicked submit but page not advanced, retry: {email}")
            self._sleep(0.5)
        self.dump_diagnostics("email_form_fail")
        raise BrowserRegisterError("email input/submit not found")

    def _resend_code(self) -> None:
        self._eval(
            r"""() => {
const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'));
const target = nodes.find((node) => {
  const t = (node.innerText || node.textContent || '').replace(/\s+/g, '').toLowerCase();
  return t.includes('重新发送') || t.includes('resend') || t.includes('再次发送');
});
if (target && !target.disabled) { target.click(); return true; }
return false;
}"""
        )

    def fill_code(self, code: str) -> None:
        self._begin_phase("fill_code")
        clean = str(code).replace("-", "").strip()
        display = str(code).strip()
        deadline = time.time() + float(self.cfg.get("code_form_timeout") or 60)
        while time.time() < deadline:
            self._maybe_diag()
            filled = self._eval(
                f"""() => {{
const code = {json.dumps(clean)};
const raw = {json.dumps(display)};
function isVisible(node) {{
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}}
function setInputValue(input, value) {{
  const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
  const tracker = input._valueTracker;
  if (tracker) tracker.setValue('');
  if (nativeSetter) nativeSetter.call(input, value); else input.value = value;
  input.dispatchEvent(new InputEvent('beforeinput', {{bubbles:true, data:value, inputType:'insertText'}}));
  input.dispatchEvent(new InputEvent('input', {{bubbles:true, data:value, inputType:'insertText'}}));
  input.dispatchEvent(new Event('change', {{bubbles:true}}));
}}
const aggregate = Array.from(document.querySelectorAll(
  'input[data-input-otp="true"], input[name="code"], input[autocomplete="one-time-code"], input[inputmode="numeric"], input[inputmode="text"]'
)).find((node) => isVisible(node) && !node.disabled && !node.readOnly && Number(node.maxLength || 6) > 1);
if (aggregate) {{
  aggregate.focus(); aggregate.click();
  setInputValue(aggregate, raw.includes('-') ? raw : code);
  if (String(aggregate.value||'').replace(/\\s+/g,'')) return 'filled-aggregate';
  setInputValue(aggregate, code);
  return String(aggregate.value||'').replace(/\\s+/g,'') ? 'filled-aggregate' : 'aggregate-failed';
}}
const otpBoxes = Array.from(document.querySelectorAll('input')).filter((node) => {{
  if (!isVisible(node) || node.disabled || node.readOnly) return false;
  const maxLength = Number(node.maxLength || 0);
  const ac = String(node.autocomplete || '').toLowerCase();
  return maxLength === 1 || ac === 'one-time-code';
}});
if (otpBoxes.length >= code.length) {{
  for (let i = 0; i < code.length; i++) {{
    const ch = code[i] || '';
    const box = otpBoxes[i];
    box.focus(); box.click(); setInputValue(box, ch);
  }}
  const merged = otpBoxes.slice(0, code.length).map((x) => String(x.value||'').trim()).join('');
  return merged.length ? 'filled-boxes' : 'boxes-failed';
}}
return 'not-ready';
}}"""
            )
            if filled == "not-ready" or (filled and "failed" in str(filled)):
                self._sleep(0.5)
                continue
            clicked = self._eval(
                r"""() => {
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
  return t.includes('确认邮箱') || t.includes('继续') || t.includes('下一步')
    || t.includes('confirm') || t.includes('continue') || t.includes('next');
});
if (!btn) return 'no-button';
btn.focus(); btn.click();
return 'clicked';
}"""
            )
            self.log(f"[reg] code submitted: {display} click={clicked}")
            self._sleep(1.5)
            return
        self.dump_diagnostics("code_fail")
        raise BrowserRegisterError("code fill/submit failed")

    def _cf_token_len(self) -> int:
        try:
            n = self._eval(
                r"""() => {
const cf = document.querySelector('input[name="cf-turnstile-response"]');
return String((cf && cf.value) || '').trim().length;
}"""
            )
            return int(n or 0)
        except Exception:
            return 0

    def _click_turnstile(self) -> bool:
        """Click CF Turnstile checkbox (left side of widget / nested frame)."""
        if not self._page_alive():
            return False
        page = self.page
        # 1) host-page iframe element: click left checkbox area with real mouse
        try:
            loc = page.locator(
                'iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"], div.cf-turnstile iframe'
            ).first
            if loc.count() > 0:
                box = loc.bounding_box()
                if box:
                    x = box["x"] + min(28, max(12, box["width"] * 0.12))
                    y = box["y"] + box["height"] / 2
                    page.mouse.move(x, y)
                    page.mouse.down()
                    page.mouse.up()
                    self.log(f"[reg] turnstile mouse click ({x:.0f},{y:.0f})")
                    return True
        except Exception as exc:
            self.log(f"[reg] turnstile host click: {exc}")

        # 2) frame content click
        try:
            for fr in page.frames:
                url = (fr.url or "").lower()
                if "challenges.cloudflare.com" not in url and "turnstile" not in url:
                    continue
                for sel in (
                    "input[type=checkbox]",
                    "label",
                    ".mark",
                    "#challenge-stage",
                    "body",
                ):
                    try:
                        el = fr.locator(sel).first
                        el.click(timeout=800, force=True)
                        self.log(f"[reg] clicked turnstile frame sel={sel}")
                        return True
                    except Exception:
                        continue
                try:
                    # coordinate inside frame viewport (checkbox left)
                    fr.mouse.click(25, 30)
                    self.log("[reg] clicked turnstile frame coords")
                    return True
                except Exception:
                    pass
        except Exception as exc:
            self.log(f"[reg] turnstile frame: {exc}")

        # 3) JS fallback
        clicked = self._eval(
            r"""() => {
const nodes = Array.from(document.querySelectorAll(
  'iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey]'
));
for (const n of nodes) {
  try {
    n.scrollIntoView({block:'center'});
    const r = n.getBoundingClientRect();
    const x = r.left + Math.min(30, r.width * 0.15);
    const y = r.top + r.height / 2;
    for (const type of ['mousemove','mousedown','mouseup','click']) {
      n.dispatchEvent(new MouseEvent(type, {bubbles:true, clientX:x, clientY:y, view:window}));
    }
  } catch(e) {}
}
return nodes.length > 0;
}"""
        )
        if clicked:
            self.log("[reg] clicked turnstile widget (js)")
        return bool(clicked)

    def fill_profile(self) -> dict[str, str]:
        self._begin_phase("profile_turnstile")
        first, last = _rand_name()
        password = _rand_password()
        deadline = time.time() + float(self.cfg.get("profile_timeout") or 180)
        form_filled = False
        wait_cf_since: float | None = None
        last_click = 0.0

        while time.time() < deadline:
            if not self._page_alive():
                raise BrowserRegisterError("browser/page closed during profile")
            self._maybe_diag()
            if not form_filled:
                filled = self._eval(
                    f"""() => {{
const givenName = {json.dumps(first)};
const familyName = {json.dumps(last)};
const password = {json.dumps(password)};
function isVisible(node) {{
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}}
function pickInput(selector) {{
  return Array.from(document.querySelectorAll(selector)).find((n) => isVisible(n) && !n.disabled && !n.readOnly) || null;
}}
function setInputValue(input, value) {{
  if (!input) return false;
  input.focus(); input.click();
  const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
  const tracker = input._valueTracker;
  if (tracker) tracker.setValue('');
  if (nativeSetter) nativeSetter.call(input, value); else input.value = value;
  input.dispatchEvent(new InputEvent('input', {{bubbles:true, data:value, inputType:'insertText'}}));
  input.dispatchEvent(new Event('change', {{bubbles:true}}));
  input.blur();
  return String(input.value||'').trim() === String(value||'').trim();
}}
const givenInput = pickInput('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"]');
const familyInput = pickInput('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"]');
const passwordInput = pickInput('input[data-testid="password"], input[name="password"], input[type="password"], input[autocomplete="new-password"]');
if (!givenInput || !familyInput || !passwordInput) return 'not-ready';
if (!setInputValue(givenInput, givenName) || !setInputValue(familyInput, familyName) || !setInputValue(passwordInput, password)) return 'fill-failed';
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]');
if (cfPresent) {{
  const token = String((cfInput && cfInput.value) || '').trim();
  if (token.length < 80) return 'wait-cloudflare:' + token.length;
}}
return 'ready-to-submit';
}}"""
                )
                self.log(f"[reg] profile fill state={filled}")
                if isinstance(filled, str) and filled.startswith("wait-cloudflare"):
                    form_filled = True
                    if wait_cf_since is None:
                        wait_cf_since = time.time()
                elif filled in ("ready-to-submit", "filled-no-submit"):
                    form_filled = True
                else:
                    self._sleep(0.5)
                    continue

            tok_len = self._cf_token_len()
            if tok_len < 80:
                now = time.time()
                if wait_cf_since is None:
                    wait_cf_since = now
                # don't hammer every 2s — click every 4s and wait for token
                if now - last_click >= 4:
                    self._click_turnstile()
                    last_click = now
                # poll token more patiently after click
                for _ in range(6):
                    self._sleep(0.5)
                    if self._cf_token_len() >= 80:
                        break
                continue

            submit_state = self._eval(
                r"""() => {
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey]');
if (cfPresent) {
  const token = String((cfInput && cfInput.value) || '').trim();
  if (token.length < 80) return 'wait-cloudflare:' + token.length;
}
function buttonText(node) {
  return [node.innerText, node.textContent, node.getAttribute('value'), node.getAttribute('aria-label'), node.getAttribute('title')]
    .filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]'))
  .filter((n) => isVisible(n) && !n.disabled && n.getAttribute('aria-disabled') !== 'true');
const submitBtn = buttons.find((node) => {
  const t = buttonText(node).replace(/\s+/g, '').toLowerCase();
  return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount') || t.includes('注册');
});
if (!submitBtn) return 'no-submit-button';
submitBtn.focus(); submitBtn.click();
return 'submitted';
}"""
            )
            self.log(f"[reg] profile submit={submit_state}")
            if submit_state == "submitted":
                self._sleep(2)
                return {"given_name": first, "family_name": last, "password": password}
            if isinstance(submit_state, str) and submit_state.startswith("wait-cloudflare"):
                self._click_turnstile()
            self._sleep(0.6)
        self.dump_diagnostics("profile_fail")
        raise BrowserRegisterError("profile fill/submit failed")

    def wait_sso(self, timeout: float | None = None) -> str:
        timeout = float(timeout if timeout is not None else self.cfg.get("sso_timeout") or 180)
        deadline = time.time() + timeout
        self._begin_phase("wait_sso")
        self.log("[reg] wait sso cookie")
        last_submit = 0.0
        while time.time() < deadline:
            self._maybe_diag()
            now = time.time()
            if now - last_submit >= 2.5:
                if self._cf_token_len() >= 80:
                    self._eval(
                        r"""() => {
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button')).filter((n) => isVisible(n) && !n.disabled);
const submitBtn = buttons.find((node) => {
  const t = (node.innerText || node.textContent || '').replace(/\s+/g, '').toLowerCase();
  return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount') || t.includes('注册');
});
if (submitBtn) { submitBtn.click(); return 'clicked'; }
return 'no';
}"""
                    )
                else:
                    self._click_turnstile()
                last_submit = now
            try:
                cookies = self.page.context.cookies()
            except Exception:
                cookies = []
            for c in cookies or []:
                if str(c.get("name") or "").lower() == "sso" and str(c.get("value") or ""):
                    sso = str(c["value"]).strip()
                    if len(sso) > 20:
                        self.log("[reg] sso obtained")
                        return sso
            js_sso = self._eval(
                r"""() => {
const m = (document.cookie || '').split(';').map(s => s.trim()).find(s => s.startsWith('sso='));
return m || '';
}"""
            )
            if js_sso and str(js_sso).startswith("sso="):
                sso = str(js_sso)[4:].strip()
                if sso:
                    self.log("[reg] sso obtained (js)")
                    return sso
            self._sleep(1)
        self.dump_diagnostics("sso_timeout")
        raise BrowserRegisterError("sso timeout")

    def extract_cf_clearance_and_ua(self) -> tuple[str, str]:
        cf_clearance = ""
        user_agent = ""
        try:
            for c in self.page.context.cookies() or []:
                if c.get("name") == "cf_clearance" and c.get("value"):
                    cf_clearance = str(c["value"])
                    break
            user_agent = str(self._eval("() => navigator.userAgent") or "")
        except Exception as exc:
            self.log(f"[reg] extract cf: {exc}")
        return cf_clearance, user_agent

    def browser_web_default_chat(self) -> dict[str, Any]:
        """In-browser request to grok.com default web model; success = got model reply."""
        self._begin_phase("web_default_chat")
        try:
            self.page.goto("https://grok.com/", wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            self.log(f"[web] open grok.com: {exc}")
        self._sleep(2)
        model = str(self.cfg.get("web_default_model") or "grok-3")
        prompt = str(self.cfg.get("web_probe_prompt") or "1+1=? Reply with one number only.")
        # try REST new conversation (web default)
        result = self._eval(
            f"""async () => {{
const model = {json.dumps(model)};
const prompt = {json.dumps(prompt)};
const tries = [
  {{
    url: 'https://grok.com/rest/app-chat/conversations/new',
    body: {{
      temporary: true,
      modelName: model,
      message: prompt,
      fileAttachments: [],
      imageAttachments: [],
      disableSearch: true,
      enableImageGeneration: false,
      returnImageBytes: false,
      returnRawGrokInXaiRequest: false,
      enableImageStreaming: false,
      imageGenerationCount: 0,
      forceConcise: true,
      toolOverrides: {{}},
      enableSideBySide: false,
      isPreset: false,
      sendFinalMetadata: true
    }}
  }},
  {{
    url: 'https://grok.com/rest/app-chat/conversations/new',
    body: {{ temporary: true, model: model, message: prompt }}
  }}
];
const out = [];
for (const t of tries) {{
  try {{
    const r = await fetch(t.url, {{
      method: 'POST',
      credentials: 'include',
      headers: {{
        'content-type': 'application/json',
        'origin': 'https://grok.com',
        'referer': 'https://grok.com/'
      }},
      body: JSON.stringify(t.body)
    }});
    const text = (await r.text()).slice(0, 1500);
    out.push({{url: t.url, status: r.status, text}});
    if (r.status >= 200 && r.status < 300 && text && !text.toLowerCase().includes('permission-denied')) {{
      // stream/json may contain model tokens
      const low = text.toLowerCase();
      const hasReply = text.length > 20 || /\\b2\\b/.test(text) || low.includes('message') || low.includes('token') || low.includes('result');
      if (hasReply) return {{ok: true, status: r.status, text: text.slice(0, 400), model}};
    }}
  }} catch (e) {{
    out.push({{url: t.url, status: 0, text: String(e)}});
  }}
}}
return {{ok: false, status: 0, text: JSON.stringify(out).slice(0, 500), model}};
}}"""
        )
        # Playwright evaluate may return coroutine result already resolved
        if not isinstance(result, dict):
            self._sleep(1)
            result = {"ok": False, "status": 0, "text": str(result)[:300], "model": model}
        ok = bool(result.get("ok"))
        self.log(
            f"[web] default model probe ok={ok} status={result.get('status')} "
            f"model={result.get('model')} text={(str(result.get('text') or ''))[:120]!r}"
        )
        return {
            "ok": ok,
            "status": int(result.get("status") or 0),
            "code": "web_ok" if ok else "web_fail",
            "model": result.get("model") or model,
            "text": str(result.get("text") or "")[:400],
            "endpoint": "grok.com/rest/app-chat",
        }

    def _browser_activate_light(self) -> None:
        try:
            self.page.goto("https://accounts.x.ai/accept-tos", wait_until="domcontentloaded", timeout=30000)
            self._sleep(1.5)
            self._eval(
                r"""() => {
const btns = Array.from(document.querySelectorAll('button, [role="button"], a'));
const t = btns.find((n) => {
  const s = (n.innerText || n.textContent || '').replace(/\s+/g, '').toLowerCase();
  return s.includes('accept') || s.includes('agree') || s.includes('同意') || s.includes('接受') || s.includes('继续') || s.includes('continue');
});
if (t) { t.click(); return true; }
return false;
}"""
            )
        except Exception as exc:
            self.log(f"[reg] accept-tos: {exc}")
        try:
            import random as _r
            from datetime import date

            today = date.today()
            age = _r.randint(20, 40)
            birth = f"{today.year - age}-{_r.randint(1,12):02d}-{_r.randint(1,28):02d}T16:00:00.000Z"
            self.page.goto("https://grok.com/", wait_until="domcontentloaded", timeout=45000)
            self._sleep(1.5)
            res = self._eval(
                f"""async () => {{
const r = await fetch('https://grok.com/rest/auth/set-birth-date', {{
  method: 'POST', credentials: 'include',
  headers: {{'content-type':'application/json','origin':'https://grok.com','referer':'https://grok.com/'}},
  body: JSON.stringify({{birthDate: {json.dumps(birth)}}})
}});
return {{status: r.status, text: (await r.text()).slice(0,120)}};
}}"""
            )
            self.log(f"[reg] set-birth-date via browser: {res}")
        except Exception as exc:
            self.log(f"[reg] set-birth-date: {exc}")

    def register_one(self) -> dict[str, Any]:
        if self._page is None:
            self.start()
        max_mail = max(1, int(self.cfg.get("register_max_attempts") or self.cfg.get("mail_retry_count") or 3))
        last_err: Exception | None = None
        for attempt in range(1, max_mail + 1):
            try:
                self.log(f"[reg] attempt {attempt}/{max_mail} engine=cloak")
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
                self._browser_activate_light()
                cf_clearance, browser_ua = self.extract_cf_clearance_and_ua()
                if cf_clearance:
                    self.log(f"[reg] cf_clearance len={len(cf_clearance)}")
                web = self.browser_web_default_chat()
                return {
                    "email": email,
                    "password": profile.get("password") or "",
                    "sso": sso,
                    "profile": profile,
                    "cf_clearance": cf_clearance,
                    "user_agent": browser_ua,
                    "web": web,
                    "fingerprint": self._fingerprint,
                }
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
