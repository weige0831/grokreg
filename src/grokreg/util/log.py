from __future__ import annotations

import sys
import time
from typing import Callable


LogFn = Callable[[str], None]


def default_log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def prefix_log(prefix: str, base: LogFn | None = None) -> LogFn:
    log = base or default_log

    def _fn(msg: str) -> None:
        log(f"{prefix}{msg}")

    return _fn
