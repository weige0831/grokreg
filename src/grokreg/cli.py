from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from grokreg.config import load_config
from grokreg.pipeline import run_probe_upload, run_register
from grokreg.util.log import default_log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grokreg", description="Grok register → Build 4.5 probe → grok2api Build")
    parser.add_argument("-c", "--config", default="", help="path to config.json")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="register + probe + upload")
    p_run.add_argument("--count", type=int, default=0, help="how many accounts (0=config)")
    p_run.add_argument("--extra", type=int, default=0, help="alias of --count")

    p_pu = sub.add_parser("probe-upload", help="probe existing accounts then upload Build")
    p_pu.add_argument("--accounts", default="", help="accounts file")
    p_pu.add_argument("--limit", type=int, default=0, help="max accounts (0=all)")

    p_mail = sub.add_parser("mail-smoke", help="create one tempmail address and print it")

    args = parser.parse_args(argv)
    cfg = load_config(args.config or None)

    if args.cmd == "run":
        n = args.count or args.extra or 0
        results = run_register(cfg, count=n or None, log=default_log)
        if not results:
            return 1
        # success if any usable / probe_ok (upload optional when auto_add disabled)
        ok = sum(
            1
            for r in results
            if r.get("usable") is True
            or r.get("status") in {"probe_ok", "upload_ok", "upload_fail"}
        )
        return 0 if ok else 1

    if args.cmd == "probe-upload":
        results = run_probe_upload(
            cfg,
            accounts_file=args.accounts or None,
            limit=args.limit,
            log=default_log,
        )
        if not results:
            return 1
        ok = sum(
            1
            for r in results
            if r.get("usable") is True
            or r.get("status") in {"probe_ok", "upload_ok", "upload_fail"}
        )
        return 0 if ok else 1

    if args.cmd == "mail-smoke":
        from grokreg.mail.tempmail import TempMailClient

        client = TempMailClient(
            base_url=str(cfg.get("tempmail_base_url") or ""),
            domains=list(cfg.get("tempmail_domains") or []),
            proxy=str(cfg.get("proxy") or ""),
            log=default_log,
        )
        domains = client.list_domains()
        default_log(f"remote domains: {domains}")
        email, token = client.create_address()
        default_log(f"email={email}")
        default_log(f"token={token[:16]}...")
        print(json.dumps({"email": email, "token": token[:20] + "..."}, ensure_ascii=False))
        return 0

    parser.error(f"unknown cmd {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
