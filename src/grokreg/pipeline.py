from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from grokreg.browser import BrowserRegisterError, create_registrar

try:
    from grokreg.protocol.register import ProtocolRegisterError
except Exception:  # pragma: no cover
    ProtocolRegisterError = BrowserRegisterError  # type: ignore
from grokreg.config import load_config, resolve_proxy
from grokreg.probe.build45 import mint_and_probe
from grokreg.probe.web_chat import probe_web_default_model
from grokreg.sink.grok2api import Grok2ApiError, upload_from_config
from grokreg.util.accounts import append_account, parse_accounts_file
from grokreg.util.log import LogFn, default_log, prefix_log


def _write_g2a_auth_file(
    row: dict[str, Any],
    *,
    token: dict[str, Any] | None = None,
    out_dir: str | Path = "g2a_auth",
) -> str:
    """Write CPA-compatible single-account JSON for grok2api import. Returns path or ''."""
    from grokreg.mint.auth_code import token_to_cpa_record

    email = str(row.get("email") or "")
    sso = str(row.get("sso") or "")
    tok = token if isinstance(token, dict) else {}
    if not tok.get("access_token") and row.get("access_token"):
        tok = {
            "access_token": row.get("access_token") or "",
            "refresh_token": row.get("refresh_token") or "",
            "id_token": row.get("id_token") or "",
            "token_type": row.get("token_type") or "Bearer",
            "expires_in": row.get("expires_in"),
        }
    if not tok.get("access_token"):
        return ""
    rec = token_to_cpa_record(tok, email=email, sso=sso)
    safe = (email or "unknown").replace("@", "_").replace("/", "_")
    path = Path(out_dir) / f"xai-{safe}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(path)


def _append_result(path: str | Path, row: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    out = dict(row)
    sso = str(out.get("sso") or "")
    if len(sso) > 24:
        out["sso"] = sso[:12] + "..." + sso[-8:]
    for k in ("access_token", "refresh_token", "id_token"):
        v = str(out.get(k) or "")
        if len(v) > 24:
            out[k] = v[:12] + "..." + v[-8:]
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False) + "\n")

    # only export usable (probe_ok) accounts
    if row.get("status") in {"probe_ok", "upload_ok", "upload_fail"} and row.get("sso"):
        side = p.with_name("importable.jsonl")
        item = {
            "email": row.get("email") or "",
            "password": row.get("password") or "",
            "sso": row.get("sso") or "",
            "access_token": row.get("access_token") or "",
            "refresh_token": row.get("refresh_token") or "",
            "id_token": row.get("id_token") or "",
            "source": "grokreg",
            "probe_status": 200,
            "probe_detail": str((row.get("probe") or {}).get("detail") or row.get("status") or ""),
            "tags": ["probe-ok", "build-ok"],
            "usable": True,
        }
        with side.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
        # CPA single JSON for grok2api direct import
        try:
            g2a_path = _write_g2a_auth_file(row, token=row.get("_token_full") if isinstance(row.get("_token_full"), dict) else None)
            if g2a_path:
                row["g2a_auth_file"] = g2a_path
        except Exception:
            pass

    # 403 / unusable sidecar for inventory
    if row.get("usable") is False and row.get("sso"):
        dead = p.with_name("unusable.jsonl")
        item = {
            "email": row.get("email") or "",
            "password": row.get("password") or "",
            "sso": row.get("sso") or "",
            "code": row.get("code") or "",
            "status": row.get("status") or "",
            "usable": False,
            "bot_flag": (row.get("probe") or {}).get("bot_flag"),
            "error": str(row.get("error") or "")[:300],
        }
        with dead.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def process_sso(
    email: str,
    sso: str,
    cfg: dict[str, Any],
    *,
    log: LogFn | None = None,
    password: str = "",
    skip_register: bool = True,
    cf_clearance: str = "",
    user_agent: str = "",
    web_pre: dict[str, Any] | None = None,
    build_web_pre: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Flow: web (UI pre or HTTP) → wait → Build (browser authorize pre or HTTP) → probe → 403 unusable."""
    log = log or default_log
    row: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "email": email,
        "password": password,
        "sso": sso,
        "status": "registered",
        "code": "",
        "usable": None,
    }
    proxy = resolve_proxy(cfg, mint=True)

    # 0) optional Cloak post path
    use_cloak = bool(cfg.get("post_register_cloak", False))
    web = web_pre if isinstance(web_pre, dict) else None
    if use_cloak:
        try:
            from grokreg.browser.cloak_session import cloak_post_register

            log("[pipe] post-register Cloak login + web probe…")
            cloak_meta = cloak_post_register(sso, cfg, cf_clearance=cf_clearance, log=log)
            if isinstance(cloak_meta.get("web"), dict):
                web = cloak_meta["web"]
            if cloak_meta.get("cf_clearance"):
                cf_clearance = str(cloak_meta["cf_clearance"])
            if cloak_meta.get("user_agent"):
                user_agent = str(cloak_meta["user_agent"])
            if cloak_meta.get("fingerprint"):
                row["cloak_fingerprint"] = cloak_meta["fingerprint"]
        except Exception as exc:
            log(f"[pipe] cloak post-register failed: {exc}")
            row["cloak_error"] = str(exc)[:300]
            if bool(cfg.get("require_cloak_web", False)):
                row["status"] = "cloak_fail"
                row["code"] = "cloak_fail"
                row["error"] = str(exc)[:500]
                row["usable"] = False
                return row

    # 1) web (prefer browser UI result from register_one)
    if not web or web.get("ok") is None:
        web = probe_web_default_model(
            sso,
            proxy=proxy,
            cf_clearance=cf_clearance,
            user_agent=user_agent,
            model=str(cfg.get("web_default_model") or "grok-3"),
            prompt=str(cfg.get("web_probe_prompt") or "你好"),
            timeout=float(cfg.get("web_probe_timeout") or 45),
            log=log,
        )
    row["web"] = {k: v for k, v in (web or {}).items() if k != "raw"}
    if not web.get("ok"):
        if bool(cfg.get("require_web_before_build", True)):
            row["status"] = "web_fail"
            row["code"] = str(web.get("code") or "web_fail")
            row["error"] = web.get("error") or web.get("text")
            row["usable"] = False
            log(f"[pipe] web failed -> skip Build code={row['code']}")
            return row
        log("[pipe] web fail but require_web_before_build=false, continue Build")

    # 2) Build: prefer same-browser authorize token, else HTTP mint
    build_web = build_web_pre if isinstance(build_web_pre, dict) else None
    if build_web is not None:
        row["build_web"] = {k: v for k, v in build_web.items() if k != "token"}

    access = ""
    refresh = ""
    full_token: dict[str, Any] | None = None
    if isinstance(build_web, dict) and build_web.get("ok") and isinstance(build_web.get("token"), dict):
        tok = build_web["token"]
        full_token = dict(tok)
        access = str(tok.get("access_token") or "")
        refresh = str(tok.get("refresh_token") or "")
        if tok.get("id_token"):
            row["id_token"] = str(tok.get("id_token") or "")
        log("[pipe] using browser Build authorize token")
    else:
        wait_sec = float(cfg.get("build_authorize_delay_sec") or 3)
        if wait_sec > 0 and not (isinstance(build_web, dict) and build_web.get("ok") is False and bool(cfg.get("browser_build_authorize"))):
            log(f"[pipe] web ok, wait {wait_sec:.1f}s then HTTP Build authorize")
            time.sleep(wait_sec)
        log("[pipe] HTTP Build authorize (protocol mint)")
        probe_mint = mint_and_probe(
            sso,
            proxy=proxy,
            base_url=str(cfg.get("probe_base_url") or "https://cli-chat-proxy.grok.com/v1"),
            model=str(cfg.get("probe_model") or "grok-4.5"),
            timeout=float(cfg.get("probe_timeout") or 60),
            fail_status_codes=list(cfg.get("probe_fail_status_codes") or []),
            log=log,
            cf_clearance=cf_clearance,
            user_agent=user_agent,
            activate=bool(cfg.get("activate_before_probe", True)),
            endpoint=str(cfg.get("probe_endpoint") or "responses"),
        )
        tok = probe_mint.get("token") if isinstance(probe_mint.get("token"), dict) else {}
        full_token = dict(tok) if tok else None
        access = str(tok.get("access_token") or "")
        refresh = str(tok.get("refresh_token") or "")
        row["probe"] = {k: v for k, v in probe_mint.items() if k != "token"}
        if access:
            row["access_token"] = access
            row["refresh_token"] = refresh
            if tok.get("id_token"):
                row["id_token"] = str(tok.get("id_token") or "")
            if full_token:
                row["_token_full"] = full_token
        if not probe_mint.get("ok"):
            status_code = int(probe_mint.get("status") or 0)
            code = str(probe_mint.get("code") or "probe_fail")
            row["status"] = "probe_fail"
            row["code"] = code
            row["error"] = probe_mint.get("error")
            if status_code == 403 or "403" in code or "permission" in code:
                row["usable"] = False
                row["code"] = "probe_403_unusable"
                log("[pipe] Build probe 403 -> mark unusable")
            else:
                row["usable"] = False
            return row
        row["status"] = "probe_ok"
        row["code"] = "probe_ok"
        row["usable"] = True
        log("[pipe] Build probe OK -> usable")
        # fall through to upload section below via shared path
        if not cfg.get("grok2api_auto_add_remote", False):
            row["upload"] = {"ok": False, "code": "upload_skipped", "error": "auto_add_remote disabled"}
            log("[pipe] upload skipped (auto_add_remote=false)")
            return row
        try:
            up = upload_from_config(sso, email, cfg, log=log)
            row["upload"] = up
            if up.get("ok"):
                row["status"] = "upload_ok"
                row["code"] = "upload_ok"
            else:
                row["status"] = "upload_fail"
                row["code"] = str(up.get("code") or "upload_fail")
                row["error"] = up.get("error")
        except Grok2ApiError as exc:
            log(f"[pipe] upload failed: {exc}")
            row["status"] = "upload_fail"
            row["code"] = "upload_fail"
            row["error"] = str(exc)[:500]
            row["upload"] = {"ok": False, "error": str(exc)[:500]}
        except Exception as exc:
            log(f"[pipe] upload error: {exc}")
            row["status"] = "upload_fail"
            row["code"] = "upload_fail"
            row["error"] = str(exc)[:500]
        return row

    # browser/protocol token path: activate → probe Build API (settle retry)
    if access:
        row["access_token"] = access
        row["refresh_token"] = refresh
        if full_token:
            row["_token_full"] = full_token
            if full_token.get("id_token"):
                row["id_token"] = str(full_token.get("id_token") or "")
        from grokreg.mint.auth_code import activate_account_for_build, decode_jwt_payload
        from grokreg.probe.build45 import probe_with_settle_retry

        if bool(cfg.get("activate_before_probe", True)) and sso:
            log("[pipe] activate TOS/birth before probe…")
            act = activate_account_for_build(
                sso,
                proxy=proxy,
                cf_clearance=cf_clearance,
                user_agent=user_agent,
                log=log,
            )
            row["activate"] = act

        claims = decode_jwt_payload(access)
        pr = probe_with_settle_retry(
            access,
            base_url=str(cfg.get("probe_base_url") or "https://cli-chat-proxy.grok.com/v1"),
            model=str(cfg.get("probe_model") or "grok-4.5"),
            proxy=proxy,
            timeout=float(cfg.get("probe_timeout") or 60),
            fail_status_codes=list(cfg.get("probe_fail_status_codes") or []),
            endpoint=str(cfg.get("probe_endpoint") or "responses"),
            retries=int(cfg.get("probe_settle_retries") or 3),
            retry_delay_sec=float(cfg.get("probe_settle_delay_sec") or 8),
            log=log,
        )
        pr["bot_flag"] = claims.get("bot_flag_source")
        pr["referrer"] = claims.get("referrer")
        row["probe"] = pr
        if not pr.get("ok"):
            status_code = int(pr.get("status") or 0)
            code = str(pr.get("code") or "probe_fail")
            row["status"] = "probe_fail"
            row["code"] = code
            row["error"] = pr.get("error")
            if status_code == 403 or "403" in code or "permission" in code:
                row["usable"] = False
                row["code"] = "probe_403_unusable"
                log(
                    f"[pipe] Build probe 403 -> mark unusable "
                    f"bot_flag={pr.get('bot_flag')!r} attempts={pr.get('attempt')}"
                )
            else:
                row["usable"] = False
            return row
        row["status"] = "probe_ok"
        row["code"] = "probe_ok"
        row["usable"] = True
        log("[pipe] Build probe OK (browser authorize) -> usable")
    else:
        row["status"] = "build_fail"
        row["code"] = "build_no_token"
        row["usable"] = False
        row["error"] = "no access_token from browser or HTTP mint"
        return row

    if not cfg.get("grok2api_auto_add_remote", False):
        row["upload"] = {"ok": False, "code": "upload_skipped", "error": "auto_add_remote disabled"}
        log("[pipe] upload skipped (auto_add_remote=false)")
        return row

    try:
        up = upload_from_config(sso, email, cfg, log=log)
        row["upload"] = up
        if up.get("ok"):
            row["status"] = "upload_ok"
            row["code"] = "upload_ok"
        else:
            row["status"] = "upload_fail"
            row["code"] = str(up.get("code") or "upload_fail")
            row["error"] = up.get("error")
    except Grok2ApiError as exc:
        log(f"[pipe] upload failed: {exc}")
        row["status"] = "upload_fail"
        row["code"] = "upload_fail"
        row["error"] = str(exc)[:500]
        row["upload"] = {"ok": False, "error": str(exc)[:500]}
    except Exception as exc:
        log(f"[pipe] upload error: {exc}")
        row["status"] = "upload_fail"
        row["code"] = "upload_fail"
        row["error"] = str(exc)[:500]
    return row


def run_register(
    cfg: dict[str, Any] | None = None,
    *,
    count: int | None = None,
    log: LogFn | None = None,
) -> list[dict[str, Any]]:
    cfg = cfg or load_config()
    log = log or default_log
    n = int(count if count is not None else cfg.get("register_count") or 1)
    n = max(1, n)
    accounts_file = str(cfg.get("accounts_file") or "accounts.txt")
    results_file = str(cfg.get("results_file") or "results.jsonl")
    results: list[dict[str, Any]] = []

    reg = create_registrar(cfg, log=log)
    try:
        reg.start()
        for i in range(1, n + 1):
            wlog = prefix_log(f"[{i}/{n}] ", log)
            wlog(f"=== start engine={cfg.get('browser_engine') or 'cloak'} ===")
            row: dict[str, Any] = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "idx": i,
                "status": "reg_fail",
                "code": "reg_fail",
                "usable": False,
            }
            try:
                acc = reg.register_one()
                email = acc["email"]
                password = acc["password"]
                sso = acc["sso"]
                append_account(accounts_file, email, password, sso)
                wlog(f"registered {email}")
                row = process_sso(
                    email,
                    sso,
                    cfg,
                    log=wlog,
                    password=password,
                    skip_register=True,
                    cf_clearance=str(acc.get("cf_clearance") or ""),
                    user_agent=str(acc.get("user_agent") or ""),
                    web_pre=acc.get("web") if isinstance(acc.get("web"), dict) else None,
                    build_web_pre=acc.get("build_web") if isinstance(acc.get("build_web"), dict) else None,
                )
                row["idx"] = i
                if acc.get("fingerprint"):
                    row["fingerprint"] = acc.get("fingerprint")
            except (BrowserRegisterError, ProtocolRegisterError) as exc:
                wlog(f"register failed: {exc}")
                row["error"] = str(exc)[:500]
                row["code"] = "reg_fail"
                row["status"] = "reg_fail"
                row["usable"] = False
            except Exception as exc:
                wlog(f"error: {exc}")
                row["error"] = str(exc)[:500]
                row["code"] = "reg_fail"
                row["status"] = "reg_fail"
                row["usable"] = False
            _append_result(results_file, row)
            results.append(row)
            try:
                reg.restart()
            except Exception:
                pass
    finally:
        reg.stop()

    ok = sum(1 for r in results if r.get("status") == "upload_ok")
    probe_ok = sum(1 for r in results if r.get("status") in {"probe_ok", "upload_ok", "upload_fail"})
    usable = sum(1 for r in results if r.get("usable") is True)
    unusable = sum(1 for r in results if r.get("usable") is False)
    log(f"[summary] total={len(results)} usable={usable} unusable={unusable} probe_ok={probe_ok} upload_ok={ok}")
    return results


def run_probe_upload(
    cfg: dict[str, Any] | None = None,
    *,
    accounts_file: str | None = None,
    log: LogFn | None = None,
    limit: int = 0,
) -> list[dict[str, Any]]:
    cfg = cfg or load_config()
    log = log or default_log
    path = accounts_file or str(cfg.get("accounts_file") or "accounts.txt")
    results_file = str(cfg.get("results_file") or "results.jsonl")
    lines = parse_accounts_file(path)
    if limit > 0:
        lines = lines[:limit]
    log(f"[probe-upload] {len(lines)} accounts from {path}")
    results: list[dict[str, Any]] = []
    for i, acc in enumerate(lines, 1):
        wlog = prefix_log(f"[{i}/{len(lines)}] ", log)
        row = process_sso(acc.email, acc.sso, cfg, log=wlog, password=acc.password)
        row["idx"] = i
        _append_result(results_file, row)
        results.append(row)
    usable = sum(1 for r in results if r.get("usable") is True)
    ok = sum(1 for r in results if r.get("status") == "upload_ok")
    log(f"[summary] total={len(results)} usable={usable} upload_ok={ok}")
    return results
