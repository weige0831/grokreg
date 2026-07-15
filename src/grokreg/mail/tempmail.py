from __future__ import annotations

import random
import re
import time
from typing import Any

from curl_cffi import requests

from grokreg.util.log import LogFn, default_log

CODE_RE = re.compile(r"\b(\d{6})\b")
SENDER_HINTS = ("x.ai", "xai", "grok", "accounts.x.ai", "noreply")


class TempMailError(RuntimeError):
    pass


class TempMailClient:
    """Lm36/tempmail-server API client.

    Base: https://mail.minecraft-cn.net
    POST /api/v1/addresses  → {email, token, expires_at}
    GET  /api/v1/{token}/emails
    GET  /api/v1/{token}/emails/{id}
    """

    def __init__(
        self,
        base_url: str = "https://mail.minecraft-cn.net",
        domains: list[str] | None = None,
        proxy: str = "",
        poll_interval: float = 2.0,
        timeout: float = 120.0,
        list_limit: int = 30,
        log: LogFn | None = None,
    ) -> None:
        self.base = str(base_url or "").rstrip("/")
        self.domains = list(domains or ["mtoosov.shop", "olsbvgq.shop", "htazmbb.shop"])
        self.proxy = (proxy or "").strip()
        self.poll_interval = max(0.5, float(poll_interval))
        self.timeout = max(10.0, float(timeout))
        self.list_limit = max(5, int(list_limit))
        self.log = log or default_log
        self._session = requests.Session()
        if self.proxy:
            self._session.proxies = {"http": self.proxy, "https": self.proxy}

    def _req(self, method: str, path: str, **kwargs: Any):
        url = f"{self.base}{path}"
        kwargs.setdefault("timeout", 30)
        kwargs.setdefault("impersonate", "chrome")
        try:
            resp = self._session.request(method, url, **kwargs)
        except Exception as exc:
            raise TempMailError(f"request failed {method} {path}: {exc}") from exc
        return resp

    def list_domains(self) -> list[str]:
        resp = self._req("GET", "/api/v1/domains")
        if resp.status_code >= 400:
            raise TempMailError(f"domains HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json() if resp.content else {}
        domains = data.get("domains") if isinstance(data, dict) else data
        if not isinstance(domains, list):
            return []
        return [str(d).strip() for d in domains if str(d).strip()]

    def create_address(self, domain: str | None = None) -> tuple[str, str]:
        """Return (email, token). Domain randomly chosen from configured pool."""
        dom = (domain or "").strip() or random.choice(self.domains)
        self.log(f"[mail] create address domain={dom}")
        resp = self._req(
            "POST",
            "/api/v1/addresses",
            headers={"Content-Type": "application/json"},
            json={"domain": dom},
        )
        if resp.status_code >= 400:
            raise TempMailError(f"create address HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json() if resp.content else {}
        email = str(data.get("email") or "").strip()
        token = str(data.get("token") or "").strip()
        if not email or not token:
            raise TempMailError(f"create address bad response: {data!r}")
        self.log(f"[mail] created {email}")
        return email, token

    def list_emails(self, token: str) -> list[dict[str, Any]]:
        resp = self._req("GET", f"/api/v1/{token}/emails")
        if resp.status_code >= 400:
            raise TempMailError(f"list emails HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json() if resp.content else {}
        emails = data.get("emails") if isinstance(data, dict) else data
        if not isinstance(emails, list):
            return []
        return [e for e in emails if isinstance(e, dict)][: self.list_limit]

    def get_email(self, token: str, email_id: str) -> dict[str, Any]:
        resp = self._req("GET", f"/api/v1/{token}/emails/{email_id}")
        if resp.status_code >= 400:
            raise TempMailError(f"get email HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json() if resp.content else {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def extract_code(text: str) -> str | None:
        if not text:
            return None
        # Prefer "verification code is 123456" style
        m = re.search(
            r"(?:code|验证码|verification)[^\d]{0,40}(\d{6})",
            text,
            re.I,
        )
        if m:
            return m.group(1)
        m = CODE_RE.search(text)
        return m.group(1) if m else None

    def _body_text(self, detail: dict[str, Any]) -> str:
        parts: list[str] = []
        for key in ("subject", "text", "text_body", "body", "plain", "html", "html_body"):
            v = detail.get(key)
            if isinstance(v, str) and v.strip():
                parts.append(v)
        # nested content
        content = detail.get("content")
        if isinstance(content, dict):
            for key in ("text", "html", "plain"):
                v = content.get(key)
                if isinstance(v, str) and v.strip():
                    parts.append(v)
        return "\n".join(parts)

    def wait_code(
        self,
        token: str,
        *,
        timeout: float | None = None,
        since: float | None = None,
    ) -> str:
        deadline = time.time() + (timeout if timeout is not None else self.timeout)
        seen: set[str] = set()
        while time.time() < deadline:
            try:
                emails = self.list_emails(token)
            except TempMailError as exc:
                self.log(f"[mail] list error: {exc}")
                time.sleep(self.poll_interval)
                continue
            for item in emails:
                eid = str(item.get("id") or "")
                if not eid or eid in seen:
                    continue
                subject = str(item.get("subject") or "")
                frm = str(item.get("from") or item.get("sender") or "")
                blob = f"{subject} {frm}".lower()
                likely = any(h in blob for h in SENDER_HINTS) or True
                if not likely:
                    continue
                try:
                    detail = self.get_email(token, eid)
                except TempMailError as exc:
                    self.log(f"[mail] detail error {eid}: {exc}")
                    continue
                seen.add(eid)
                text = self._body_text(detail) + "\n" + subject
                code = self.extract_code(text)
                if code:
                    self.log(f"[mail] code={code} from={frm!r} subject={subject!r}")
                    return code
            time.sleep(self.poll_interval)
        raise TempMailError("verification code timeout")
