from __future__ import annotations

import time
from typing import Any

from curl_cffi import requests

from grokreg.mint.auth_code import (
    CPA_GROK_BASE_URL,
    CPA_GROK_HEADERS,
    activate_account_for_build,
    decode_jwt_payload,
    sso_to_token,
)
from grokreg.util.log import LogFn, default_log

# sample probe: POST /v1/responses with CPA_GROK_HEADERS
DEFAULT_BASE = CPA_GROK_BASE_URL
DEFAULT_MODEL = "grok-4.5"
FAIL_STATUSES = {400, 401, 402, 403, 429, 439, 500, 502, 503}


def is_transient_probe_denial(result: dict[str, Any]) -> bool:
    """True when brand-new OIDC tokens still settle (edi/grokv2 register_lite_store)."""
    code = int(result.get("status") or result.get("status_code") or 0)
    err = str(result.get("error") or "").lower()
    if code == 403 and (
        "permission-denied" in err
        or "access to the chat endpoint is denied" in err
        or "permissiondenied" in err
    ):
        return True
    if code == 401 and "no auth context" in err:
        return True
    return False


def probe_with_settle_retry(
    access_token: str,
    *,
    base_url: str = DEFAULT_BASE,
    model: str = DEFAULT_MODEL,
    proxy: str = "",
    timeout: float = 60.0,
    fail_status_codes: list[int] | None = None,
    endpoint: str = "responses",
    retries: int = 3,
    retry_delay_sec: float = 8.0,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """Probe Build API; retry transient permission-denied while token settles."""
    log = log or default_log
    attempts = max(1, int(retries or 1))
    delay = max(0.0, float(retry_delay_sec or 0.0))
    pr: dict[str, Any] = {"ok": False, "code": "probe_fail"}
    for attempt in range(1, attempts + 1):
        if endpoint == "chat/completions":
            pr = probe_chat_completions(
                access_token,
                base_url=base_url,
                model=model,
                proxy=proxy,
                timeout=timeout,
                fail_status_codes=fail_status_codes,
            )
        else:
            pr = probe_responses(
                access_token,
                base_url=base_url,
                model=model,
                proxy=proxy,
                timeout=timeout,
                fail_status_codes=fail_status_codes,
            )
            if not pr.get("ok") and int(pr.get("status") or 0) == 403:
                pr2 = probe_chat_completions(
                    access_token,
                    base_url=base_url,
                    model=model,
                    proxy=proxy,
                    timeout=timeout,
                    fail_status_codes=fail_status_codes,
                )
                if pr2.get("ok"):
                    pr = pr2
        pr["attempt"] = attempt
        if pr.get("ok"):
            return pr
        if attempt < attempts and is_transient_probe_denial(pr):
            wait = delay * attempt
            log(
                f"[probe] transient denial attempt {attempt}/{attempts}, "
                f"wait {wait:.0f}s then retry"
            )
            time.sleep(wait)
            continue
        break
    return pr


def _extract_text(body: dict[str, Any] | None, raw: str) -> str:
    if not isinstance(body, dict):
        return (raw or "")[:200]
    texts: list[str] = []
    # /v1/responses
    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "output_text":
                    texts.append(str(c.get("text") or ""))
        if item.get("text"):
            texts.append(str(item["text"]))
    # chat/completions fallback
    for ch in body.get("choices") or []:
        if not isinstance(ch, dict):
            continue
        msg = ch.get("message") or {}
        if isinstance(msg, dict) and msg.get("content"):
            texts.append(str(msg["content"]))
        if ch.get("text"):
            texts.append(str(ch["text"]))
    if body.get("output_text"):
        texts.append(str(body["output_text"]))
    return "\n".join(t for t in texts if t).strip()


def probe_responses(
    access_token: str,
    *,
    base_url: str = DEFAULT_BASE,
    model: str = DEFAULT_MODEL,
    proxy: str = "",
    timeout: float = 60.0,
    fail_status_codes: list[int] | None = None,
    prompt: str = "ping",
) -> dict[str, Any]:
    """sample probe_cpa_record: POST /v1/responses."""
    fail = set(fail_status_codes or FAIL_STATUSES)
    base = base_url.rstrip("/")
    url = f"{base}/responses"
    headers = {
        **CPA_GROK_HEADERS,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "input": prompt,
        "max_output_tokens": 16,
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
            "endpoint": "responses",
        }

    status = int(resp.status_code)
    body_text = (resp.text or "")[:800]
    low = body_text.lower()
    if status in fail or status >= 400:
        detail = body_text.replace("\n", " ").strip()[:400]
        if status == 429:
            code = "probe_429"
        elif status == 403 and ("cloudflare" in low or "just a moment" in low):
            code = "probe_403_cf"
        else:
            code = f"probe_{status}"
        return {
            "ok": False,
            "status": status,
            "code": code,
            "error": detail,
            "endpoint": "responses",
        }

    try:
        body = resp.json()
    except Exception:
        body = None
    if isinstance(body, dict) and body.get("error") and not body.get("output") and not body.get("choices"):
        return {
            "ok": False,
            "status": status,
            "code": f"probe_{status}",
            "error": str(body.get("error"))[:400],
            "endpoint": "responses",
        }
    text = _extract_text(body if isinstance(body, dict) else None, body_text)
    if "permission-denied" in low or "access to the chat" in low:
        return {
            "ok": False,
            "status": status,
            "code": "probe_permission",
            "error": body_text[:400],
            "endpoint": "responses",
        }
    return {
        "ok": True,
        "status": status,
        "code": "probe_ok",
        "model": model,
        "text": (text or body_text)[:200],
        "endpoint": "responses",
        "detail": "answered",
    }


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
    """Fallback chat/completions with same CPA headers."""
    fail = set(fail_status_codes or FAIL_STATUSES)
    base = base_url.rstrip("/")
    url = f"{base}/chat/completions"
    headers = {
        **CPA_GROK_HEADERS,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
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
    if status in fail or status >= 400:
        return {
            "ok": False,
            "status": status,
            "code": f"probe_{status}",
            "error": body_text.replace("\n", " ").strip()[:400],
            "endpoint": "chat/completions",
        }
    if "permission-denied" in low:
        return {
            "ok": False,
            "status": status,
            "code": "probe_permission",
            "error": body_text[:400],
            "endpoint": "chat/completions",
        }
    try:
        body = resp.json()
    except Exception:
        body = None
    text = _extract_text(body if isinstance(body, dict) else None, body_text)
    return {
        "ok": True,
        "status": status,
        "code": "probe_ok",
        "model": model,
        "text": (text or body_text)[:200],
        "endpoint": "chat/completions",
        "detail": "answered",
    }


def mint_and_probe(
    sso: str,
    *,
    proxy: str = "",
    base_url: str = DEFAULT_BASE,
    model: str = DEFAULT_MODEL,
    timeout: float = 60.0,
    fail_status_codes: list[int] | None = None,
    log: LogFn | None = None,
    cf_clearance: str = "",
    user_agent: str = "",
    activate: bool = True,
    endpoint: str = "responses",
) -> dict[str, Any]:
    """Activate (TOS/birth) → mint Build OAuth → probe /v1/responses (sample)."""
    log = log or default_log
    result: dict[str, Any] = {"ok": False, "code": "mint_fail"}

    if activate:
        act = activate_account_for_build(
            sso,
            proxy=proxy,
            cf_clearance=cf_clearance,
            user_agent=user_agent,
            log=log,
        )
        result["activate"] = act

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
    claims = decode_jwt_payload(str(token.get("access_token") or ""))
    result["bot_flag"] = claims.get("bot_flag_source")
    result["referrer"] = claims.get("referrer")

    # sample primary: /v1/responses; fallback chat/completions + settle retry
    pr = probe_with_settle_retry(
        token["access_token"],
        base_url=base_url,
        model=model,
        proxy=proxy,
        timeout=timeout,
        fail_status_codes=fail_status_codes,
        endpoint=endpoint,
        retries=3,
        retry_delay_sec=8.0,
        log=log,
    )

    result.update(pr)

    def _safe(s: Any, n: int = 200) -> str:
        t = str(s or "")
        try:
            t.encode("gbk", errors="strict")
        except Exception:
            t = t.encode("ascii", errors="replace").decode("ascii")
        return t[:n]

    if pr.get("ok"):
        log(
            f"[probe] OK endpoint={pr.get('endpoint')} status={pr.get('status')} "
            f"bot_flag={result.get('bot_flag')} text={_safe(pr.get('text'))!r}"
        )
    else:
        log(
            f"[probe] FAIL code={pr.get('code')} status={pr.get('status')} "
            f"endpoint={pr.get('endpoint')} bot_flag={result.get('bot_flag')} "
            f"err={_safe(pr.get('error'))}"
        )
        if int(pr.get("status") or 0) == 403:
            result["usable"] = False
            result["code"] = "probe_403_unusable"
    return result
