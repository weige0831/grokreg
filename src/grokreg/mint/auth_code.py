from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from curl_cffi import requests

from grokreg.util.log import LogFn, default_log

# Strict align with Git-creat7/grokRegister-cpa/sso_to_auth_json.py
CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
OIDC_ISSUER = "https://auth.x.ai"
SCOPES = (
    "openid profile email offline_access grok-cli:access "
    "api:access conversations:read conversations:write"
)
REDIRECT_URI = "http://127.0.0.1:56121/callback"
GROK_REFERRER = "grok-build"
GROK_PLAN = "generic"
GROK_VERSION = "0.2.93"
GROK_TOKEN_UA = f"grok-pager/{GROK_VERSION} grok-shell/{GROK_VERSION} (linux; x86_64)"
NEXT_ACTION_ID = "4005315a1d7e426de592990bb54bb37471f39dd6d2"

CPA_TOKEN_ENDPOINT = f"{OIDC_ISSUER}/oauth2/token"
CPA_GROK_BASE_URL = "https://cli-chat-proxy.grok.com/v1"
# sample CPA_GROK_HEADERS
CPA_GROK_HEADERS = {
    "User-Agent": GROK_TOKEN_UA,
    "X-XAI-Token-Auth": "xai-grok-cli",
    "x-authenticateresponse": "authenticate-response",
    "x-grok-client-identifier": "grok-pager",
    "x-grok-client-version": GROK_VERSION,
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
    """SSO → OAuth token via Authorization Code + PKCE (sample sso_to_token).

    authorize injects referrer=grok-build + plan=generic;
    consent also sends referrer=grok-build (sample actual code).
    """
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
    log(f"[mint] authorize referrer={GROK_REFERRER} plan={GROK_PLAN}")
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

    # sample: consent also must carry referrer=grok-build
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
    log("[mint] consent ok (Build authorize allow)")

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
    log(
        f"[mint] token ok referrer={ref!r} bot_flag={ap.get('bot_flag_source')} "
        f"scope={ap.get('scope')!r}"
    )
    return token


def token_to_cpa_record(token: dict[str, Any], email: str = "", sso: str = "") -> dict[str, Any]:
    access = token.get("access_token") or ""
    refresh = token.get("refresh_token") or ""
    id_token = token.get("id_token") or ""
    payload = decode_jwt_payload(access)
    id_payload = decode_jwt_payload(id_token) if id_token else {}
    if not email:
        email = str(id_payload.get("email") or payload.get("email") or "")
    sub = str(payload.get("sub") or id_payload.get("sub") or "")
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
        "id_token": id_token,
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


def _response_preview(res, limit: int = 200) -> str:
    try:
        text = str(getattr(res, "text", "") or "")
        return " ".join(text.split())[:limit]
    except Exception:
        return ""


def _is_cf_block(res) -> bool:
    try:
        headers = {str(k).lower(): str(v).lower() for k, v in dict(res.headers).items()}
        text = str(res.text or "").lower()
        server = headers.get("server", "")
        content_type = headers.get("content-type", "")
        return res.status_code in (403, 429, 503) and (
            "cloudflare" in server
            or "cloudflare" in text
            or "cf-error" in text
            or "__cf_chl" in text
            or "just a moment" in text
            or "text/html" in content_type
        )
    except Exception:
        return False


def activate_account_for_build(
    sso_cookie: str,
    *,
    proxy: str = "",
    cf_clearance: str = "",
    user_agent: str = "",
    log: LogFn | None = None,
) -> dict[str, Any]:
    """sample enable_nsfw_for_token path: TOS + birth-date (+ optional NSFW).

    New accounts often need TOS/birth before Build chat works.
    Uses SSO cookie (+ browser cf_clearance when available).
    """
    import random

    log = log or default_log
    sso = normalize_sso(sso_cookie)
    if not sso:
        return {"ok": False, "error": "empty sso"}

    ua = (
        user_agent
        or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    )
    proxies = {"http": proxy, "https": proxy} if proxy else None
    out: dict[str, Any] = {"ok": False, "tos": "", "birth": "", "nsfw": ""}

    try:
        with requests.Session(impersonate="chrome120") as session:
            if proxies:
                session.proxies = proxies
            cookie_parts = [f"sso={sso}", f"sso-rw={sso}"]
            if cf_clearance:
                cookie_parts.append(f"cf_clearance={cf_clearance}")
            session.headers.update(
                {
                    "user-agent": ua,
                    "cookie": "; ".join(cookie_parts),
                }
            )

            # 1) TOS — sample SetTosAcceptedVersion grpc-web
            tos_url = "https://accounts.x.ai/auth_mgmt.AuthManagement/SetTosAcceptedVersion"
            payload = struct.pack("B", (2 << 3) | 0) + struct.pack("B", 1)
            data = b"\x00" + struct.pack(">I", len(payload)) + payload
            try:
                res = session.post(
                    tos_url,
                    data=data,
                    headers={
                        "content-type": "application/grpc-web+proto",
                        "x-grpc-web": "1",
                        "x-user-agent": "connect-es/2.1.1",
                        "origin": "https://accounts.x.ai",
                        "referer": "https://accounts.x.ai/accept-tos",
                    },
                    timeout=15,
                )
                if 200 <= res.status_code < 300:
                    out["tos"] = "ok"
                elif _is_cf_block(res):
                    out["tos"] = f"cf_block_{res.status_code}"
                else:
                    out["tos"] = f"http_{res.status_code}:{_response_preview(res)}"
            except Exception as exc:
                out["tos"] = f"err:{exc}"

            # 2) birth date — sample grok.com/rest/auth/set-birth-date
            today = datetime.now(timezone.utc).date()
            age = random.randint(20, 40)
            birth = f"{today.year - age}-{random.randint(1,12):02d}-{random.randint(1,28):02d}T16:00:00.000Z"
            try:
                res = session.post(
                    "https://grok.com/rest/auth/set-birth-date",
                    json={"birthDate": birth},
                    headers={
                        "content-type": "application/json",
                        "origin": "https://grok.com",
                        "referer": "https://grok.com/",
                    },
                    timeout=15,
                )
                text = str(res.text or "")
                if 200 <= res.status_code < 300:
                    out["birth"] = "ok"
                elif res.status_code in (400, 409, 429) and (
                    "birth-date-change-limit-reached" in text
                    or "Birth date is locked" in text
                    or "already set" in text.lower()
                ):
                    out["birth"] = "already_set"
                elif _is_cf_block(res):
                    out["birth"] = f"cf_block_{res.status_code}"
                else:
                    out["birth"] = f"http_{res.status_code}:{_response_preview(res)}"
            except Exception as exc:
                out["birth"] = f"err:{exc}"

            # 3) optional NSFW feature flag (sample)
            try:
                field1_content = bytes([0x10, 0x01])
                field1 = bytes([0x0A, len(field1_content)]) + field1_content
                nsfw_string = b"always_show_nsfw_content"
                field2_inner = bytes([0x0A, len(nsfw_string)]) + nsfw_string
                field2 = bytes([0x12, len(field2_inner)]) + field2_inner
                nsfw_payload = field1 + field2
                nsfw_data = b"\x00" + struct.pack(">I", len(nsfw_payload)) + nsfw_payload
                res = session.post(
                    "https://grok.com/auth_mgmt.AuthManagement/UpdateUserFeatureControls",
                    data=nsfw_data,
                    headers={
                        "content-type": "application/grpc-web+proto",
                        "x-grpc-web": "1",
                        "origin": "https://grok.com",
                        "referer": "https://grok.com/",
                    },
                    timeout=15,
                )
                if 200 <= res.status_code < 300:
                    out["nsfw"] = "ok"
                elif _is_cf_block(res):
                    out["nsfw"] = f"cf_block_{res.status_code}"
                else:
                    out["nsfw"] = f"http_{res.status_code}"
            except Exception as exc:
                out["nsfw"] = f"err:{exc}"

    except Exception as exc:
        out["error"] = str(exc)[:300]
        log(f"[activate] fail: {exc}")
        return out

    out["ok"] = out.get("tos") in ("ok",) or out.get("birth") in ("ok", "already_set")
    log(f"[activate] tos={out.get('tos')} birth={out.get('birth')} nsfw={out.get('nsfw')}")
    return out
