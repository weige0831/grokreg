from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "email_provider": "tempmail",
    "tempmail_base_url": "https://mail.minecraft-cn.net",
    "tempmail_domains": ["mtoosov.shop", "olsbvgq.shop", "htazmbb.shop"],
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
    "probe_required": True,
    "probe_base_url": "https://cli-chat-proxy.grok.com/v1",
    "probe_model": "grok-4.5",
    "probe_timeout": 60,
    "probe_fail_status_codes": [401, 402, 403, 429, 439, 500, 502, 503],
    "grok2api_auto_add_remote": True,
    "grok2api_remote_base": "",
    "grok2api_remote_app_key": "",
    "grok2api_pool_name": "Build",
    "grok2api_pool_api_value": "build",
    "grok2api_upload_only_after_probe": True,
    "grok2api_upload_sync": True,
    "grok2api_auto_nsfw": True,
}


def _env_override(cfg: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "PROXY_URL": "proxy",
        "GROK2API_REMOTE_BASE": "grok2api_remote_base",
        "GROK2API_APP_KEY": "grok2api_remote_app_key",
        "GROK2API_POOL": "grok2api_pool_name",
        "TEMPMAIL_BASE_URL": "tempmail_base_url",
    }
    for env_k, cfg_k in mapping.items():
        v = os.environ.get(env_k, "").strip()
        if v:
            cfg[cfg_k] = v
    # pool api value: if only pool name set via env, keep separate unless GROK2API_POOL_API set
    api_pool = os.environ.get("GROK2API_POOL_API", "").strip()
    if api_pool:
        cfg["grok2api_pool_api_value"] = api_pool
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
