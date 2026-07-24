"""Integrationstests der Voltibus-Cloud-API (Auth, Installationen, Ingest)."""


def test_register_sets_session_and_rejects_duplicate(client):
    r = client.post("/api/auth/register", json={"email": "a@example.com", "password": "supersecret1"})
    assert r.status_code == 201, r.text
    assert r.json()["email"] == "a@example.com"

    # Session-Cookie ist gesetzt -> direkt angemeldet
    r_me = client.get("/api/auth/me")
    assert r_me.status_code == 200
    assert r_me.json()["email"] == "a@example.com"

    r_dup = client.post("/api/auth/register", json={"email": "a@example.com", "password": "otherpass1"})
    assert r_dup.status_code == 409


def test_register_rejects_short_password(client):
    r = client.post("/api/auth/register", json={"email": "b@example.com", "password": "short"})
    assert r.status_code == 422


def test_login_logout_flow(client):
    client.post("/api/auth/register", json={"email": "c@example.com", "password": "supersecret1"})
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401

    r_bad = client.post("/api/auth/login", json={"email": "c@example.com", "password": "wrongpass"})
    assert r_bad.status_code == 401

    r_ok = client.post("/api/auth/login", json={"email": "c@example.com", "password": "supersecret1"})
    assert r_ok.status_code == 200
    assert client.get("/api/auth/me").status_code == 200

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_installations_require_login(client):
    assert client.get("/api/installations").status_code == 401
    assert client.post("/api/installations", json={"name": "X"}).status_code == 401


def test_installation_crud_and_token_lifecycle(client):
    client.post("/api/auth/register", json={"email": "d@example.com", "password": "supersecret1"})

    r = client.post("/api/installations", json={"name": "Zuhause"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["online"] is False
    assert body["last_snapshot"] is None
    token = body["token"]
    inst_id = body["id"]

    r_list = client.get("/api/installations")
    assert r_list.status_code == 200
    assert len(r_list.json()) == 1
    assert "token" not in r_list.json()[0]  # Token wird nach Erzeugung nie wieder ausgegeben

    r_rot = client.post(f"/api/installations/{inst_id}/rotate-token")
    assert r_rot.status_code == 200
    new_token = r_rot.json()["token"]
    assert new_token != token

    r_del = client.delete(f"/api/installations/{inst_id}")
    assert r_del.status_code == 204
    assert client.get("/api/installations").json() == []


def test_installations_are_scoped_per_user(client):
    client.post("/api/auth/register", json={"email": "owner@example.com", "password": "supersecret1"})
    inst = client.post("/api/installations", json={"name": "Owner-Anlage"}).json()

    client.post("/api/auth/logout")
    client.post("/api/auth/register", json={"email": "other@example.com", "password": "supersecret1"})

    # Andere Nutzer sehen die Installation nicht und können sie nicht löschen
    assert client.get("/api/installations").json() == []
    assert client.delete(f"/api/installations/{inst['id']}").status_code == 404


def test_ingest_requires_valid_bearer_token(client):
    client.post("/api/auth/register", json={"email": "e@example.com", "password": "supersecret1"})
    inst = client.post("/api/installations", json={"name": "Zuhause"}).json()

    assert client.post("/api/ingest", json={}).status_code == 401
    assert client.post(
        "/api/ingest", json={}, headers={"Authorization": "Bearer wrong-token"}
    ).status_code == 401

    r = client.post(
        "/api/ingest",
        json={"phase_load_a": {"L1": 1.0, "L2": 2.0, "L3": 3.0}},
        headers={"Authorization": f"Bearer {inst['token']}"},
    )
    assert r.status_code == 204


def test_ingest_updates_dashboard_snapshot(client):
    client.post("/api/auth/register", json={"email": "f@example.com", "password": "supersecret1"})
    inst = client.post("/api/installations", json={"name": "Zuhause"}).json()

    client.post(
        "/api/ingest",
        json={"phase_load_a": {"L1": 5.0, "L2": 6.0, "L3": 7.0}, "active_charge_points": 1},
        headers={"Authorization": f"Bearer {inst['token']}"},
    )

    listed = client.get("/api/installations").json()[0]
    assert listed["online"] is True
    assert listed["last_snapshot"]["phase_load_a"] == {"L1": 5.0, "L2": 6.0, "L3": 7.0}
    assert listed["last_snapshot"]["active_charge_points"] == 1
    assert listed["last_seen_at"] is not None


def test_old_token_invalid_after_rotation(client):
    client.post("/api/auth/register", json={"email": "g@example.com", "password": "supersecret1"})
    inst = client.post("/api/installations", json={"name": "Zuhause"}).json()
    old_token = inst["token"]

    r_rot = client.post(f"/api/installations/{inst['id']}/rotate-token")
    new_token = r_rot.json()["token"]

    r_old = client.post("/api/ingest", json={}, headers={"Authorization": f"Bearer {old_token}"})
    assert r_old.status_code == 401

    r_new = client.post("/api/ingest", json={}, headers={"Authorization": f"Bearer {new_token}"})
    assert r_new.status_code == 204


def test_healthz(client):
    assert client.get("/healthz").json()["status"] == "ok"
