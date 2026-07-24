# Voltibus Cloud

Ein **eigenständiger, mandantenfähiger Dienst** für die Fernansicht eigener
Voltibus-Installationen ("Phone-Home"). Läuft bewusst **getrennt** vom
eigentlichen Lastmanagement (`app/`): Voltibus selbst bleibt single-tenant
und läuft weiter lokal auf dem Raspberry Pi; dieser Dienst läuft auf einem
Host deiner Wahl (VPS, NAS, eigener Server) und verwaltet Benutzerkonten
sowie den jeweils letzten Status mehrerer Installationen.

**Wichtig:** Dieser Dienst ist Code, kein Hosting-Angebot. Domain, Server
und TLS-Zertifikat musst du selbst bereitstellen.

---

## Architektur

```
Raspberry Pi (Voltibus)  --POST /api/ingest-->  Voltibus Cloud  <--Browser--  du
  app/loadmanager/cloud_relay.py                 cloud/
  (Bearer-Token je Installation)                 (eigene DB, eigene Auth)
```

- Ein Voltibus-Cloud-Konto kann **mehrere Installationen** verwalten (z. B.
  Zuhause + Ferienhaus).
- Jede Installation bekommt beim Anlegen ein **Token**, das lokal unter
  *Einstellungen → Cloud-Anbindung* eingetragen wird.
- Es wird **nur der jeweils letzte Status** gespeichert (kein Verlauf/keine
  Historie) – bewusste Datensparsamkeit, siehe unten.

---

## Lokale Entwicklung / Test

```bash
python3 -m venv .venv-cloud
source .venv-cloud/bin/activate
pip install -r requirements-cloud.txt

export LMC_JWT_SECRET="dev-secret-nicht-fuer-produktion"
uvicorn cloud.main:app --port 9000
# Weboberfläche: http://127.0.0.1:9000/
```

---

## Produktivbetrieb

### 1. Installation

```bash
sudo useradd --system --home /opt/voltibus-cloud voltibus-cloud
sudo mkdir -p /opt/voltibus-cloud/data
sudo chown -R voltibus-cloud:voltibus-cloud /opt/voltibus-cloud

git clone https://github.com/derhofib/lastmanagment /opt/voltibus-cloud/src
cd /opt/voltibus-cloud
python3 -m venv .venv
source .venv/bin/activate
pip install -r src/requirements-cloud.txt
ln -s src/cloud cloud   # ExecStart erwartet "cloud.main:app" im Arbeitsverzeichnis
```

### 2. Geheimnisse setzen

`systemd/voltibus-cloud.service` nach `/etc/systemd/system/` kopieren und
**vor dem ersten Start** anpassen:

- `LMC_JWT_SECRET` – fester, zufälliger Wert (`openssl rand -hex 32`).
  Ohne diesen wird bei jedem Neustart ein neuer Zufallswert erzeugt und
  **alle Sessions werden ungültig**.
- `LMC_COOKIE_SECURE=true`, sobald ein TLS-Reverse-Proxy davor steht (siehe
  unten) – sonst verweigert der Browser das Session-Cookie über HTTPS nicht,
  sendet es aber auch über HTTP, was ohne Proxy für lokale Tests in Ordnung,
  im Internet aber unsicher ist.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now voltibus-cloud
journalctl -u voltibus-cloud -f
```

### 3. TLS über einen Reverse-Proxy (Beispiel: Caddy)

Caddy holt automatisch ein Let's-Encrypt-Zertifikat, sobald die Domain auf
den Server zeigt:

```
cloud.deine-domain.de {
    reverse_proxy 127.0.0.1:9000
}
```

Alternativ nginx/Apache mit beliebigem ACME-Client – wichtig ist nur, dass
`LMC_COOKIE_SECURE=true` gesetzt ist, sobald Nutzer über `https://` zugreifen.

---

## Eine lokale Voltibus-Installation anbinden

1. Auf der Cloud-Weboberfläche registrieren/anmelden.
2. **„+ Installation"** klicken, Namen vergeben → das angezeigte **Token
   wird nur einmal angezeigt**, sofort kopieren.
3. In der lokalen Voltibus-Weboberfläche unter **Einstellungen →
   Cloud-Anbindung**: Cloud-URL (z. B. `https://cloud.deine-domain.de`) und
   das Token eintragen, „Verbindung jetzt testen" klicken, dann aktivieren
   und speichern.
4. Die Installation erscheint innerhalb des konfigurierten Intervalls
   (Standard 30 s) als „Online" im Cloud-Dashboard.

Ein Token kann jederzeit über „Token neu erzeugen" ausgetauscht werden
(altes Token wird sofort ungültig).

---

## REST-API (Auszug)

| Methode | Pfad | Zweck |
|---|---|---|
| POST | `/api/auth/register` | Konto anlegen (setzt Session-Cookie) |
| POST | `/api/auth/login` | Anmelden (setzt Session-Cookie) |
| POST | `/api/auth/logout` | Abmelden |
| GET | `/api/auth/me` | Aktuell angemeldeter Nutzer |
| GET/POST | `/api/installations` | Installationen auflisten/anlegen |
| POST | `/api/installations/{id}/rotate-token` | Neues Token erzeugen |
| DELETE | `/api/installations/{id}` | Installation löschen |
| POST | `/api/ingest` | Status-Update (Bearer-Token der Installation) |

Vollständige, interaktive Dokumentation unter `/docs` (Swagger UI).

---

## Datensparsamkeit & Sicherheit

- **Keine Verlaufsdaten:** Es wird ausschließlich der jeweils letzte Status
  je Installation gespeichert, keine Zeitreihe. Damit sammelt der Dienst
  keine langfristige Energie-Nutzungshistorie.
- **Passwörter:** gehasht mit bcrypt, nie im Klartext gespeichert.
- **Installations-Token:** wie ein Passwort behandelt (nur gehasht in der
  DB), Klartext ausschließlich einmalig bei Erzeugung/Rotation sichtbar.
- **Rate-Limiting:** ein einfacher In-Memory-Limiter auf `/api/auth/login`
  (pro Prozess) erschwert Brute-Force-Versuche.

## Bekannte Einschränkungen (v1)

- Keine E-Mail-Versendung: weder Adressverifizierung noch
  Passwort-Reset per Mail. Ein vergessenes Passwort erfordert aktuell
  direkten Datenbankzugriff.
- Der Login-Rate-Limiter wirkt nur pro Prozess; bei mehreren Instanzen
  hinter einem Load-Balancer bräuchte man einen gemeinsamen Store (z. B.
  Redis).
- Keine Kopplung an die Voltibus-Lizenzstufe (jede Lizenzstufe kann die
  Cloud-Anbindung nutzen) – eine naheliegende spätere Erweiterung.
