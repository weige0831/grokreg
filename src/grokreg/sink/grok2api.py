from __future__ import annotations

from typing import Any

from curl_cffi import requests

from grokreg.mint.auth_code import normalize_sso
from grokreg.util.log import LogFn, default_log


class Grok2ApiError(RuntimeError):
    pass


def resolve_pool_api_value(cfg: dict[str, Any]) -> str:
    """Map config pool name to API pool field. Never silently fall back to basic."""
    explicit = str(cfg.get("grok2api_pool_api_value") or "").strip()
    if explicit:
        return explicit
    name = str(cfg.get("grok2api_pool_name") or "Build").strip()
    # common aliases
    lower = name.lower()
    if lower in {"build", "ssobuild", "sso_build"}:
        return "build"
    if lower in {"ssobasic", "basic"}:
        return "basic"
    if lower in {"ssosuper", "super"}:
        return "super"
    # pass through as-is (case-sensitive for custom pools)
    return name


def upload_sso_to_build(
    sso: str,
    *,
    base: str,
    app_key: str,
    pool: str = "build",
    email: str = "",
    tags: list[str] | None = None,
    auto_nsfw: bool = True,
    proxy: str = "",
    timeout: float = 20.0,
    log: LogFn | None = None,
) -> dict[str, Any]:
    log = log or default_log
    token = normalize_sso(sso)
    base = str(base or "").strip().rstrip("/")
    app_key = str(app_key or "").strip()
    if not token:
        raise Grok2ApiError("empty sso")
    if not base:
        raise Grok2ApiError("grok2api_remote_base empty")
    if not app_key:
        raise Grok2ApiError("grok2api_remote_app_key empty")

    headers = {"Content-Type": "application/json"}
    query = {"app_key": app_key}
    if auto_nsfw:
        query["auto_nsfw"] = "true"
    tag_list = list(tags or ["auto-register", "probe-ok"])
    if email:
        tag_list = list(tag_list) + [f"email:{email}"]
    payload = {
        "tokens": [token],
        "pool": pool,
        "tags": tag_list,
    }

    kwargs: dict[str, Any] = {
        "headers": headers,
        "params": query,
        "json": payload,
        "impersonate": "chrome",
        "timeout": timeout,
    }
    if proxy:
        kwargs["proxy"] = proxy

    url = f"{base}/tokens/add"
    log(f"[g2a] POST {url} pool={pool}")
    try:
        resp = requests.post(url, **kwargs)
    except Exception as exc:
        raise Grok2ApiError(f"network: {exc}") from exc

    if resp.status_code >= 400:
        # fallback full replace for older servers
        log(f"[g2a] /tokens/add HTTP {resp.status_code}, try /tokens fallback")
        return _upload_full_replace(
            token,
            base=base,
            app_key=app_key,
            pool_name=pool,
            email=email,
            tags=tags or ["auto-register", "probe-ok"],
            auto_nsfw=auto_nsfw,
            proxy=proxy,
            timeout=timeout,
            log=log,
        )

    log(f"[g2a] upload ok pool={pool} email={email}")
    return {"ok": True, "code": "upload_ok", "pool": pool, "status": resp.status_code}


def _upload_full_replace(
    token: str,
    *,
    base: str,
    app_key: str,
    pool_name: str,
    email: str,
    tags: list[str],
    auto_nsfw: bool,
    proxy: str,
    timeout: float,
    log: LogFn,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    query = {"app_key": app_key}
    if auto_nsfw:
        query["auto_nsfw"] = "true"
    kwargs: dict[str, Any] = {
        "headers": headers,
        "params": query,
        "impersonate": "chrome",
        "timeout": timeout,
    }
    if proxy:
        kwargs["proxy"] = proxy

    current: dict[str, Any] = {}
    try:
        r = requests.get(f"{base}/tokens", **kwargs)
        if r.status_code == 200:
            payload = r.json()
            current = payload.get("tokens", {}) if isinstance(payload, dict) else {}
    except Exception:
        current = {}
    if not isinstance(current, dict):
        current = {}

    # try both api pool key and display name keys
    keys_try = [pool_name, "Build", "build", "ssoBuild"]
    pool_key = pool_name
    pool: list[Any] = []
    for k in keys_try:
        if k in current and isinstance(current[k], list):
            pool_key = k
            pool = list(current[k])
            break
    else:
        pool = []

    existing = set()
    for item in pool:
        if isinstance(item, str):
            existing.add(normalize_sso(item))
        elif isinstance(item, dict):
            existing.add(normalize_sso(str(item.get("token") or "")))
    if token not in existing:
        pool.append({"token": token, "tags": tags, "note": email})
    current[pool_key] = pool

    r2 = requests.post(f"{base}/tokens", json=current, **kwargs)
    if r2.status_code >= 400:
        raise Grok2ApiError(f"upload fallback HTTP {r2.status_code}: {(r2.text or '')[:300]}")
    log(f"[g2a] upload ok (full) pool_key={pool_key} email={email}")
    return {"ok": True, "code": "upload_ok", "pool": pool_key, "status": r2.status_code}


def upload_from_config(
    sso: str,
    email: str,
    cfg: dict[str, Any],
    *,
    log: LogFn | None = None,
    force: bool = False,
) -> dict[str, Any]:
    log = log or default_log
    if not cfg.get("grok2api_auto_add_remote", True) and not force:
        return {"ok": False, "code": "upload_skipped", "error": "auto_add_remote disabled"}
    base = str(cfg.get("grok2api_remote_base") or "").strip()
    app_key = str(cfg.get("grok2api_remote_app_key") or "").strip()
    pool = resolve_pool_api_value(cfg)
    proxy = str(cfg.get("proxy") or "").strip()
    return upload_sso_to_build(
        sso,
        base=base,
        app_key=app_key,
        pool=pool,
        email=email,
        auto_nsfw=bool(cfg.get("grok2api_auto_nsfw", True)),
        proxy=proxy,
        log=log,
    )
