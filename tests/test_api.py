"""Integrationstests der REST-API (Profile, Stationen, Config, Status)."""

EXAMPLE_PROFILE = {
    "name": "Beispiel-Wallbox 22kW",
    "manufacturer": "Muster GmbH",
    "default_unit_id": 1,
    "byte_order": "big",
    "word_order": "big",
    "registers": [
        {"key": "current_l1", "role": "read", "function_code": 4, "register_address": 100,
         "data_type": "float32", "scale": 1, "unit": "A"},
        {"key": "charge_status", "role": "read", "function_code": 3, "register_address": 200,
         "data_type": "uint16", "enum_map": {"0": "Verfügbar", "2": "Lädt"}},
        {"key": "set_current", "role": "write", "function_code": 6, "register_address": 300,
         "data_type": "uint16", "unit": "A", "writable_min": 6, "writable_max": 32},
        {"key": "enable", "role": "write", "function_code": 6, "register_address": 301,
         "data_type": "uint16", "writable_min": 0, "writable_max": 1},
    ],
}


def test_profile_crud_and_export(client):
    # Anlegen
    r = client.post("/api/profiles", json=EXAMPLE_PROFILE)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert len(r.json()["registers"]) == 4

    # Doppelter Name -> 409
    assert client.post("/api/profiles", json=EXAMPLE_PROFILE).status_code == 409

    # Lesen
    assert client.get(f"/api/profiles/{pid}").json()["name"] == "Beispiel-Wallbox 22kW"

    # Export -> Import (unter neuem Namen)
    exported = client.get(f"/api/profiles/{pid}/export").json()
    assert exported["registers"][0]["key"] == "current_l1"
    exported["name"] = "Kopie"
    r2 = client.post("/api/profiles/import", json=exported)
    assert r2.status_code == 201
    assert len(r2.json()["registers"]) == 4


def test_register_validation_rejects_bad_function_code(client):
    bad = dict(EXAMPLE_PROFILE)
    bad = {**EXAMPLE_PROFILE, "name": "Bad", "registers": [
        {"key": "x", "role": "read", "function_code": 6, "register_address": 1,
         "data_type": "uint16"},
    ]}
    r = client.post("/api/profiles", json=bad)
    assert r.status_code == 422


def test_station_crud(client):
    pid = client.post("/api/profiles", json=EXAMPLE_PROFILE).json()["id"]
    station = {
        "name": "Garage links", "ip_address": "192.168.1.50", "tcp_port": 502,
        "unit_id": 1, "profile_id": pid, "phase_config": "3p", "priority": 5,
        "max_current_a": 32, "min_current_a": 6, "enabled": True, "safe_state": "block",
    }
    r = client.post("/api/stations", json=station)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]

    # Live ohne Regelzyklus -> offline
    live = client.get(f"/api/stations/{sid}/live").json()
    assert live["online"] is False

    # Profil in Benutzung -> löschen verboten
    assert client.delete(f"/api/profiles/{pid}").status_code == 409

    # Station aktualisieren
    station["priority"] = 9
    assert client.put(f"/api/stations/{sid}", json=station).json()["priority"] == 9

    # Station löschen, dann Profil löschbar
    assert client.delete(f"/api/stations/{sid}").status_code == 204
    assert client.delete(f"/api/profiles/{pid}").status_code == 204


def test_station_min_max_validation(client):
    pid = client.post("/api/profiles", json=EXAMPLE_PROFILE).json()["id"]
    station = {
        "name": "X", "ip_address": "10.0.0.1", "profile_id": pid,
        "max_current_a": 6, "min_current_a": 16,
    }
    assert client.post("/api/stations", json=station).status_code == 422


def test_config_update(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    assert r.json()["grid_limit_current_a"] == 63.0

    r = client.put("/api/config", json={
        "grid_limit_current_a": 32.0,
        "management_mode": "static",
        "distribution_strategy": "priority",
    })
    assert r.status_code == 200
    assert r.json()["grid_limit_current_a"] == 32.0
    assert r.json()["distribution_strategy"] == "priority"


def test_status_endpoint(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert "phase_load_a" in body
    assert set(body["phase_load_a"]) == {"L1", "L2", "L3"}


def test_healthz(client):
    assert client.get("/healthz").json()["status"] == "ok"
