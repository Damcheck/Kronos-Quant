from forven import bot
from forven import reporter


def test_owner_guard_disabled_denies_all_operators(monkeypatch):
    monkeypatch.setattr(bot, "OWNER_ID", 0)
    assert bot._owner_guard_enabled() is False
    # Fail closed: with no discord_owner_id configured, nobody is the operator.
    assert bot._is_authorized_operator(123456789) is False


def test_owner_guard_enabled_requires_matching_operator(monkeypatch):
    monkeypatch.setattr(bot, "OWNER_ID", 4242)
    assert bot._owner_guard_enabled() is True
    assert bot._is_authorized_operator(4242) is True
    assert bot._is_authorized_operator(1111) is False


def test_discord_audit_uses_gateway_token_for_all_routed_channels(monkeypatch):
    monkeypatch.setattr(bot, "CHANNELS", {"general": "1", "heartbeat": "2", "alerts": "3", "research": "4"})
    monkeypatch.setattr(reporter, "AGENT_CHANNEL_MAP", {"agent-a": "research", "agent-b": "research"})
    monkeypatch.setattr(bot, "get_bot_token", lambda: "main-token")
    monkeypatch.setattr(bot, "load_config", lambda: {"discord_owner_id": 0})

    class _DummyResponse:
        def __init__(self, status_code: int, payload: dict | None = None):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = ""

        def json(self):
            return self._payload

    class _DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, headers=None):
            return _DummyResponse(200, {"id": url.rsplit("/", 1)[-1]})

        def post(self, url, headers=None, json=None):
            return _DummyResponse(200, {"id": "m1"})

    monkeypatch.setattr("httpx.Client", _DummyClient)

    result = bot.run_discord_audit(send_probe=False)
    assert result["status"] == "ok"
    assert result["summary"]["failed"] == 0
    assert {item["channel_alias"] for item in result["results"]} == {"general", "heartbeat", "alerts", "research"}
