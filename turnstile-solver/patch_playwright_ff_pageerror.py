# -*- coding: utf-8 -*-
"""Patch Playwright Firefox pageError crash (location undefined).

On Windows GHA + Camoufox, uncaught page errors sometimes arrive without
``location``, and Playwright's coreBundle does:

    url: pageError.location.url

which throws and kills the Node driver. Patch known patterns to optional-chain.
"""
from __future__ import annotations

import sys
from pathlib import Path


PATTERNS = [
    (
        "url: pageError.location.url,",
        "url: pageError.location && pageError.location.url,",
    ),
    (
        "url: pageError.location.url",
        "url: pageError.location && pageError.location.url",
    ),
    (
        "pageError.location.url",
        "(pageError.location && pageError.location.url)",
    ),
]


def main() -> int:
    roots = []
    # site-packages next to this script's venv or cwd
    for base in [
        Path(sys.prefix) / "Lib" / "site-packages",
        Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages",
        Path.cwd() / ".venv" / "Lib" / "site-packages",
        Path.cwd() / ".venv" / "lib",
    ]:
        if base.exists():
            roots.append(base)

    targets: list[Path] = []
    for root in roots:
        targets.extend(root.rglob("coreBundle.js"))
        targets.extend(root.rglob("**/playwright/driver/package/lib/coreBundle.js"))

    # de-dupe
    seen = set()
    uniq: list[Path] = []
    for t in targets:
        key = str(t.resolve())
        if key not in seen:
            seen.add(key)
            uniq.append(t)

    if not uniq:
        print("patch: no coreBundle.js found (ok if not installed yet)")
        return 0

    patched = 0
    for path in uniq:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"skip {path}: {e}")
            continue
        orig = text
        # Only apply first specific replacements carefully
        if "pageError.location.url" in text and "pageError.location && pageError.location.url" not in text:
            text = text.replace(
                "url: pageError.location.url,",
                "url: pageError.location && pageError.location.url,",
            )
            text = text.replace(
                "url: pageError.location.url",
                "url: pageError.location && pageError.location.url",
            )
            # remaining bare refs inside the same handler area
            if "pageError.location.url" in text:
                text = text.replace(
                    "pageError.location.url",
                    "(pageError.location&&pageError.location.url)",
                )
        if text != orig:
            path.write_text(text, encoding="utf-8")
            patched += 1
            print(f"patched {path}")
        else:
            print(f"already ok / no match: {path}")

    print(f"patch done files={len(uniq)} patched={patched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
