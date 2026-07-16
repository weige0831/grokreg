# -*- coding: utf-8 -*-
"""Camoufox local Turnstile solver client helpers (YesCaptcha-compatible API)."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any


DEFAULT_LOCAL_SOLVER_URL = "http://127.0.0.1:5072"


def local_solver_url(cfg: dict[str, Any] | None = None, explicit: str = "") -> str:
    raw = (
        (explicit or "").strip()
        or str((cfg or {}).get("local_solver_url") or "").strip()
        or os.environ.get("LOCAL_SOLVER_URL", "").strip()
        or os.environ.get("GROK2API_LOCAL_SOLVER_URL", "").strip()
        or DEFAULT_LOCAL_SOLVER_URL
    ).rstrip("/")
    return raw or DEFAULT_LOCAL_SOLVER_URL


def probe_local_solver(url: str = "", *, timeout: float = 2.0) -> dict[str, Any]:
    base = (url or DEFAULT_LOCAL_SOLVER_URL).rstrip("/")
    out: dict[str, Any] = {
        "ok": False,
        "ready": False,
        "url": base,
        "error": None,
        "status_code": None,
    }
    try:
        req = urllib.request.Request(
            f"{base}/health",
            headers={"Accept": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=max(0.5, float(timeout))) as resp:
            out["status_code"] = int(getattr(resp, "status", 200) or 200)
            body = resp.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(body) if body else {}
        except Exception:
            data = {}
        if isinstance(data, dict) and data.get("ok") is False:
            out["error"] = f"solver health ok=false body={body[:200]}"
            return out
        out["ok"] = True
        out["ready"] = True
        return out
    except Exception as e:
        out["error"] = str(e)[:300]
    # fallback root
    try:
        req = urllib.request.Request(f"{base}/", method="GET")
        with urllib.request.urlopen(req, timeout=max(0.5, float(timeout))) as resp:
            out["status_code"] = int(getattr(resp, "status", 200) or 200)
            _ = resp.read(64)
        out["ok"] = True
        out["ready"] = True
        out["error"] = None
        return out
    except Exception as e:
        if not out.get("error"):
            out["error"] = str(e)[:300]
        return out


def wait_for_local_solver(
    url: str = "",
    *,
    timeout_sec: float = 120.0,
    poll_sec: float = 1.0,
    log=None,
) -> dict[str, Any]:
    base = (url or DEFAULT_LOCAL_SOLVER_URL).rstrip("/")
    deadline = time.time() + max(5.0, float(timeout_sec))
    last: dict[str, Any] = {"ok": False, "ready": False, "url": base, "error": "not started"}
    while time.time() < deadline:
        last = probe_local_solver(base, timeout=min(2.0, poll_sec + 0.5))
        if last.get("ready"):
            if log:
                log(f"[solver] local ready {base}")
            return last
        if log:
            log(f"[solver] waiting local {base}: {last.get('error') or 'not ready'}")
        time.sleep(max(0.3, float(poll_sec)))
    last["error"] = last.get("error") or f"local solver not ready within {timeout_sec}s: {base}"
    return last


def pin_local_solver_env(url: str) -> str:
    """Point YesCaptcha-compatible client at local Camoufox solver."""
    base = (url or DEFAULT_LOCAL_SOLVER_URL).rstrip("/")
    os.environ["LOCAL_SOLVER_URL"] = base
    os.environ["GROK2API_LOCAL_SOLVER_URL"] = base
    os.environ["YESCAPTCHA_ENDPOINT"] = base
    os.environ["GROK2API_YESCAPTCHA_ENDPOINT"] = base
    os.environ["YESCAPTCHA_API_BASE"] = base
    return base
