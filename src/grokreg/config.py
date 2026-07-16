from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "email_provider": "tempmail",
    "tempmail_base_url": "https://mail.minecraft-cn.net",
    "tempmail_domains": [
        "mtoosov.shop",
        "olsbvgq.shop",
        "htazmbb.shop",
        "cabuhu.cn",
        "pfdszfg.shop",
        "tnfolpr.shop",
        "xiiktcx.cn",
    ],
    "tempmail_poll_interval": 2,
    "tempmail_timeout": 120,
    "tempmail_list_limit": 30,
    "proxy": "",
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    ),
    "register_count": 1,
    "register_threads": 1,
    "headless": False,
    "browser_path": "",
    "accounts_file": "accounts.txt",
    "results_file": "results.jsonl",
    "register_max_attempts": 3,
    "mail_timeout": 150,
    "sso_timeout": 240,
    "turnstile_stuck_timeout": 150,
    "mint_proxy": "",
    "browser_engine": "drission",
    # protocol mode (HTTP signup + Build OAuth, based on xconsole_client)
    "yescaptcha_api_key": "",
    "yescaptcha_premium": True,
    "protocol_build_oauth": True,
    "protocol_debug": False,
    "g2a_auth_dir": "g2a_auth",
    "browser_use_proxy": False,
    "browser_proxy": "",
    "browser_set_user_agent": False,
    "stealth_inject": False,
    "network_diag_hook": False,
    "cloak_humanize": True,
    "cloak_geoip": False,
    "cloak_stealth_args": True,
    "cloak_human_preset": "default",
    "cloak_license_key": "",
    "turnstile_extension": True,
    "probe_required": True,
    "probe_base_url": "https://cli-chat-proxy.grok.com/v1",
    "probe_model": "grok-4.5",
    "probe_timeout": 60,
    "probe_endpoint": "responses",
    "probe_fail_status_codes": [401, 402, 403, 429, 439, 500, 502, 503],
    "web_default_model": "default-ui",
    "web_probe_prompt": "你好",
    "web_probe_timeout": 60,
    "require_web_before_build": True,
    "build_authorize_delay_sec": 3,
    "browser_build_authorize": True,
    "build_browser_timeout": 90,
    "activate_before_probe": True,
    # Drission-only default; Cloak post path optional
    "post_register_cloak": False,
    "require_cloak_web": False,
    "cloak_web_dwell_sec": 2,
    "cloak_login_settle_sec": 3,
    "cloak_use_proxy": False,
    "cloak_keep_open": False,
    "grok2api_auto_add_remote": False,
    "grok2api_remote_base": "",
    "grok2api_remote_app_key": "",
    "grok2api_pool_name": "Build",
    "grok2api_pool_api_value": "build",
    "grok2api_upload_only_after_probe": True,
    "grok2api_upload_sync": True,
    "grok2api_auto_nsfw": True,
}


def _parse_domains(raw: str) -> list[str]:
    """Parse comma/space/semicolon separated domain list."""
    out: list[str] = []
    for part in str(raw or "").replace(";", ",").replace("\n", ",").split(","):
        for bit in part.split():
            d = bit.strip().lower().lstrip("@")
            if d and d not in out:
                out.append(d)
    return out


def _env_override(cfg: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "PROXY_URL": "proxy",
        "GROK2API_REMOTE_BASE": "grok2api_remote_base",
        "GROK2API_APP_KEY": "grok2api_remote_app_key",
        "GROK2API_POOL": "grok2api_pool_name",
        "TEMPMAIL_BASE_URL": "tempmail_base_url",
        "YESCAPTCHA_API_KEY": "yescaptcha_api_key",
    }
    for env_k, cfg_k in mapping.items():
        v = os.environ.get(env_k, "").strip()
        if v:
            cfg[cfg_k] = v
    # pool api value: if only pool name set via env, keep separate unless GROK2API_POOL_API set
    api_pool = os.environ.get("GROK2API_POOL_API", "").strip()
    if api_pool:
        cfg["grok2api_pool_api_value"] = api_pool
    # custom tempmail domains: TEMPMAIL_DOMAINS=a.com,b.com
    dom_raw = os.environ.get("TEMPMAIL_DOMAINS", "").strip()
    if dom_raw:
        domains = _parse_domains(dom_raw)
        if domains:
            cfg["tempmail_domains"] = domains
    return cfg


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    else:
        candidates.append(Path("config.json"))
        candidates.append(Path(__file__).resolve().parents[2] / "config.json")
    for p in candidates:
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    if str(k).startswith("//") or str(k).startswith("#"):
                        continue
                    cfg[k] = v
            break
    return _env_override(cfg)


def resolve_proxy(cfg: dict[str, Any], *, mint: bool = False) -> str:
    if mint:
        p = str(cfg.get("mint_proxy") or "").strip()
        if p:
            return p
    return str(cfg.get("proxy") or os.environ.get("PROXY_URL") or "").strip()
