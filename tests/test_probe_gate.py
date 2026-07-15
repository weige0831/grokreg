from grokreg.probe.build45 import probe_chat_completions


class _Resp:
    def __init__(self, status, text="", data=None):
        self.status_code = status
        self.text = text
        self._data = data

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


def test_probe_403(monkeypatch):
    def fake_post(*a, **k):
        assert "/chat/completions" in a[0] or "/chat/completions" in str(k.get("url", a[0] if a else ""))
        return _Resp(403, '{"error":"permission-denied"}')

    monkeypatch.setattr("grokreg.probe.build45.requests.post", fake_post)
    r = probe_chat_completions("tok")
    assert r["ok"] is False
    assert r["code"] == "probe_403"
    assert r["endpoint"] == "chat/completions"


def test_probe_439(monkeypatch):
    def fake_post(*a, **k):
        return _Resp(439, "quota-like")

    monkeypatch.setattr("grokreg.probe.build45.requests.post", fake_post)
    r = probe_chat_completions("tok")
    assert r["ok"] is False
    assert r["code"] == "probe_439"


def test_probe_429_fail(monkeypatch):
    """check_alive marks 429 as not uploadable; we keep fail for gate."""

    def fake_post(*a, **k):
        return _Resp(429, "rate limit")

    monkeypatch.setattr("grokreg.probe.build45.requests.post", fake_post)
    r = probe_chat_completions("tok")
    assert r["ok"] is False
    assert r["code"] == "probe_429"


def test_probe_ok_chat_completions(monkeypatch):
    def fake_post(url, **k):
        assert "chat/completions" in url
        assert k["json"]["model"] == "grok-4.5"
        assert k["json"]["messages"][0]["content"] == "1+1=?"
        assert k["headers"]["x-grok-client-version"] == "0.2.99"
        return _Resp(
            200,
            "{}",
            {
                "choices": [
                    {"message": {"role": "assistant", "content": "2"}},
                ]
            },
        )

    monkeypatch.setattr("grokreg.probe.build45.requests.post", fake_post)
    r = probe_chat_completions("tok")
    assert r["ok"] is True
    assert r["code"] == "probe_ok"
    assert r["detail"] == "answered"
    assert "2" in r["text"]
