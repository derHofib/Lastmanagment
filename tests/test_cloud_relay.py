"""Tests für app.loadmanager.cloud_relay: Cloud-Fehler dürfen die lokale
Regelung nie stören – jeder Netzwerkfehler wird geloggt und geschluckt."""

import pytest

import app.loadmanager.cloud_relay as cloud_relay_module
from app.db import session_scope
from app.loadmanager.cloud_relay import CloudRelayService
from app.models import GlobalConfig


class _FakeResponse:
    def __init__(self, status_code=204):
        self.status_code = status_code


class _FakeAsyncClient:
    """Ersetzt httpx.AsyncClient: zeichnet Aufrufe auf, kann Fehler simulieren."""

    calls: list[dict] = []
    raise_exc: Exception | None = None
    response_status = 204

    def __init__(self, timeout=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, json=None, headers=None):
        type(self).calls.append({"url": url, "json": json, "headers": headers})
        if type(self).raise_exc is not None:
            raise type(self).raise_exc
        return _FakeResponse(type(self).response_status)


@pytest.fixture(autouse=True)
def _fake_httpx(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.raise_exc = None
    _FakeAsyncClient.response_status = 204
    monkeypatch.setattr(cloud_relay_module.httpx, "AsyncClient", _FakeAsyncClient)
    yield


def _configure(**kwargs):
    with session_scope() as session:
        cfg = GlobalConfig.get_or_create(session)
        for key, value in kwargs.items():
            setattr(cfg, key, value)


@pytest.mark.asyncio
async def test_send_once_noop_when_disabled(client):
    relay = CloudRelayService()
    interval = await relay._send_once()
    assert _FakeAsyncClient.calls == []
    assert interval == 30.0  # GlobalConfig-Default für cloud_relay_interval_s


@pytest.mark.asyncio
async def test_send_once_noop_when_enabled_but_missing_url(client):
    _configure(cloud_relay_enabled=True, cloud_relay_token="tok123")
    relay = CloudRelayService()
    await relay._send_once()
    assert _FakeAsyncClient.calls == []


@pytest.mark.asyncio
async def test_send_once_posts_current_status_when_enabled(client):
    _configure(
        cloud_relay_enabled=True, cloud_relay_url="http://cloud.example/",
        cloud_relay_token="tok123", cloud_relay_interval_s=15.0,
    )
    relay = CloudRelayService()
    interval = await relay._send_once()

    assert interval == 15.0
    assert len(_FakeAsyncClient.calls) == 1
    call = _FakeAsyncClient.calls[0]
    # Doppelter Slash wird bereinigt (rstrip("/") vor "/api/ingest")
    assert call["url"] == "http://cloud.example/api/ingest"
    assert call["headers"]["Authorization"] == "Bearer tok123"
    assert "phase_load_a" in call["json"]


@pytest.mark.asyncio
async def test_send_once_swallows_connection_error(client):
    _configure(
        cloud_relay_enabled=True, cloud_relay_url="http://cloud.example",
        cloud_relay_token="tok", cloud_relay_interval_s=20.0,
    )
    _FakeAsyncClient.raise_exc = ConnectionError("refused")
    relay = CloudRelayService()

    interval = await relay._send_once()  # darf NICHT werfen

    assert interval == 20.0


@pytest.mark.asyncio
async def test_send_once_swallows_http_error_status(client):
    _configure(
        cloud_relay_enabled=True, cloud_relay_url="http://cloud.example",
        cloud_relay_token="tok", cloud_relay_interval_s=10.0,
    )
    _FakeAsyncClient.response_status = 401
    relay = CloudRelayService()

    interval = await relay._send_once()  # darf NICHT werfen

    assert interval == 10.0


@pytest.mark.asyncio
async def test_test_now_reports_missing_config(client):
    relay = CloudRelayService()
    result = await relay.test_now()
    assert result == {"ok": False, "error": "Cloud-URL oder Token fehlt"}


@pytest.mark.asyncio
async def test_test_now_reports_success_and_ignores_enabled_flag(client):
    # cloud_relay_enabled bleibt False -> test_now() muss trotzdem senden
    _configure(cloud_relay_url="http://cloud.example", cloud_relay_token="tok")
    relay = CloudRelayService()
    result = await relay.test_now()
    assert result == {"ok": True, "error": None}
    assert len(_FakeAsyncClient.calls) == 1


@pytest.mark.asyncio
async def test_test_now_reports_http_error_status(client):
    _configure(cloud_relay_url="http://cloud.example", cloud_relay_token="tok")
    _FakeAsyncClient.response_status = 401
    relay = CloudRelayService()
    result = await relay.test_now()
    assert result["ok"] is False
    assert "401" in result["error"]


@pytest.mark.asyncio
async def test_test_now_swallows_connection_error(client):
    _configure(cloud_relay_url="http://cloud.example", cloud_relay_token="tok")
    _FakeAsyncClient.raise_exc = ConnectionError("refused")
    relay = CloudRelayService()
    result = await relay.test_now()
    assert result["ok"] is False
    assert "refused" in result["error"]
