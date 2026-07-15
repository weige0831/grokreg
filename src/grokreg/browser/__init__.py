from grokreg.browser.register import BrowserRegisterError, BrowserRegistrar

try:
    from grokreg.browser.cloak_register import CloakBrowserRegistrar
except Exception:  # pragma: no cover
    CloakBrowserRegistrar = None  # type: ignore

try:
    from grokreg.browser.cloak_session import CloakSession, cloak_post_register
except Exception:  # pragma: no cover
    CloakSession = None  # type: ignore
    cloak_post_register = None  # type: ignore


def create_registrar(cfg: dict, log=None):
    """Factory: drission sample engine (default, Turnstile-proven) or cloakbrowser."""
    engine = str((cfg or {}).get("browser_engine") or "drission").strip().lower()
    if engine in {"cloak", "cloakbrowser", "playwright"}:
        if CloakBrowserRegistrar is None:
            raise RuntimeError("cloakbrowser not available; pip install cloakbrowser")
        return CloakBrowserRegistrar(cfg, log=log)
    return BrowserRegistrar(cfg, log=log)


__all__ = [
    "BrowserRegistrar",
    "BrowserRegisterError",
    "CloakBrowserRegistrar",
    "CloakSession",
    "cloak_post_register",
    "create_registrar",
]
