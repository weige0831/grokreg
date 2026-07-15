from __future__ import annotations

import random
import re
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
    # age 25-40
    year = str(random.randint(1986, 2000))
    month = str(random.randint(1, 12))
    day = str(random.randint(1, 28))
    return month, day, year


class BrowserRegisterError(RuntimeError):
    pass


class BrowserRegistrar:
    """DrissionPage-based accounts.x.ai signup."""

    def __init__(self, cfg: dict[str, Any], log: LogFn | None = None) -> None:
        self.cfg = cfg
        self.log = log or default_log
        self._browser = None
        self._page = None
        self._mail = TempMailClient(
            base_url=str(cfg.get("tempmail_base_url") or "https://mail.minecraft-cn.net"),
            domains=list(cfg.get("tempmail_domains") or []),
            proxy=str(cfg.get("proxy") or ""),
            poll_interval=float(cfg.get("tempmail_poll_interval") or 2),
            timeout=float(cfg.get("tempmail_timeout") or cfg.get("mail_timeout") or 120),
            list_limit=int(cfg.get("tempmail_list_limit") or 30),
            log=self.log,
        )

    def start(self) -> None:
        from DrissionPage import Chromium, ChromiumOptions

        co = ChromiumOptions()
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

        # turnstilePatch extension if present
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

    def open_signup(self) -> None:
        self.log("[reg] open signup")
        self.page.get(SIGNUP_URL)
        self._sleep(2)

    def _click_email_option(self) -> None:
        page = self.page
        # try common buttons/links
        selectors = [
            "text:email",
            "text:Email",
            "text:使用邮箱",
            "text:用邮箱",
            "text:Continue with email",
            "css:button",
            "tag:button",
        ]
        for sel in selectors:
            try:
                els = page.eles(sel, timeout=1)
            except Exception:
                continue
            for el in els or []:
                try:
                    t = (el.text or "").lower()
                    if "email" in t or "邮箱" in t or "mail" in t:
                        el.click()
                        self._sleep(1)
                        return
                except Exception:
                    continue

    def fill_email(self, email: str) -> None:
        page = self.page
        self._click_email_option()
        self._sleep(0.5)
        # find email input
        inp = None
        for sel in (
            "css:input[type=email]",
            "css:input[name=email]",
            "css:input[autocomplete=email]",
            "css:input[type=text]",
        ):
            try:
                inp = page.ele(sel, timeout=3)
                if inp:
                    break
            except Exception:
                continue
        if not inp:
            raise BrowserRegisterError("email input not found")
        try:
            inp.clear()
        except Exception:
            pass
        inp.input(email)
        self._sleep(0.3)
        self._click_submit_like(["continue", "next", "提交", "继续", "sign"])
        self._sleep(2)

    def fill_code(self, code: str) -> None:
        page = self.page
        inp = None
        for sel in (
            "css:input[autocomplete=one-time-code]",
            "css:input[name=code]",
            "css:input[inputmode=numeric]",
            "css:input[type=text]",
        ):
            try:
                inp = page.ele(sel, timeout=5)
                if inp:
                    break
            except Exception:
                continue
        if not inp:
            raise BrowserRegisterError("code input not found")
        try:
            inp.clear()
        except Exception:
            pass
        inp.input(code)
        self._sleep(0.3)
        self._click_submit_like(["continue", "verify", "确认", "继续", "next"])
        self._sleep(2)

    def fill_profile(self) -> dict[str, str]:
        page = self.page
        first, last = _rand_name()
        password = _rand_password()
        month, day, year = _rand_birthday()

        def fill_by_selectors(selectors: list[str], value: str) -> bool:
            for sel in selectors:
                try:
                    el = page.ele(sel, timeout=2)
                    if el:
                        try:
                            el.clear()
                        except Exception:
                            pass
                        el.input(value)
                        return True
                except Exception:
                    continue
            return False

        fill_by_selectors(
            [
                "css:input[name=given_name]",
                "css:input[name=firstName]",
                "css:input[autocomplete=given-name]",
                "css:input[name=first_name]",
            ],
            first,
        )
        fill_by_selectors(
            [
                "css:input[name=family_name]",
                "css:input[name=lastName]",
                "css:input[autocomplete=family-name]",
                "css:input[name=last_name]",
            ],
            last,
        )
        fill_by_selectors(
            [
                "css:input[type=password]",
                "css:input[name=password]",
                "css:input[autocomplete=new-password]",
            ],
            password,
        )
        # birthday may be selects or inputs
        fill_by_selectors(
            ["css:input[name=month]", "css:select[name=month]", "css:input[placeholder*=Month]"],
            month,
        )
        fill_by_selectors(
            ["css:input[name=day]", "css:select[name=day]", "css:input[placeholder*=Day]"],
            day,
        )
        fill_by_selectors(
            ["css:input[name=year]", "css:select[name=year]", "css:input[placeholder*=Year]"],
            year,
        )
        self._sleep(0.5)
        self._click_submit_like(["sign up", "create", "continue", "注册", "完成", "next"])
        self._sleep(3)
        return {
            "given_name": first,
            "family_name": last,
            "password": password,
            "birthday": f"{year}-{month}-{day}",
        }

    def _click_submit_like(self, keywords: list[str]) -> None:
        page = self.page
        kws = [k.lower() for k in keywords]
        try:
            buttons = page.eles("tag:button", timeout=2) or []
        except Exception:
            buttons = []
        for b in buttons:
            try:
                t = (b.text or "").strip().lower()
                if any(k in t for k in kws) or t in {"", "→"}:
                    # prefer explicit matches
                    if any(k in t for k in kws):
                        b.click()
                        return
            except Exception:
                continue
        # type=submit
        try:
            sub = page.ele("css:button[type=submit]", timeout=2)
            if sub:
                sub.click()
                return
        except Exception:
            pass
        try:
            page.actions.key_down("ENTER").key_up("ENTER")
        except Exception:
            pass

    def wait_sso(self, timeout: float | None = None) -> str:
        timeout = float(timeout if timeout is not None else self.cfg.get("sso_timeout") or 240)
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
            # also try js
            try:
                js_sso = self.page.run_js(
                    "return document.cookie.split(';').map(s=>s.trim())"
                    ".find(s=>s.startsWith('sso='))"
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
        # best-effort: extension handles most; log if iframe present
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
        max_mail = max(1, int(self.cfg.get("register_max_attempts") or 3))
        last_err: Exception | None = None
        for attempt in range(1, max_mail + 1):
            email = ""
            token = ""
            try:
                self.log(f"[reg] attempt {attempt}/{max_mail}")
                self.open_signup()
                email, token = self._mail.create_address()
                self.fill_email(email)
                code = self._mail.wait_code(
                    token,
                    timeout=float(self.cfg.get("mail_timeout") or self.cfg.get("tempmail_timeout") or 150),
                )
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
