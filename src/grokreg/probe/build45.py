from __future__ import annotations

from typing import Any

from curl_cffi import requests

from grokreg.mint.auth_code import sso_to_token
from grokreg.util.log import LogFn, default_log

# Aligned with F:\opencode\edi\grok\check_alive.py
DEFAULT_BASE = "https://cli-chat-proxy.grok.com/v1"
DEFAULT_MODEL = "grok-4.5"
BUILD_UA = "grok-shell/0.2.99 (linux; x86_64)"
BUILD_HEADERS = {
    "X-XAI-Token-Auth": "xai-grok-cli",
    "Content-Type": "application/json",
    "x-grok-client-version": "0.2.99",
    "x-grok-client-identifier": "grok-shell",
    "x-grok-client-surface": "tui",
    "x-grok-client-name": "grok-shell",
    "User-Agent": BUILD_UA,
    "Accept": "application/json",
}

# Hard fail for upload gate (user): 403/439/429/... not alive
FAIL_STATUSES = {400, 401, 402, 403, 429, 439, 500, 502, 503}


def _extract_chat_text(body: dict[str, Any] | None, raw: str) -> str:
    if not isinstance(body, dict):
        return ""
    texts: list[str] = []
    for ch in body.get("choices") or []:
        if not isinstance(ch, dict):
            continue
        msg = ch.get("message") or {}
        if isinstance(msg, dict) and msg.get("content"):
            texts.append(str(msg["content"]))
        if ch.get("text"):
            texts.append(str(ch["text"]))
    # /v1/responses shape fallback
    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "output_text":
                    texts.append(str(c.get("text") or ""))
    return "\n".join(t for t in texts if t).strip()


def probe_chat_completions(
    access_token: str,
    *,
    base_url: str = DEFAULT_BASE,
    model: str = DEFAULT_MODEL,
    proxy: str = "",
    timeout: float = 60.0,
    fail_status_codes: list[int] | None = None,
    prompt: str = "1+1=?",
) -> dict[str, Any]:
    """Probe Build API the same way as check_alive.py (chat/completions + grok-4.5).

    Alive for upload: HTTP 200 and a real model answer (not empty / permission-denied).
    403 / 439 / 429 / 401 etc. => fail (do not upload).
    """
    fail = set(fail_status_codes or FAIL_STATUSES)
    base = base_url.rstrip("/")
    url = f"{base}/chat/completions"
    headers = {
        **BUILD_HEADERS,
        "Authorization": f"Bearer {access_token}",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 16,
        "stream": False,
    }
    kwargs: dict[str, Any] = {
        "headers": headers,
        "json": payload,
        "impersonate": "chrome",
        "timeout": timeout,
    }
    if proxy:
        kwargs["proxy"] = proxy

    try:
        resp = requests.post(url, **kwargs)
    except Exception as exc:
        return {
            "ok": False,
            "status": 0,
            "code": "probe_network",
            "error": str(exc)[:500],
            "endpoint": "chat/completions",
        }

    status = int(resp.status_code)
    body_text = (resp.text or "")[:800]
    low = body_text.lower()

    # check_alive: CF 403 sometimes treated soft-ok for listing; we fail for upload gate
    if status in fail or status >= 400:
        detail = body_text.replace("\n", " ").strip()[:400]
        if status == 429:
            code = "probe_429"
            detail = detail or "quota-exhausted"
        elif status == 403 and ("cloudflare" in low or "just a moment" in low):
            code = "probe_403_cf"
        else:
            code = f"probe_{status}"
        return {
            "ok": False,
            "status": status,
            "code": code,
            "error": detail,
            "endpoint": "chat/completions",
        }

    body: dict[str, Any] | None
    try:
        body = resp.json()
    except Exception:
        body = None

    if isinstance(body, dict) and body.get("error") and not body.get("choices"):
        return {
            "ok": False,
            "status": status,
            "code": f"probe_{status}",
            "error": str(body.get("error"))[:400],
            "endpoint": "chat/completions",
        }

    text = _extract_chat_text(body, body_text)
    if "permission-denied" in low or "access to the chat" in low:
        return {
            "ok": False,
            "status": status,
            "code": "probe_permission",
            "error": body_text[:400],
            "endpoint": "chat/completions",
        }
    if not text and not (body_text.strip() and status == 200):
        return {
            "ok": False,
            "status": status,
            "code": "probe_empty",
            "error": "empty model response",
            "endpoint": "chat/completions",
        }

    # 200 with body/answer — matches check_alive "answered"
    return {
        "ok": True,
        "status": status,
        "code": "probe_ok",
        "model": model,
        "text": (text or body_text)[:200],
        "endpoint": "chat/completions",
        "detail": "answered",
    }


# Back-compat alias
def probe_responses(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return probe_chat_completions(*args, **kwargs)


def mint_and_probe(
    sso: str,
    *,
    proxy: str = "",
    base_url: str = DEFAULT_BASE,
    model: str = DEFAULT_MODEL,
    timeout: float = 60.0,
    fail_status_codes: list[int] | None = None,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """Mint OIDC then hard-probe grok-4.5 via chat/completions (check_alive style)."""
    log = log or default_log
    result: dict[str, Any] = {"ok": False, "code": "mint_fail"}
    try:
        token = sso_to_token(sso, proxy=proxy, log=log)
    except Exception as exc:
        result["error"] = str(exc)[:500]
        result["code"] = "mint_fail"
        log(f"[probe] mint failed: {exc}")
        return result
    result["token"] = {
        "access_token": token.get("access_token"),
        "refresh_token": token.get("refresh_token"),
        "expires_in": token.get("expires_in"),
    }
    pr = probe_chat_completions(
        token["access_token"],
        base_url=base_url,
        model=model,
        proxy=proxy,
        timeout=timeout,
        fail_status_codes=fail_status_codes,
    )
    result.update(pr)
    if pr.get("ok"):
        log(f"[probe] OK status={pr.get('status')} text={pr.get('text')!r}")
    else:
        log(
            f"[probe] FAIL code={pr.get('code')} status={pr.get('status')} "
            f"err={pr.get('error')}"
        )
    return result
