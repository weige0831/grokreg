from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from grokreg.browser.register import BrowserRegisterError, BrowserRegistrar
from grokreg.config import load_config, resolve_proxy
from grokreg.probe.build45 import mint_and_probe
from grokreg.sink.grok2api import Grok2ApiError, upload_from_config
from grokreg.util.accounts import AccountLine, append_account, parse_accounts_file
from grokreg.util.log import LogFn, default_log, prefix_log


def _append_result(path: str | Path, row: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # redact long secrets in results.jsonl (importable export uses accounts + full fields separately)
    out = dict(row)
    sso = str(out.get("sso") or "")
    if len(sso) > 24:
        out["sso"] = sso[:12] + "..." + sso[-8:]
    for k in ("access_token", "refresh_token"):
        v = str(out.get(k) or "")
        if len(v) > 24:
            out[k] = v[:12] + "..." + v[-8:]
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False) + "\n")

    # sidecar with full credentials for import (scheduler / manual test)
    if row.get("status") in {"probe_ok", "upload_ok", "upload_fail"} and row.get("sso"):
        side = p.with_name("importable.jsonl")
        item = {
            "email": row.get("email") or "",
            "password": row.get("password") or "",
            "sso": row.get("sso") or "",
            "access_token": row.get("access_token") or "",
            "refresh_token": row.get("refresh_token") or "",
            "source": "grokreg",
            "probe_status": 200 if row.get("status") in {"probe_ok", "upload_ok", "upload_fail"} else 0,
            "probe_detail": str((row.get("probe") or {}).get("detail") or row.get("status") or ""),
            "tags": ["probe-ok"],
        }
        with side.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def process_sso(
    email: str,
    sso: str,
    cfg: dict[str, Any],
    *,
    log: LogFn | None = None,
    password: str = "",
    skip_register: bool = True,
) -> dict[str, Any]:
    """Mint + hard probe 4.5 + upload Build if probe ok."""
    log = log or default_log
    row: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "email": email,
        "password": password,
        "sso": sso,
        "status": "registered" if skip_register else "registered",
        "code": "",
    }
    proxy = resolve_proxy(cfg, mint=True)
    probe = mint_and_probe(
        sso,
        proxy=proxy,
        base_url=str(cfg.get("probe_base_url") or "https://cli-chat-proxy.grok.com/v1"),
        model=str(cfg.get("probe_model") or "grok-4.5"),
        timeout=float(cfg.get("probe_timeout") or 60),
        fail_status_codes=list(cfg.get("probe_fail_status_codes") or []),
        log=log,
    )
    token = probe.get("token") if isinstance(probe.get("token"), dict) else {}
    if token:
        row["access_token"] = str(token.get("access_token") or "")
        row["refresh_token"] = str(token.get("refresh_token") or "")
    row["probe"] = {k: v for k, v in probe.items() if k != "token"}
    if not probe.get("ok"):
        row["status"] = "probe_fail"
        row["code"] = str(probe.get("code") or "probe_fail")
        row["error"] = probe.get("error")
        return row

    row["status"] = "probe_ok"
    row["code"] = "probe_ok"

    # skip remote upload when disabled (register/probe dry-run)
    if not cfg.get("grok2api_auto_add_remote", True):
        row["upload"] = {"ok": False, "code": "upload_skipped", "error": "auto_add_remote disabled"}
        log("[pipe] upload skipped (auto_add_remote=false)")
        return row

    only_after = bool(cfg.get("grok2api_upload_only_after_probe", True))
    if only_after and not probe.get("ok"):
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

    reg = BrowserRegistrar(cfg, log=log)
    try:
        reg.start()
        for i in range(1, n + 1):
            wlog = prefix_log(f"[{i}/{n}] ", log)
            wlog("=== start ===")
            row: dict[str, Any] = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "idx": i,
                "status": "reg_fail",
                "code": "reg_fail",
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
                )
                row["idx"] = i
            except BrowserRegisterError as exc:
                wlog(f"register failed: {exc}")
                row["error"] = str(exc)[:500]
                row["code"] = "reg_fail"
                row["status"] = "reg_fail"
            except Exception as exc:
                wlog(f"error: {exc}")
                row["error"] = str(exc)[:500]
                row["code"] = "reg_fail"
                row["status"] = "reg_fail"
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
    log(f"[summary] total={len(results)} probe_passish={probe_ok} upload_ok={ok}")
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
    ok = sum(1 for r in results if r.get("status") == "upload_ok")
    log(f"[summary] total={len(results)} upload_ok={ok}")
    return results
