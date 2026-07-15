from __future__ import annotations

import json
from typing import Any

from curl_cffi import requests

from grokreg.mint.auth_code import normalize_sso
from grokreg.util.log import LogFn, default_log


def probe_web_default_model(
    sso: str,
    *,
    proxy: str = "",
    cf_clearance: str = "",
    user_agent: str = "",
    model: str = "grok-3",
    prompt: str = "1+1=? Reply with one number only.",
    timeout: float = 45.0,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """HTTP fallback: request grok.com web default model with SSO cookie."""
    log = log or default_log
    sso = normalize_sso(sso)
    if not sso:
        return {"ok": False, "code": "web_no_sso", "status": 0, "error": "empty sso"}

    ua = user_agent or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    )
    cookie_parts = [f"sso={sso}", f"sso-rw={sso}"]
    if cf_clearance:
        cookie_parts.append(f"cf_clearance={cf_clearance}")
    headers = {
        "user-agent": ua,
        "cookie": "; ".join(cookie_parts),
        "content-type": "application/json",
        "origin": "https://grok.com",
        "referer": "https://grok.com/",
        "accept": "application/json, text/plain, */*",
    }
    bodies = [
        {
            "temporary": True,
            "modelName": model,
            "message": prompt,
            "fileAttachments": [],
            "imageAttachments": [],
            "disableSearch": True,
            "enableImageGeneration": False,
            "returnImageBytes": False,
            "forceConcise": True,
            "toolOverrides": {},
            "enableSideBySide": False,
            "isPreset": False,
            "sendFinalMetadata": True,
        },
        {"temporary": True, "model": model, "message": prompt},
    ]
    url = "https://grok.com/rest/app-chat/conversations/new"
    last: dict[str, Any] = {"ok": False, "code": "web_fail", "status": 0}
    for body in bodies:
        try:
            kwargs: dict[str, Any] = {
                "headers": headers,
                "json": body,
                "impersonate": "chrome",
                "timeout": timeout,
            }
            if proxy:
                kwargs["proxy"] = proxy
            resp = requests.post(url, **kwargs)
        except Exception as exc:
            last = {"ok": False, "code": "web_network", "status": 0, "error": str(exc)[:300]}
            continue
        text = (resp.text or "")[:800]
        status = int(resp.status_code)
        low = text.lower()
        if status in (403, 429, 503) and ("cloudflare" in low or "just a moment" in low):
            last = {
                "ok": False,
                "code": "web_cf_block",
                "status": status,
                "error": text[:300],
                "endpoint": url,
                "model": model,
            }
            continue
        if status >= 400:
            last = {
                "ok": False,
                "code": f"web_{status}",
                "status": status,
                "error": text[:300],
                "endpoint": url,
                "model": model,
            }
            continue
        if "permission-denied" in low or "access denied" in low:
            last = {
                "ok": False,
                "code": "web_permission",
                "status": status,
                "error": text[:300],
                "endpoint": url,
                "model": model,
            }
            continue
        # success heuristic: 2xx with body content
        if text.strip():
            log(f"[web] HTTP default model ok status={status} model={model}")
            return {
                "ok": True,
                "code": "web_ok",
                "status": status,
                "text": text[:400],
                "endpoint": url,
                "model": model,
            }
        last = {
            "ok": False,
            "code": "web_empty",
            "status": status,
            "error": "empty body",
            "endpoint": url,
            "model": model,
        }
    log(f"[web] HTTP default model fail code={last.get('code')} status={last.get('status')}")
    return last
