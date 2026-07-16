# -*- coding: utf-8 -*-
"""Protocol register: x.ai signup (HTTP) + Build OAuth + probe.

Based on edi/grokv2/app/grok-build-auth (xconsole_client).
Captcha: Camoufox local solver by default (YesCaptcha-compatible API on :5072).
"""
from __future__ import annotations

import json
import os
import random
import string
import time
import uuid
from pathlib import Path
from typing import Any

from grokreg.mail.tempmail import TempMailClient, TempMailError
from grokreg.mint.auth_code import token_to_cpa_record
from grokreg.probe.build45 import probe_chat_completions, probe_responses
from grokreg.protocol.local_solver import (
    local_solver_url,
    pin_local_solver_env,
    wait_for_local_solver,
)
from grokreg.util.log import LogFn, default_log

SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"


class ProtocolRegisterError(RuntimeError):
    pass


def _rand_password(n: int = 12) -> str:
    body = "".join(random.choices(string.ascii_letters + string.digits, k=n))
    return f"Pw{body}!a#A"


def _rand_name() -> tuple[str, str]:
    firsts = ["Aiden", "Liam", "Noah", "Ethan", "Mason", "Logan", "Lucas", "James"]
    lasts = ["Chen", "Wang", "Li", "Zhang", "Liu", "Fang", "Wu", "Zhou"]
    return random.choice(firsts), random.choice(lasts)


class ProtocolRegistrar:
    """Pure-protocol registrar (no Drission browser)."""

    def __init__(self, cfg: dict[str, Any], log: LogFn | None = None) -> None:
        self.cfg = cfg or {}
        self.log = log or default_log
        self._client = None

    def start(self) -> None:
        return

    def stop(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def restart(self) -> None:
        self.stop()
        self.start()

    def _make_solver(self, *, proxy: str, debug: bool):
        """Camoufox local solver (default) or optional YesCaptcha remote."""
        from grokreg.protocol.xconsole_client import YesCaptchaSolver

        provider = str(self.cfg.get("captcha_provider") or "local").strip().lower()
        if provider in {"local", "camoufox", "solver"}:
            endpoint = local_solver_url(self.cfg)
            pin_local_solver_env(endpoint)
            wait_sec = float(self.cfg.get("local_solver_wait_sec") or 120)
            ready = wait_for_local_solver(
                endpoint,
                timeout_sec=wait_sec,
                poll_sec=1.0,
                log=self.log,
            )
            if not ready.get("ready"):
                raise ProtocolRegisterError(
                    ready.get("error")
                    or f"本地 Camoufox 过盾未就绪: {endpoint}。"
                    "请先启动: cd turnstile-solver && bash start.sh"
                )
            self.log(f"[proto] captcha=local Camoufox {endpoint}")
            return YesCaptchaSolver(
                "local",
                endpoint=endpoint,
                timeout=float(self.cfg.get("local_solver_timeout") or 120),
                poll_interval=1.0,
                debug=debug,
                auto_fallback_endpoint=False,
            ), "local"

        # optional remote YesCaptcha (explicit only)
        ykey = (
            str(self.cfg.get("yescaptcha_api_key") or "").strip()
            or os.environ.get("YESCAPTCHA_API_KEY", "").strip()
        )
        if not ykey:
            raise ProtocolRegisterError(
                "captcha_provider=yescaptcha 需要 yescaptcha_api_key；"
                "默认请用 local Camoufox（captcha_provider=local）"
            )
        self.log("[proto] captcha=yescaptcha remote")
        return YesCaptchaSolver(
            ykey,
            timeout=180,
            poll_interval=2.0,
            debug=debug,
        ), "yescaptcha"

    def register_one(self) -> dict[str, Any]:
        """signup → sso → protocol Build OAuth → probe."""
        proxy = str(self.cfg.get("proxy") or self.cfg.get("mint_proxy") or "").strip()
        debug = bool(self.cfg.get("protocol_debug", False))

        try:
            from grokreg.protocol.xconsole_client import XConsoleAuthClient
            from grokreg.protocol.xconsole_client import config as xc_config
            from grokreg.protocol.xconsole_client.oauth_protocol import (
                extract_cookies_from_auth_client,
                login_with_protocol,
            )
        except Exception as exc:
            raise ProtocolRegisterError(f"xconsole_client import failed: {exc}") from exc

        solver, captcha_mode = self._make_solver(proxy=proxy, debug=debug)
        # pin env so oauth_protocol's internal YesCaptchaSolver also hits local
        if captcha_mode == "local":
            pin_local_solver_env(local_solver_url(self.cfg))
            ykey_for_oauth = "local"
        else:
            ykey_for_oauth = (
                str(self.cfg.get("yescaptcha_api_key") or "").strip()
                or os.environ.get("YESCAPTCHA_API_KEY", "").strip()
            )

        mail = TempMailClient(
            base_url=str(self.cfg.get("tempmail_base_url") or "https://mail.minecraft-cn.net"),
            domains=list(self.cfg.get("tempmail_domains") or []),
            proxy=proxy,
            poll_interval=float(self.cfg.get("tempmail_poll_interval") or 2),
            timeout=float(self.cfg.get("tempmail_timeout") or self.cfg.get("mail_timeout") or 120),
            list_limit=int(self.cfg.get("tempmail_list_limit") or 30),
            log=self.log,
        )

        max_attempts = max(1, int(self.cfg.get("register_max_attempts") or 3))
        last_err: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            client = None
            try:
                self.log(f"[proto] attempt {attempt}/{max_attempts} captcha={captcha_mode}")
                client = XConsoleAuthClient(
                    debug=debug,
                    proxy=proxy or None,
                    signup_url=SIGNUP_URL,
                )
                self._client = client

                self.log("[proto] visit_home + load_signup_page")
                client.visit_home()
                client.load_signup_page()

                email, token = mail.create_address()
                password = _rand_password()
                first, last = _rand_name()
                self.log(f"[proto] email={email}")
                client.create_email_validation_code(email)
                code = mail.wait_code(token, timeout=float(self.cfg.get("mail_timeout") or 150))
                self.log(f"[proto] code={code}")
                client.verify_email_validation_code(email, code)
                client.validate_password(email, password)

                sitekey = (
                    getattr(client, "turnstile_sitekey", None)
                    or getattr(xc_config, "TURNSTILE_SITEKEY", None)
                    or "0x4AAAAAAAhr9JGVDZbrZOo0"
                )
                self.log(f"[proto] solve turnstile ({captcha_mode}) sitekey={str(sitekey)[:20]}...")
                # local: no premium; pass proxy so CF token egress matches protocol IP
                turnstile = solver.solve_turnstile(
                    website_url=SIGNUP_URL,
                    website_key=str(sitekey),
                    premium=bool(self.cfg.get("yescaptcha_premium", False))
                    if captcha_mode != "local"
                    else False,
                    proxy=proxy or None,
                )
                self.log(f"[proto] turnstile len={len(turnstile or '')}")

                res = client.create_account(
                    email=email,
                    given_name=first,
                    family_name=last,
                    password=password,
                    email_validation_code=code,
                    turnstile_token=turnstile,
                    castle_request_token="",
                    conversion_id=str(uuid.uuid4()),
                )
                if not getattr(res, "ok", False):
                    raise ProtocolRegisterError(
                        f"create_account failed HTTP {getattr(res, 'http_status', '?')}"
                    )
                self.log("[proto] account created")

                sso = client.fetch_sso_token(email=email, password=password, save=False, retries=3)
                if not sso:
                    raise ProtocolRegisterError("SSO extraction failed")
                self.log(f"[proto] sso len={len(sso)}")

                out: dict[str, Any] = {
                    "email": email,
                    "password": password,
                    "sso": sso,
                    "profile": {"given_name": first, "family_name": last, "password": password},
                    "engine": "protocol",
                    "captcha": captcha_mode,
                }

                if bool(self.cfg.get("protocol_build_oauth", True)):
                    session_cookies = extract_cookies_from_auth_client(client) or {}
                    session_cookies.setdefault("sso", sso)
                    self.log("[proto] Build OAuth (protocol)…")
                    # ensure oauth path also uses local solver endpoint via env
                    if captcha_mode == "local":
                        pin_local_solver_env(local_solver_url(self.cfg))
                    oauth = login_with_protocol(
                        email,
                        password,
                        yescaptcha_key=ykey_for_oauth or "local",
                        proxy=proxy,
                        debug=debug,
                        session_cookies=session_cookies,
                        auth_client=client,
                        cliproxyapi_disabled=True,
                    )
                    access = str(getattr(oauth, "access_token", "") or "")
                    refresh = str(getattr(oauth, "refresh_token", "") or "")
                    id_token = str(getattr(oauth, "id_token", "") or "")
                    if not access:
                        raise ProtocolRegisterError("Build OAuth returned empty access_token")
                    token_dict = {
                        "access_token": access,
                        "refresh_token": refresh,
                        "id_token": id_token,
                        "token_type": "Bearer",
                        "expires_in": getattr(oauth, "expires_in", None),
                    }
                    out["build_web"] = {
                        "ok": True,
                        "code": "build_protocol_ok",
                        "token": token_dict,
                        "method": "protocol_oauth",
                    }
                    pr = probe_responses(
                        access,
                        base_url=str(self.cfg.get("probe_base_url") or "https://cli-chat-proxy.grok.com/v1"),
                        model=str(self.cfg.get("probe_model") or "grok-4.5"),
                        proxy=proxy,
                        timeout=float(self.cfg.get("probe_timeout") or 60),
                    )
                    if not pr.get("ok") and int(pr.get("status") or 0) == 403:
                        pr2 = probe_chat_completions(
                            access,
                            base_url=str(self.cfg.get("probe_base_url") or "https://cli-chat-proxy.grok.com/v1"),
                            model=str(self.cfg.get("probe_model") or "grok-4.5"),
                            proxy=proxy,
                            timeout=float(self.cfg.get("probe_timeout") or 60),
                        )
                        if pr2.get("ok"):
                            pr = pr2
                    out["probe"] = pr
                    out["web"] = {
                        "ok": True,
                        "code": "web_skipped_protocol",
                        "status": 200,
                        "method": "protocol",
                        "detail": "protocol mode skips web UI probe",
                    }
                    try:
                        rec = token_to_cpa_record(token_dict, email=email, sso=sso)
                        gdir = Path(str(self.cfg.get("g2a_auth_dir") or "g2a_auth"))
                        gdir.mkdir(parents=True, exist_ok=True)
                        safe = email.replace("@", "_")
                        path = gdir / f"xai-{safe}.json"
                        path.write_text(
                            json.dumps(rec, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8",
                        )
                        out["g2a_auth_file"] = str(path)
                        self.log(f"[proto] wrote {path}")
                    except Exception as exc:
                        self.log(f"[proto] g2a write: {exc}")

                return out
            except (TempMailError, ProtocolRegisterError) as exc:
                last_err = exc
                self.log(f"[proto] attempt failed: {exc}")
            except Exception as exc:
                last_err = ProtocolRegisterError(str(exc))
                self.log(f"[proto] attempt error: {exc}")
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass
                    if self._client is client:
                        self._client = None
            time.sleep(1.0)
        raise ProtocolRegisterError(str(last_err or "protocol register failed"))
