from __future__ import annotations

import sys
import time
from typing import Callable


LogFn = Callable[[str], None]


def _safe_console_text(msg: str) -> str:
    """Avoid Windows GBK console crashes on emoji / special unicode."""
    text = str(msg)
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(enc)
        return text
    except Exception:
        return text.encode(enc, errors="replace").decode(enc, errors="replace")


def default_log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {_safe_console_text(msg)}"
    try:
        print(line, flush=True)
    except Exception:
        try:
            sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))
            sys.stdout.buffer.flush()
        except Exception:
            pass


def prefix_log(prefix: str, base: LogFn | None = None) -> LogFn:
    log = base or default_log

    def _fn(msg: str) -> None:
        log(f"{prefix}{msg}")

    return _fn
