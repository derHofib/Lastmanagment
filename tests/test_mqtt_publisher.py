"""Tests für app.loadmanager.mqtt_publisher: MQTT-Fehler dürfen die lokale
Regelung nie stören – jeder Verbindungsfehler wird geloggt und geschluckt."""

import pytest

import app.loadmanager.mqtt_publisher as mqtt_publisher_module
from app.db import session_scope
from app.loadmanager.mqtt_publisher import MqttPublisherService
from app.models import GlobalConfig


class _FakeAiomqttClient:
    """Ersetzt aiomqtt.Client: zeichnet publish()-Aufrufe auf, kann Fehler simulieren."""

    calls: list[dict] = []
    raise_exc: Exception | None = None
    raise_on_connect = False

    def __init__(self, hostname=None, port=None, username=None, password=None):
        type(self).connect_args = {"hostname": hostname, "port": port, "username": username, "password": password}

    async def __aenter__(self):
        if type(self).raise_on_connect:
            raise type(self).raise_exc or ConnectionError("connect failed")
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def publish(self, topic, payload=None, retain=False, **kwargs):
        type(self).calls.append({"topic": topic, "payload": payload, "retain": retain})
        if type(self).raise_exc is not None and not type(self).raise_on_connect:
            raise type(self).raise_exc


@pytest.fixture(autouse=True)
def _fake_aiomqtt(monkeypatch):
    _FakeAiomqttClient.calls = []
    _FakeAiomqttClient.raise_exc = None
    _FakeAiomqttClient.raise_on_connect = False
    monkeypatch.setattr(mqtt_publisher_module.aiomqtt, "Client", _FakeAiomqttClient)
    yield


def _configure(**kwargs):
    with session_scope() as session:
        cfg = GlobalConfig.get_or_create(session)
        for key, value in kwargs.items():
            setattr(cfg, key, value)


@pytest.mark.asyncio
async def test_publish_once_noop_when_disabled(client):
    svc = MqttPublisherService()
    interval = await svc._publish_once()
    assert _FakeAiomqttClient.calls == []
    assert interval == 10.0  # GlobalConfig-Default für mqtt_interval_s


@pytest.mark.asyncio
async def test_publish_once_noop_when_enabled_but_missing_host(client):
    _configure(mqtt_enabled=True)
    svc = MqttPublisherService()
    await svc._publish_once()
    assert _FakeAiomqttClient.calls == []


@pytest.mark.asyncio
async def test_publish_once_publishes_state_and_discovery_when_enabled(client):
    _configure(mqtt_enabled=True, mqtt_host="broker.local", mqtt_interval_s=20.0, mqtt_ha_discovery=True)
    svc = MqttPublisherService()
    interval = await svc._publish_once()

    assert interval == 20.0
    assert len(_FakeAiomqttClient.calls) > 0
    state_calls = [c for c in _FakeAiomqttClient.calls if c["topic"].startswith("voltibus/status/")]
    discovery_calls = [c for c in _FakeAiomqttClient.calls if c["topic"].startswith("homeassistant/")]
    assert state_calls, "erwarte Status-Topics"
    assert discovery_calls, "erwarte HA-Discovery-Topics bei aktiviertem mqtt_ha_discovery"
    assert all(c["retain"] is True for c in discovery_calls)
    assert all(c["retain"] is False for c in state_calls)


@pytest.mark.asyncio
async def test_publish_once_skips_discovery_when_disabled(client):
    _configure(mqtt_enabled=True, mqtt_host="broker.local", mqtt_ha_discovery=False)
    svc = MqttPublisherService()
    await svc._publish_once()

    discovery_calls = [c for c in _FakeAiomqttClient.calls if c["topic"].startswith("homeassistant/")]
    assert discovery_calls == []


@pytest.mark.asyncio
async def test_publish_once_uses_configured_topic_prefix(client):
    _configure(mqtt_enabled=True, mqtt_host="broker.local", mqtt_topic_prefix="myprefix")
    svc = MqttPublisherService()
    await svc._publish_once()

    assert any(c["topic"].startswith("myprefix/status/") for c in _FakeAiomqttClient.calls)


@pytest.mark.asyncio
async def test_publish_once_swallows_connection_error(client):
    _configure(mqtt_enabled=True, mqtt_host="broker.local", mqtt_interval_s=25.0)
    _FakeAiomqttClient.raise_on_connect = True
    svc = MqttPublisherService()

    interval = await svc._publish_once()  # darf NICHT werfen

    assert interval == 25.0


@pytest.mark.asyncio
async def test_publish_once_swallows_publish_error(client):
    _configure(mqtt_enabled=True, mqtt_host="broker.local", mqtt_interval_s=12.0)
    _FakeAiomqttClient.raise_exc = OSError("broken pipe")
    svc = MqttPublisherService()

    interval = await svc._publish_once()  # darf NICHT werfen

    assert interval == 12.0


@pytest.mark.asyncio
async def test_test_now_reports_missing_host(client):
    svc = MqttPublisherService()
    result = await svc.test_now()
    assert result == {"ok": False, "error": "MQTT-Host fehlt"}


@pytest.mark.asyncio
async def test_test_now_reports_success_and_ignores_enabled_flag(client):
    # mqtt_enabled bleibt False -> test_now() muss trotzdem senden
    _configure(mqtt_host="broker.local")
    svc = MqttPublisherService()
    result = await svc.test_now()
    assert result == {"ok": True, "error": None}
    assert len(_FakeAiomqttClient.calls) > 0


@pytest.mark.asyncio
async def test_test_now_swallows_connection_error(client):
    _configure(mqtt_host="broker.local")
    _FakeAiomqttClient.raise_on_connect = True
    _FakeAiomqttClient.raise_exc = ConnectionError("refused")
    svc = MqttPublisherService()
    result = await svc.test_now()
    assert result["ok"] is False
    assert "refused" in result["error"]
