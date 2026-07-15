from __future__ import annotations

import random
import re
import time
from typing import Any

from curl_cffi import requests

from grokreg.util.log import LogFn, default_log

# xAI signup codes look like "ABC-DEF" (subject often: "ABC-DEF xAI")
CODE_XAI_RE = re.compile(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b", re.I)
CODE_DIGIT_RE = re.compile(
    r"(?:verification\s+code|your\s+code|confirm(?:ation)?\s+code|验证码)[:\s]+(\d{4,8})",
    re.I,
)
CODE_FALLBACK_RE = re.compile(r"\b(\d{6})\b")
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
    def extract_code(text: str, subject: str = "") -> str | None:
        """Match sample extract_verification_code: prefer ABC-DEF xAI codes."""
        if subject:
            m = re.search(r"^([A-Z0-9]{3}-[A-Z0-9]{3})\s+xAI", subject, re.I)
            if m:
                return m.group(1)
            m = CODE_XAI_RE.search(subject)
            if m:
                return m.group(1)
        blob = f"{subject}\n{text or ''}"
        m = CODE_XAI_RE.search(blob)
        if m:
            return m.group(1)
        m = CODE_DIGIT_RE.search(blob)
        if m:
            return m.group(1)
        m = CODE_FALLBACK_RE.search(blob)
        return m.group(1) if m else None

    def _body_text(self, detail: dict[str, Any]) -> str:
        parts: list[str] = []
        for key in ("subject", "text", "text_body", "body", "plain", "html", "html_body"):
            v = detail.get(key)
            if isinstance(v, str) and v.strip():
                parts.append(v)
        content = detail.get("content")
        if isinstance(content, dict):
            for key in ("text", "html", "plain"):
                v = content.get(key)
                if isinstance(v, str) and v.strip():
                    parts.append(v)
        # strip simple HTML tags for code extraction
        raw = "\n".join(parts)
        return re.sub(r"<[^>]+>", " ", raw)

    def wait_code(
        self,
        token: str,
        *,
        timeout: float | None = None,
        since: float | None = None,
    ) -> str:
        deadline = time.time() + (timeout if timeout is not None else self.timeout)
        seen: set[str] = set()
        last_log = 0.0
        while time.time() < deadline:
            try:
                emails = self.list_emails(token)
            except TempMailError as exc:
                self.log(f"[mail] list error: {exc}")
                time.sleep(self.poll_interval)
                continue
            now = time.time()
            if now - last_log >= 15:
                self.log(f"[mail] polling… inbox={len(emails)} left={int(deadline - now)}s")
                last_log = now
            for item in emails:
                eid = str(item.get("id") or item.get("_id") or item.get("message_id") or "")
                subject = str(item.get("subject") or "")
                frm = str(item.get("from") or item.get("sender") or item.get("from_address") or "")
                # try extract from list subject first (xAI puts code in subject)
                code = self.extract_code("", subject)
                if code:
                    self.log(f"[mail] code={code} from={frm!r} subject={subject!r} (list)")
                    return code
                if not eid or eid in seen:
                    # still try body fields on list item
                    preview = " ".join(
                        str(item.get(k) or "")
                        for k in ("text", "body", "preview", "snippet", "intro")
                    )
                    code = self.extract_code(preview, subject)
                    if code:
                        self.log(f"[mail] code={code} from={frm!r} subject={subject!r} (preview)")
                        return code
                    if eid:
                        seen.add(eid)
                    continue
                try:
                    detail = self.get_email(token, eid)
                except TempMailError as exc:
                    self.log(f"[mail] detail error {eid}: {exc}")
                    seen.add(eid)
                    continue
                seen.add(eid)
                det_subject = str(detail.get("subject") or subject)
                text = self._body_text(detail)
                code = self.extract_code(text, det_subject)
                if code:
                    self.log(f"[mail] code={code} from={frm!r} subject={det_subject!r}")
                    return code
            time.sleep(self.poll_interval)
        raise TempMailError("verification code timeout")
