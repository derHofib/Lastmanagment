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
        "unit_id": 1, "profile_id": pid,
    }
    r = client.post("/api/stations", json=station)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]

    # Profil in Benutzung -> löschen verboten
    assert client.delete(f"/api/profiles/{pid}").status_code == 409

    # Station aktualisieren
    station["location"] = "Garage"
    assert client.put(f"/api/stations/{sid}", json=station).json()["location"] == "Garage"

    # Verbindungstest ohne echten Wallbox-Server -> offline, kein Fehler
    r = client.post(f"/api/stations/{sid}/test")
    assert r.status_code == 200
    assert r.json()["online"] is False

    # Station löschen, dann Profil löschbar
    assert client.delete(f"/api/stations/{sid}").status_code == 204
    assert client.delete(f"/api/profiles/{pid}").status_code == 204


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


# --- Verteilungshierarchie (Hauptverteilung/Unterverteilung) --------------

def test_boards_root_auto_created(client):
    r = client.get("/api/boards")
    assert r.status_code == 200
    boards = r.json()
    assert len(boards) == 1
    assert boards[0]["parent_board_id"] is None
    assert boards[0]["name"] == "Hauptverteilung"


def test_board_create_requires_parent(client):
    # Ohne parent_board_id -> abgelehnt (Wurzel wird nur automatisch verwaltet)
    r = client.post("/api/boards", json={"name": "UV Garage", "incoming_fuse_a": 35})
    assert r.status_code == 400


def test_board_crud_and_hierarchy(client):
    root_id = client.get("/api/boards").json()[0]["id"]

    r = client.post("/api/boards", json={
        "name": "UV Garage", "parent_board_id": root_id, "incoming_fuse_a": 35,
    })
    assert r.status_code == 201, r.text
    uv_id = r.json()["id"]

    # Unbekannter parent_board_id -> 400
    assert client.post("/api/boards", json={
        "name": "X", "parent_board_id": 9999, "incoming_fuse_a": 16,
    }).status_code == 400

    # Verschachtelte Unterverteilung
    r = client.post("/api/boards", json={
        "name": "UV Garage - Zweig", "parent_board_id": uv_id, "incoming_fuse_a": 16,
    })
    assert r.status_code == 201
    sub_id = r.json()["id"]

    # Zyklus verhindern: UV Garage kann nicht ihrem eigenen Kind unterstellt werden
    r = client.put(f"/api/boards/{uv_id}", json={
        "name": "UV Garage", "parent_board_id": sub_id, "incoming_fuse_a": 35,
    })
    assert r.status_code == 400

    # Wurzel-Status kann nicht geändert werden
    r = client.put(f"/api/boards/{root_id}", json={
        "name": "Hauptverteilung", "parent_board_id": uv_id, "incoming_fuse_a": 63,
    })
    assert r.status_code == 400

    # Löschen mit Kind-Verteiler verboten
    assert client.delete(f"/api/boards/{uv_id}").status_code == 409
    # Blatt-Verteiler ohne Kinder/Stationen löschbar
    assert client.delete(f"/api/boards/{sub_id}").status_code == 204
    assert client.delete(f"/api/boards/{uv_id}").status_code == 204

    # Wurzel kann nicht gelöscht werden
    assert client.delete(f"/api/boards/{root_id}").status_code == 409


def test_station_with_board_and_circuit_breaker(client):
    pid = client.post("/api/profiles", json=EXAMPLE_PROFILE).json()["id"]
    root_id = client.get("/api/boards").json()[0]["id"]
    uv_id = client.post("/api/boards", json={
        "name": "UV Garage", "parent_board_id": root_id, "incoming_fuse_a": 35,
    }).json()["id"]
    sid = client.post("/api/stations", json={
        "name": "Garage links", "ip_address": "192.168.1.50", "profile_id": pid,
    }).json()["id"]

    charge_point = {
        "station_id": sid, "name": "Ladepunkt 1",
        "distribution_board_id": uv_id, "circuit_breaker_a": 16,
        "max_current_a": 32, "min_current_a": 6,
    }
    r = client.post("/api/charge-points", json=charge_point)
    assert r.status_code == 201, r.text
    assert r.json()["circuit_breaker_a"] == 16
    cp_id = r.json()["id"]

    # Unbekannter Verteiler -> 400
    bad = dict(charge_point, distribution_board_id=9999)
    assert client.post("/api/charge-points", json=bad).status_code == 400

    # Unbekannte Station -> 400
    bad2 = dict(charge_point, station_id=9999)
    assert client.post("/api/charge-points", json=bad2).status_code == 400

    # Verteiler mit zugewiesenem Ladepunkt kann nicht gelöscht werden
    assert client.delete(f"/api/boards/{uv_id}").status_code == 409

    client.delete(f"/api/charge-points/{cp_id}")
    assert client.delete(f"/api/boards/{uv_id}").status_code == 204


def test_charge_point_min_max_validation(client):
    pid = client.post("/api/profiles", json=EXAMPLE_PROFILE).json()["id"]
    sid = client.post("/api/stations", json={
        "name": "X", "ip_address": "10.0.0.1", "profile_id": pid,
    }).json()["id"]
    charge_point = {
        "station_id": sid, "name": "CP", "max_current_a": 6, "min_current_a": 16,
    }
    assert client.post("/api/charge-points", json=charge_point).status_code == 422


def test_charge_point_crud_with_schedules_and_dual_connector(client):
    pid = client.post("/api/profiles", json=EXAMPLE_PROFILE).json()["id"]
    sid = client.post("/api/stations", json={
        "name": "Doppel-Wallbox", "ip_address": "192.168.1.60", "profile_id": pid,
    }).json()["id"]

    cp1 = {
        "station_id": sid, "connector_suffix": "_1", "name": "Ladepunkt 1",
        "priority": 5, "max_current_a": 16, "min_current_a": 6,
        "schedules": [
            {"weekdays_mask": 0b0011111, "start_time": "22:00:00", "end_time": "06:00:00"},
        ],
    }
    r = client.post("/api/charge-points", json=cp1)
    assert r.status_code == 201, r.text
    cp1_id = r.json()["id"]
    assert len(r.json()["schedules"]) == 1
    assert r.json()["connector_suffix"] == "_1"

    cp2 = {
        "station_id": sid, "connector_suffix": "_2", "name": "Ladepunkt 2",
        "pv_surplus_only": True, "max_current_a": 16, "min_current_a": 6,
    }
    r2 = client.post("/api/charge-points", json=cp2)
    assert r2.status_code == 201, r2.text
    cp2_id = r2.json()["id"]
    assert r2.json()["pv_surplus_only"] is True

    # Beide Ladepunkte gehören zur selben Station
    listed = client.get("/api/charge-points").json()
    ids = {cp["id"] for cp in listed}
    assert {cp1_id, cp2_id} <= ids

    # Live ohne Regelzyklus -> offline
    live = client.get(f"/api/charge-points/{cp1_id}/live").json()
    assert live["online"] is False

    # Zeitpläne aktualisieren (vollständig ersetzen)
    cp1["schedules"] = []
    r = client.put(f"/api/charge-points/{cp1_id}", json=cp1)
    assert r.status_code == 200
    assert r.json()["schedules"] == []

    assert client.delete(f"/api/charge-points/{cp1_id}").status_code == 204
    assert client.delete(f"/api/charge-points/{cp2_id}").status_code == 204


def test_board_tree_endpoint(client):
    root_id = client.get("/api/boards").json()[0]["id"]
    client.post("/api/boards", json={
        "name": "UV Garage", "parent_board_id": root_id, "incoming_fuse_a": 35,
    })
    r = client.get("/api/boards/tree")
    assert r.status_code == 200
    tree = r.json()
    assert tree["name"] == "Hauptverteilung"
    assert len(tree["children"]) == 1
    assert tree["children"][0]["name"] == "UV Garage"
    assert set(tree["load_a"]) == {"L1", "L2", "L3"}


def test_board_strategy_field_roundtrip(client):
    root_id = client.get("/api/boards").json()[0]["id"]
    r = client.post("/api/boards", json={
        "name": "UV Garage", "parent_board_id": root_id, "incoming_fuse_a": 35,
        "strategy": "priority",
    })
    assert r.status_code == 201, r.text
    uv_id = r.json()["id"]
    assert r.json()["strategy"] == "priority"

    # Ohne Angabe -> None (erbt von übergeordnetem Verteiler/global)
    r = client.get(f"/api/boards/{uv_id}")
    assert r.json()["strategy"] == "priority"

    r = client.put(f"/api/boards/{uv_id}", json={
        "name": "UV Garage", "parent_board_id": root_id, "incoming_fuse_a": 35,
        "strategy": None,
    })
    assert r.status_code == 200
    assert r.json()["strategy"] is None

    tree = client.get("/api/boards/tree").json()
    uv_node = next(c for c in tree["children"] if c["name"] == "UV Garage")
    assert uv_node["strategy"] is None

    client.delete(f"/api/boards/{uv_id}")
