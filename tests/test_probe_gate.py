from grokreg.probe.build45 import probe_chat_completions, probe_responses


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
        # sample CPA headers (grok-pager)
        assert "X-XAI-Token-Auth" in k["headers"] or "x-xai-token-auth" in {
            x.lower() for x in k["headers"]
        }
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


def test_probe_ok_responses(monkeypatch):
    def fake_post(url, **k):
        assert url.endswith("/responses")
        assert k["json"]["model"] == "grok-4.5"
        assert k["json"]["input"] == "ping"
        return _Resp(
            200,
            "{}",
            {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "pong"}],
                    }
                ]
            },
        )

    monkeypatch.setattr("grokreg.probe.build45.requests.post", fake_post)
    r = probe_responses("tok")
    assert r["ok"] is True
    assert r["endpoint"] == "responses"
    assert "pong" in r["text"]
