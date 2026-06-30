from fastapi.testclient import TestClient
from forven.api import app


def test_shutdown_rejects_non_localhost_client():
    client = TestClient(app, client=("8.8.8.8", 12345))
    r = client.post("/api/shutdown")
    assert r.status_code == 403


def test_shutdown_accepts_localhost_and_schedules_exit(monkeypatch):
    called = {}
    monkeypatch.setattr("os._exit", lambda code: called.setdefault("code", code))
    monkeypatch.setattr("os.kill", lambda pid, sig: None)  # avoid real signal
    client = TestClient(app, client=("127.0.0.1", 12345))
    r = client.post("/api/shutdown")
    assert r.status_code == 202
    assert r.json().get("status") == "shutting_down"


def test_shutdown_rejects_browser_drive_by(monkeypatch):
    """A page the operator visits POSTs from the loopback socket (client_host is
    127.0.0.1), so the localhost check passes — the cross-site Origin must not."""
    monkeypatch.delenv("FORVEN_CORS_ORIGINS", raising=False)
    monkeypatch.setattr("os._exit", lambda code: None)
    monkeypatch.setattr("os.kill", lambda pid, sig: None)
    client = TestClient(app, client=("127.0.0.1", 12345))
    r = client.post("/api/shutdown", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
