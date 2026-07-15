from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class AccountLine:
    email: str
    password: str
    sso: str
    raw: str = ""


def parse_accounts_file(path: str | Path) -> list[AccountLine]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[AccountLine] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        parts = raw.split("----")
        if len(parts) >= 3:
            email, password, sso = parts[0].strip(), parts[1].strip(), parts[-1].strip()
        elif len(parts) == 1:
            email, password, sso = "", "", parts[0].strip()
        else:
            continue
        if not sso:
            continue
        out.append(AccountLine(email=email, password=password, sso=sso, raw=raw))
    return out


def append_account(path: str | Path, email: str, password: str, sso: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(f"{email}----{password}----{sso}\n")
