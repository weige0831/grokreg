from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.parse
from typing import Any, Callable

from curl_cffi import requests

from grokreg.util.log import LogFn, default_log

CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
OIDC_ISSUER = "https://auth.x.ai"
SCOPES = (
    "openid profile email offline_access grok-cli:access "
    "api:access conversations:read conversations:write"
)
REDIRECT_URI = "http://127.0.0.1:56121/callback"
GROK_REFERRER = "grok-build"
GROK_PLAN = "generic"
# Align client headers with check_alive / grok-shell build channel
GROK_VERSION = "0.2.99"
GROK_TOKEN_UA = f"grok-shell/{GROK_VERSION} (linux; x86_64)"
NEXT_ACTION_ID = "4005315a1d7e426de592990bb54bb37471f39dd6d2"

CPA_GROK_BASE_URL = "https://cli-chat-proxy.grok.com/v1"
CPA_TOKEN_ENDPOINT = f"{OIDC_ISSUER}/oauth2/token"
CPA_GROK_HEADERS = {
    "User-Agent": GROK_TOKEN_UA,
    "X-XAI-Token-Auth": "xai-grok-cli",
    "x-grok-client-version": GROK_VERSION,
    "x-grok-client-identifier": "grok-shell",
    "x-grok-client-surface": "tui",
    "x-grok-client-name": "grok-shell",
}


class MintError(RuntimeError):
    pass


def decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        seg = token.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(seg))
    except Exception:
        return {}


def _gen_pkce() -> tuple[str, str, str, str]:
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()
    nonce = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()
    return verifier, challenge, state, nonce


def _parse_consent_code(body: str) -> str | None:
    for line in body.split("\n"):
        start = line.find("{")
        if start < 0:
            continue
        try:
            data = json.loads(line[start:])
        except Exception:
            continue
        if isinstance(data, dict) and data.get("code"):
            if data.get("success") is False:
                return None
            return str(data.get("code"))
    return None


def normalize_sso(sso: str) -> str:
    token = str(sso or "").strip()
    if token.startswith("sso="):
        token = token[4:]
    return token.strip()


def sso_to_token(
    sso_cookie: str,
    *,
    proxy: str = "",
    log: LogFn | None = None,
    next_action_id: str = NEXT_ACTION_ID,
) -> dict[str, Any]:
    """SSO cookie → OAuth token dict via Authorization Code + PKCE (referrer=grok-build)."""
    log = log or default_log
    sso = normalize_sso(sso_cookie)
    if not sso:
        raise MintError("empty sso")

    proxies = {"http": proxy, "https": proxy} if proxy else None
    s = requests.Session()
    if proxies:
        s.proxies = proxies
    for domain in (".x.ai", "accounts.x.ai", "auth.x.ai"):
        s.cookies.set("sso", sso, domain=domain)
        s.cookies.set("sso-rw", sso, domain=domain)

    try:
        r = s.get("https://accounts.x.ai/", impersonate="chrome", timeout=20)
    except Exception as exc:
        raise MintError(f"network: {exc}") from exc
    if "sign-in" in r.url or "sign-up" in r.url:
        raise MintError("sso invalid (redirected to sign-in)")
    log("[mint] sso valid")

    verifier, challenge, state, nonce = _gen_pkce()
    authorize_params = urllib.parse.urlencode(
        {
            "client_id": CLIENT_ID,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "nonce": nonce,
            "plan": GROK_PLAN,
            "redirect_uri": REDIRECT_URI,
            "referrer": GROK_REFERRER,
            "response_type": "code",
            "scope": SCOPES,
            "state": state,
        }
    )
    log(f"[mint] authorize referrer={GROK_REFERRER}")
    try:
        r = s.get(
            f"{OIDC_ISSUER}/oauth2/authorize?{authorize_params}",
            impersonate="chrome",
            timeout=20,
            allow_redirects=True,
        )
    except Exception as exc:
        raise MintError(f"authorize: {exc}") from exc
    final_url = str(r.url)
    if "sign-in" in final_url or "sign-up" in final_url:
        raise MintError("sso invalid during authorize")
    if "/oauth2/consent" not in final_url:
        raise MintError(f"authorize did not reach consent: {final_url}")

    consent_payload = json.dumps(
        [
            {
                "action": "allow",
                "clientId": CLIENT_ID,
                "redirectUri": REDIRECT_URI,
                "scope": SCOPES,
                "state": state,
                "codeChallenge": challenge,
                "codeChallengeMethod": "S256",
                "nonce": nonce,
                "principalType": "User",
                "principalId": "",
                "referrer": GROK_REFERRER,
            }
        ]
    )
    try:
        r = s.post(
            final_url,
            data=consent_payload,
            headers={
                "Content-Type": "text/plain;charset=UTF-8",
                "Accept": "text/x-component",
                "Origin": "https://accounts.x.ai",
                "Referer": final_url,
                "Next-Action": next_action_id or NEXT_ACTION_ID,
            },
            impersonate="chrome",
            timeout=20,
            allow_redirects=True,
        )
    except Exception as exc:
        raise MintError(f"consent: {exc}") from exc
    if r.status_code < 200 or r.status_code >= 300:
        raise MintError(f"consent HTTP {r.status_code}: {str(r.text)[:200]}")
    code = _parse_consent_code(str(r.text))
    if not code:
        raise MintError(f"consent no code: {str(r.text)[:200]}")
    log("[mint] consent ok")

    token_data = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "code_verifier": verifier,
        }
    )
    try:
        r = s.post(
            f"{OIDC_ISSUER}/oauth2/token",
            data=token_data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": GROK_TOKEN_UA,
                "X-Grok-Client-Version": GROK_VERSION,
                "Accept": "*/*",
            },
            impersonate="chrome",
            timeout=20,
        )
    except Exception as exc:
        raise MintError(f"token: {exc}") from exc
    if r.status_code < 200 or r.status_code >= 300:
        raise MintError(f"token HTTP {r.status_code}: {str(r.text)[:200]}")
    try:
        token = r.json()
    except Exception as exc:
        raise MintError(f"token non-json: {str(r.text)[:200]}") from exc
    if not token.get("access_token"):
        raise MintError(f"token missing access_token: {token}")
    token.setdefault("expires_in", 21600)
    token.setdefault("token_type", "Bearer")

    ap = decode_jwt_payload(token["access_token"])
    ref = ap.get("referrer")
    if ref != GROK_REFERRER:
        raise MintError(f"access_token referrer={ref!r}, expected {GROK_REFERRER!r}")
    log(f"[mint] token ok referrer={ref!r}")
    return token


def token_to_cpa_record(token: dict[str, Any], email: str = "", sso: str = "") -> dict[str, Any]:
    from datetime import datetime, timezone

    access = token.get("access_token") or ""
    refresh = token.get("refresh_token") or ""
    payload = decode_jwt_payload(access)
    if not email:
        email = str(payload.get("email") or "")
    sub = str(payload.get("sub") or "")
    expired = ""
    if "exp" in payload:
        expired = datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    record = {
        "type": "xai",
        "auth_kind": "oauth",
        "email": email or "",
        "sub": sub,
        "access_token": access,
        "refresh_token": refresh,
        "id_token": token.get("id_token") or "",
        "token_type": token.get("token_type", "Bearer"),
        "expires_in": token.get("expires_in"),
        "expired": expired,
        "last_refresh": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "redirect_uri": REDIRECT_URI,
        "token_endpoint": CPA_TOKEN_ENDPOINT,
        "base_url": CPA_GROK_BASE_URL,
        "disabled": False,
        "headers": dict(CPA_GROK_HEADERS),
    }
    if sso:
        record["sso"] = normalize_sso(sso)
    return record
