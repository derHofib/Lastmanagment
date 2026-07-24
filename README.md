# Lastmanagement für Ladestationen (Modbus TCP)

Produktionsreife Anwendung zum **statischen und dynamischen Lastmanagement**
von Elektrofahrzeug-Ladestationen. Läuft auf einem Raspberry Pi (ARM, Linux)
und kommuniziert mit den Wallboxen **ausschließlich über Modbus TCP**.

**Kerngedanke:** Es gibt keinen fest verdrahteten Wallbox-Code. Jedes Modell
wird über ein frei konfigurierbares **Geräteprofil** (Modbus-Register-Map)
beschrieben. Neue Ladestationen und neue Modelle lassen sich vollständig über
die Weboberfläche/Datenbank anlegen – ohne den Code zu ändern.

---

## Funktionsumfang

- **Generische Modbus-Schicht:** beliebige Register-Maps je Modell, alle
  Datentypen (`uint16/int16/uint32/int32/float32/float64/bool`) inkl.
  Byte-/Wort-Reihenfolge (Big/Little-Endian, Word-Swap), Skalierung & Offset.
- **Lastmanagement-Engine:** phasengenaue Verteilung (L1/L2/L3), Strategien
  `equal` / `priority` / `fifo` – **global oder individuell je Verteiler**
  (Verteiler ohne eigene Angabe erben die Strategie des übergeordneten
  Verteilers bzw. zuletzt die globale Einstellung), Minimalstrom-Regel
  (Pausieren statt Unterschreiten), Hysterese/Haltezeiten gegen Relais-Flattern.
- **Statisch & dynamisch:** feste Gesamtgrenze oder dynamisch abzüglich der
  Haus-Grundlast (Netzanschlusszähler ebenfalls per Geräteprofil eingebunden).
- **Ladestationen mit mehreren Ladepunkten (Doppel-Wallboxen):** eine
  Ladestation ist die physische Modbus-Verbindung, jeder **Ladepunkt**
  (Connector) wird unabhängig mit eigener Priorität, Phasenanschluss,
  Min/Max-Strom, Verteiler-Zuordnung und Fail-Safe gesteuert.
- **Zeitsteuerung:** je Ladepunkt beliebig viele wiederkehrende
  Wochen-Sperrfenster (Wochentage + Start-/Endzeit, auch über Mitternacht) –
  der Ladepunkt wird während des Fensters automatisch pausiert.
- **PV-Überschussladen:** je Ladepunkt einzeln aktivierbar (`pv_surplus_only`);
  PV-Ladepunkte laden ausschließlich mit gemessenem Netzeinspeise-Überschuss,
  nachrangig zu regulär ladenden Punkten und begrenzt durch die jeweils noch
  freie Absicherung im Verteilungsbaum.
- **Fail-Safe:** definierter sicherer Zustand pro Station bei
  Kommunikationsverlust; harte Summenbegrenzung je Phase; konservative
  Reservierung für unerreichbare Stationen.
- **§14a EnWG:** optionaler Modus, der bei aktivem Steuersignal die verfügbare
  Gesamtleistung auf einen konfigurierbaren Minimalwert begrenzt.
- **REST-API + Web-UI:** Dashboard (Live-Last pro Phase), Verwaltung von
  Stationen und Profilen, Import/Export von Profilen als JSON, Einstellungen.
- **Betrieb:** systemd-Service, Logging nach journald + optional Datei,
  Zeitstempel in Europe/Berlin, ressourcenschonend (asyncio, SQLite).
- **Lizenzsystem:** Free/Pro/Enterprise mit jeweils **vollem
  Funktionsumfang** – einziger Unterschied ist die Anzahl der
  Ladestationen (Free: 2). Signierte Schlüssel (Ed25519), lokal und offline
  geprüft, keine Aktivierung über das Internet nötig.
- **Voltibus Cloud (optional):** separater, selbst gehosteter Dienst zur
  Fernansicht der eigenen Installation(en) – Registrierung, Login,
  Status-Dashboard. Ausfälle der Cloud-Verbindung beeinflussen die lokale
  Regelung nie (siehe `README-cloud.md`).
- **MQTT-Anbindung (optional):** veröffentlicht denselben Status zusätzlich
  per MQTT (z. B. für Home Assistant/Node-RED), inkl. Home-Assistant-MQTT-
  Discovery. Ebenfalls fehlertolerant – keine Auswirkung auf die Regelung.
- **Energiefluss-Visualisierung:** Dashboard-Karte mit PV-Überschuss/
  Netzbezug → Ladepunkte als Fluss-Diagramm, zusätzlich zu den
  Phasen-Balken.
- **Individuell anpassbares Dashboard:** Karten ein-/ausblenden und
  umsortieren, Layout wird serverseitig gespeichert.
- **Baukasten-Topologie:** Verteiler und Ladestationen frei per Drag&Drop
  auf einer Fläche anordnen; die Verbindungen zeigen den aktuellen
  Stromfluss (animierte Linien, Dicke nach Auslastung).

---

## Online-Demo der Weboberfläche (GitHub Pages)

Die **Weboberfläche** lässt sich zum Ansehen/Kontrollieren direkt über GitHub
Pages hosten. Wichtig: GitHub Pages führt **kein Python-Backend** aus – die
veröffentlichte Version läuft daher im **Demo-Modus** mit simulierten API-Daten
im Browser (`app/web/static/demo.js`, aktiviert über `config.js`). Damit sind
Dashboard, Stationen, Profil-Editor und Einstellungen voll bedienbar; Änderungen
werden lokal im Browser (localStorage) gehalten. Echte Modbus-Kommunikation
findet nicht statt – dafür läuft die App auf dem Raspberry Pi.

Einrichtung (einmalig):

1. In GitHub unter **Settings → Pages → Build and deployment → Source** die
   Option **GitHub Actions** wählen (der Workflow versucht dies auch automatisch).
2. Der Workflow `.github/workflows/pages.yml` veröffentlicht bei jedem Push die
   Oberfläche. Die URL erscheint anschließend unter **Settings → Pages** bzw.
   im Actions-Lauf (typisch `https://<user>.github.io/Lastmanagment/`).

---

## Architektur / Modulstruktur

```
app/
  config.py           Infrastruktur-Einstellungen (.env / config.yaml)
  db.py               SQLAlchemy-Engine + Session (SQLite, WAL)
  licensing.py         Signierte Lizenzschlüssel (Ed25519) prüfen, Tier-Limits
  models/             ORM-Datenmodell (Profile, Register, Stationen, Config, Lizenz, Messwerte)
  schemas.py          Pydantic-Schemas (API-Validierung)
  modbus/
    codec.py          Generische Kodierung/Dekodierung (Herzstück, voll getestet)
    client.py         Asynchroner Modbus-Client pro Station (Timeout/Reconnect/Clamping)
    runtime.py        Entkoppelte Laufzeit-Dataclasses (kein ORM im async-Loop)
  loadmanager/
    engine.py         Reine Verteil-Logik (phasengenau, Strategien)
    smoothing.py      Hysterese / Mindesthaltezeiten
    safety.py         Fail-Safe & konservative Kapazitätsreservierung
    schedule.py       Zeitsteuerung (wiederkehrende Sperrfenster)
    status_builder.py  Baut den Systemzustand (für API, Cloud-Relay, MQTT gemeinsam)
    cloud_relay.py     Optionales "Phone-Home" an Voltibus Cloud
    mqtt_publisher.py  Optionale MQTT-Anbindung (inkl. Home-Assistant-Discovery)
    loop.py           Regelzyklus (Polling → Verteilung → Sollwerte schreiben)
  api/                REST-Endpunkte (Profile, Stationen, Ladepunkte, Verteiler, Config, Lizenz, Status)
  web/static/         Schlankes Web-UI (HTML/JS + Fetch)
  main.py             FastAPI-App + Lifespan (startet Regelzyklus + Cloud-Relay)
cloud/                Voltibus Cloud – separater, mandantenfähiger Dienst
                      (Registrierung/Login/Fernansicht), siehe README-cloud.md
tools/
  simulator.py         Modbus-TCP-Wallbox-Simulator zum Verifizieren von Profilen (statisch)
  scenario.py          Zeitgeraffter Demo-Ablauf: mehrere Ladepunkte, PV-Tagesgang, §14a, Auto-Setup
  licensing/keygen.py  Vendor-Tool: signierte Lizenzschlüssel ausstellen (nicht Teil der App)
tests/                Unit- und Integrationstests (Hauptanwendung)
```

Datenfluss im Regelzyklus (`app/loadmanager/loop.py`):

1. Konfiguration + aktive Stationen aus der DB laden.
2. Alle Stationen **parallel und isoliert** pollen (eigener Timeout je Station).
3. Effektive Grenze bestimmen (`§14a` hat Vorrang, dann ggf. dynamisch abzüglich
   Grundlast).
4. Reserven für unerreichbare Stationen abziehen (konservativ).
5. Verteilung berechnen (harte Grenzgarantie **auf jeder Ebene der
   Verteilungshierarchie** – siehe unten).
6. Sollwerte glätten (Hysterese) und nur bei Änderung schreiben.
7. Messwerte persistieren + Momentaufnahme für die API aktualisieren.

---

## Datenmodell (Kurzüberblick)

- **DeviceProfile** – Modell/Geräteprofil mit Standard-Byte/Word-Order und
  `RegisterMapping[]`.
- **RegisterMapping** – einzelnes Register: `key`, `role` (read/write),
  `register_address`, `function_code` (3/4/6/16), `data_type`, optional
  `byte_order`/`word_order`, `scale`, `offset`, `unit`, `enum_map`,
  `writable_min`/`writable_max`.
- **ChargingStation** – nur die physische Modbus-Verbindung: `name`,
  `location`, `ip_address`, `tcp_port`, `unit_id`, `profile_id`. Hat eine
  Relation `charge_points` (1:n).
- **ChargePoint** – der eigentlich steuerbare/zuteilbare Ladepunkt einer
  Station: `connector_suffix` (`""` für einen einzelnen Ladepunkt, `"_1"`/
  `"_2"` … für Doppel-Wallboxen – bestimmt, welche Register im Geräteprofil
  zu diesem Ladepunkt gehören, z. B. `set_current_1`/`set_current_2`),
  `phase_config` (`1p_l1`/`1p_l2`/`1p_l3`/`3p`), `priority`, `min/max_current_a`,
  `distribution_board_id`, `circuit_breaker_a`, `enabled`, `safe_state`,
  `pv_surplus_only`. Hat eine Relation `schedules` (1:n).
- **ChargeSchedule** – wiederkehrendes Sperrfenster eines Ladepunkts:
  `weekdays_mask` (Bit 0 = Montag … Bit 6 = Sonntag), `start_time`, `end_time`
  (`start_time > end_time` = Fenster über Mitternacht).
- **GlobalConfig** – `grid_limit_current_a`, `management_mode`,
  `distribution_strategy`, `poll_interval_s`, Hysterese, Fail-Safe, Zähler,
  §14a.
- **Measurement** – optionale Messwert-Historie je Ladepunkt.
- **DistributionBoard** – Verteiler (Hauptverteilung/Unterverteilung) im
  Verteilungsbaum: `name`, `parent_board_id` (selbstreferenziell, `None` =
  Wurzel/Hauptverteilung), `incoming_fuse_a` (Absicherung der Zuleitung),
  `priority`, `strategy` (optional; `None` = erbt vom übergeordneten
  Verteiler bzw. zuletzt von der globalen Strategie).

---

## Verteilungshierarchie (Hauptverteilung / Unterverteilung / Abgänge)

Bildet die reale Elektroinstallation ab: **Hauptverteilung** (Hausanschluss,
eigene Absicherung) → **Unterverteilungen** (jeweils mit eigener
Zuleitungs-Absicherung) → **Abgänge zu den Ladestationen** (jeweils mit
eigener Absicherung, `ChargingStation.circuit_breaker_a` – separat vom
technischen `max_current_a` der Wallbox selbst).

Das Lastmanagement (`app/loadmanager/engine.py::allocate_tree`) hält die
Absicherung auf **jeder Ebene** des Baums ein – nicht nur die
Gesamtstromgrenze am Hausanschluss. Jeder Unterverteiler-Zweig wird dabei
wie eine virtuelle 3-phasige Station behandelt (begrenzt durch seine eigene
Absicherung), wodurch die bereits getestete `allocate()`-Logik unverändert
rekursiv wiederverwendet wird. Ohne konfigurierte Unterverteiler entspricht
das Verhalten exakt der bisherigen flachen Zuteilung.

Verwaltung über den Reiter **„Verteilung"** im Web-UI: Hauptverteilung wird
automatisch angelegt, Unterverteilungen und deren Zuordnung zu Ladestationen
lassen sich dort anlegen/bearbeiten/löschen; die Baumansicht zeigt die
aktuelle Auslastung je Phase gegen die jeweilige Absicherung.

Beispiel: Zwei Ladestationen (je bis 32 A fähig) hängen an einer
Unterverteilung mit nur 20 A Absicherung → jede bekommt trotz höherer
Netzgrenze und höherem Wallbox-Maximum nur 10 A zugeteilt (Summe = 20 A).

**Verteilstrategie pro Verteiler:** Jeder Verteiler kann optional eine eigene
Strategie (`equal`/`priority`/`fifo`) festlegen, die für seinen kompletten
Unterbaum gilt, sofern ein Kind-Verteiler nicht selbst wieder etwas anderes
festlegt (Kaskadierung wie bei CSS). Ohne Angabe gilt die global eingestellte
Strategie. So kann z. B. eine Unterverteilung im Carport `priority` fahren,
während der Rest der Anlage `equal` verteilt.

---

## Doppel-Wallboxen (zwei Ladepunkte je Station)

Eine **Ladestation** ist nur die physische Modbus-TCP-Verbindung (IP, Port,
Unit-ID, Geräteprofil). Jede Station hat einen oder mehrere **Ladepunkte**
(Connectoren), die unabhängig voneinander mit eigener Priorität, eigenem
Phasenanschluss, eigenen Strom-Grenzen, eigener Verteiler-Zuordnung und
eigenem Fail-Safe-Verhalten gesteuert werden. Bei einer Doppel-Wallbox liest
der Modbus-Client die Register **einmal pro Verbindung** (ein Roundtrip
deckt beide Ladepunkte ab); welche Register zu welchem Ladepunkt gehören,
bestimmt die Konvention `key + connector_suffix` (z. B. `set_current_1`/
`set_current_2`, `current_l1_1`/`current_l1_2`) – das Geräteprofil braucht
dafür keine Schemaänderung, `key` ist bereits ein freier String.

Im Web-UI unter *Ladestationen*: pro Station eine Karte mit ihren
Ladepunkten; „+ Ladepunkt" fügt einer bestehenden Station einen zweiten
Ladepunkt (mit eigenem `connector_suffix`) hinzu.

---

## Zeitsteuerung (wiederkehrende Sperrfenster)

Jeder Ladepunkt kann beliebig viele wiederkehrende Wochenfenster erhalten
(Wochentage per Checkbox + Start-/Endzeit). Liegt die aktuelle lokale Zeit
(Europe/Berlin) innerhalb eines passenden Fensters, wird der Ladepunkt für
diesen Regelzyklus automatisch pausiert – wie ein zeitgesteuertes
`enabled=false`. Fenster über Mitternacht (z. B. 22:00–06:00) werden korrekt
behandelt: Der frühe Teil nach Mitternacht gehört noch zum Fenster des
Starttags. Das manuelle Ein-/Ausschalten (`enabled`) bleibt davon unabhängig
für den Sofort-Fall erhalten. Reine Logik in `app/loadmanager/schedule.py`
(`is_blocked()`), getestet in `tests/test_schedule.py`.

---

## PV-Überschussladen

Ladepunkte mit aktiviertem `pv_surplus_only` laden **ausschließlich** mit
gemessenem Netzeinspeise-Überschuss (erfordert dynamisches Lastmanagement
mit angebundenem Netzanschlusszähler) und **nachrangig** zu regulär
ladenden Ladepunkten. Die Zuteilung läuft pro Regelzyklus zweistufig:

1. **Lauf 1:** alle regulären Ladepunkte gegen die normale Kapazität
   (Netzgrenze bzw. dynamische Grenze abzüglich Grundlast) – unverändertes
   `allocate_tree()`.
2. **Lauf 2:** die noch freie Absicherung je Verteiler-Knoten wird aus
   Lauf 1 berechnet (`remaining_capacity_tree()`); PV-Ladepunkte werden
   gegen `min(gemessener PV-Überschuss, freie Absicherung)` verteilt.

Ohne verfügbaren Überschuss (oder ohne Zählerdaten) bekommen PV-Ladepunkte
konsequent 0 A – kein stillschweigender Rückfall auf Netzladen.

---

## Lizenzsystem

Drei Stufen (`app.models.base.LicenseTier`): **Free**, **Pro**, **Enterprise**.
Jede Stufe hat **denselben vollen Funktionsumfang** – der einzige
Unterschied ist die Obergrenze der Anzahl **Ladestationen** (nicht
Ladepunkte): Free = 2, Pro = 10, Enterprise = unbegrenzt
(`app/licensing.py::TIER_LIMITS`). Ohne aktivierten Schlüssel gilt Free.

Lizenzschlüssel sind mit **Ed25519** signiert und werden vollständig
**offline und lokal** geprüft (`app/licensing.py::verify_license_key`) – es
ist keine Internetverbindung oder Aktivierung über einen zentralen Server
nötig. Der private Signaturschlüssel liegt ausschließlich beim Herausgeber
(nicht im Repository); neue Schlüssel werden mit `tools/licensing/keygen.py`
ausgestellt:

```bash
# Einmalig: Schlüsselpaar erzeugen, öffentlichen Teil in
# app/licensing.py::PUBLIC_KEY_B64 eintragen
python -m tools.licensing.keygen generate-keypair --out private_key.pem

# Für jeden Kunden: einen Pro-Schlüssel ausstellen
python -m tools.licensing.keygen issue --tier pro --customer "Kundenname" \
    --private-key private_key.pem
```

Aktivierung über den Reiter **„Lizenz"** im Web-UI (Schlüssel einfügen,
„Aktivieren") oder direkt per `POST /api/license/activate`. Wird beim
Anlegen einer weiteren Ladestation das Limit erreicht, antwortet die API
mit `402 Payment Required` und einer sprechenden Fehlermeldung.

---

## Cloud-Anbindung (Voltibus Cloud, optional)

Ein **separater, selbst gehosteter Dienst** (`cloud/`, siehe
[`README-cloud.md`](README-cloud.md)) für die Fernansicht: Konto anlegen,
eine oder mehrere Installationen koppeln, Status von unterwegs einsehen.
Läuft bewusst getrennt vom Lastmanagement selbst (eigene Datenbank, eigene
Authentifizierung) – Voltibus bleibt single-tenant und lokal.

Die lokale Installation sendet dazu periodisch ihren Status (dieselben
Daten wie `GET /api/status`) an den Cloud-Dienst
(`app/loadmanager/cloud_relay.py`), einstellbar unter *Einstellungen →
Cloud-Anbindung* (Cloud-URL, Installations-Token, Intervall). Jeder Fehler
(Cloud nicht erreichbar, falsches Token) wird geloggt und ignoriert – **die
lokale Regelung ist davon nie betroffen**, die Anbindung läuft als eigener,
unabhängiger Hintergrund-Task.

---

## MQTT-Anbindung (optional)

Veröffentlicht denselben Systemzustand zusätzlich per MQTT
(`app/loadmanager/mqtt_publisher.py`) – für Home Assistant, Node-RED oder
eigene Auswertungen. Einstellbar unter *Einstellungen → MQTT* (Broker-Host/
Port, Zugangsdaten, Topic-Präfix, Intervall). Läuft als eigener,
unabhängiger Hintergrund-Task nach demselben Prinzip wie die
Cloud-Anbindung: Verbindungsfehler werden geloggt und ignoriert, **die
lokale Regelung ist nie betroffen**.

Veröffentlicht werden Topics wie `<prefix>/status/phase_load_a/L1`,
`<prefix>/chargepoints/<id>/current_l1`, `.../setpoint_a`, `.../power` usw.
Mit aktivierter **Home-Assistant-MQTT-Discovery** (Standard: an) erscheinen
passende Sensoren automatisch in Home Assistant, ohne manuelle
YAML-Konfiguration dort.

Bewusst **nur Publish** – Sollwerte lassen sich nicht per MQTT setzen. Das
wäre eine eigene Sicherheits-/Validierungsfrage (wer darf die Ladeleistung
per MQTT ändern?) und ist nicht Teil dieser Anbindung.

---

## Energiefluss-Visualisierung

Das Dashboard zeigt zusätzlich zu den Phasen-Balken eine Energiefluss-Karte:
PV-Überschuss (falls dynamisches Lastmanagement mit Zähler aktiv und
Überschuss vorhanden) und Netzbezug fließen zu den Ladepunkten, mit
Leistungsangabe je Zweig und animierten Verbindungslinien (Liniendicke/
-geschwindigkeit nach Leistung skaliert, respektiert
`prefers-reduced-motion`).

Die Leistung wird bevorzugt aus vorhandenen `active_power`-Registern der
Ladepunkte gebildet; ist keines vorhanden, aus der Summe der Phasenströme
geschätzt (`Strom × 230 V`) – **eine vereinfachte Näherung**, keine echte
Wirkleistungsmessung. Der PV-Überschuss stammt aus
`phase_surplus_a` (`GET /api/status`), demselben Wert, den auch die
PV-Überschussladen-Logik verwendet.

---

## Individuell anpassbares Dashboard

Über den Button **„Anpassen"** auf dem Dashboard lassen sich die vier Karten
(Gesamtlast pro Phase, Systemzustand, Energiefluss, Ladepunkte-Tabelle)
einzeln ein-/ausblenden und mit Pfeiltasten umsortieren. Das Layout wird als
JSON in `GlobalConfig.dashboard_layout` gespeichert (`PUT /api/config`) und
gilt geräteübergreifend für alle, die auf dieselbe Installation zugreifen.

---

## Baukasten-Topologie (Stromfluss-Visualisierung)

Der Reiter **„Topologie"** zeigt Verteiler und Ladestationen als frei
verschiebbare Kästchen auf einer Fläche (Baukasten-Prinzip): per Ziehen
(Pointer-Events, kein externes Framework) beliebig anordnen, die Position
wird beim Loslassen automatisch gespeichert (`canvas_x`/`canvas_y` an
`DistributionBoard` bzw. `ChargingStation`, über die bestehenden
`PUT /api/boards/{id}`/`PUT /api/stations/{id}`-Endpunkte). Noch nicht
platzierte Knoten erscheinen automatisch in einem Rasterlayout nach
Verteilungshierarchie; der Button **„Auto-Anordnen"** setzt alle Positionen
darauf zurück.

Die Verbindungen zwischen Verteilern und Ladestationen sind animierte
SVG-Linien, deren Dicke und Animationsgeschwindigkeit die aktuelle Auslastung
bzw. den fließenden Ladestrom widerspiegeln (dieselbe Optik wie die
Energiefluss-Karte, aber mit beliebigen Linien statt eines festen
horizontalen Layouts) und respektieren `prefers-reduced-motion`.

---

## Geräteprofil (Import/Export-Format)

Ein Profil ist als JSON import-/exportierbar (siehe
`examples/beispiel_wallbox.json`):

```json
{
  "name": "Beispiel-Wallbox 22kW",
  "manufacturer": "Muster GmbH",
  "default_unit_id": 1,
  "byte_order": "big",
  "word_order": "big",
  "registers": [
    { "key": "current_l1",   "role": "read",  "function_code": 4, "register_address": 100, "data_type": "float32", "scale": 1,   "unit": "A" },
    { "key": "active_power", "role": "read",  "function_code": 4, "register_address": 120, "data_type": "uint32",  "scale": 1,   "unit": "W" },
    { "key": "energy_total", "role": "read",  "function_code": 4, "register_address": 130, "data_type": "uint32",  "scale": 0.1, "unit": "kWh" },
    { "key": "charge_status","role": "read",  "function_code": 3, "register_address": 200, "data_type": "uint16",
      "enum_map": { "0": "Verfügbar", "1": "Fahrzeug verbunden", "2": "Lädt", "3": "Fehler" } },
    { "key": "set_current",  "role": "write", "function_code": 6, "register_address": 300, "data_type": "uint16", "writable_min": 6, "writable_max": 32, "unit": "A" },
    { "key": "enable",       "role": "write", "function_code": 6, "register_address": 301, "data_type": "uint16", "writable_min": 0, "writable_max": 1 }
  ]
}
```

**Semantische Schlüssel**, die die Engine nutzt:
`current_l1`/`current_l2`/`current_l3` (Ist-Ströme), `active_power`,
`energy_total`, `charge_status` (mit `enum_map`), `set_current` (Sollstrom,
Schreibregister), `enable` (0/1, Schreibregister zum Pausieren).

---

## Installation (Raspberry Pi)

```bash
sudo apt update && sudo apt install -y python3 python3-venv git
sudo mkdir -p /opt/lastmanagement && sudo chown "$USER" /opt/lastmanagement
git clone https://github.com/derhofib/lastmanagment /opt/lastmanagement
cd /opt/lastmanagement

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp config.example.yaml config.yaml   # anpassen
```

### Start (Entwicklung)

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
# Web-UI:  http://<pi-ip>:8000/
# API-Doku: http://<pi-ip>:8000/docs
```

### Betrieb als systemd-Service

```bash
# Dedizierten Benutzer anlegen (empfohlen)
sudo useradd --system --home /opt/lastmanagement lastmanager
sudo chown -R lastmanager:lastmanager /opt/lastmanagement

sudo cp systemd/lastmanagement.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lastmanagement
sudo systemctl status lastmanagement
journalctl -u lastmanagement -f
```

---

## Bedienung (Kurzanleitung)

1. **Geräteprofil anlegen** (oder `examples/beispiel_wallbox.json` importieren)
   unter *Geräteprofile*.
2. **Ladestation anlegen** unter *Ladestationen* (IP, Port, Unit-ID, Profil).
   Mit *Test* die Verbindung/Profilzuordnung prüfen.
3. **Ladepunkt(e) anlegen** an der Station (bei Doppel-Wallboxen zwei, mit
   unterschiedlichem `connector_suffix`): Phasenanschluss, Priorität,
   Min/Max-Strom, Verteiler-Zuordnung, Abgangssicherung, Fail-Safe-Verhalten,
   optional PV-Überschussladen und Zeitplan (Wochentage + Uhrzeit).
4. **Verteiler** anlegen/bearbeiten unter *Verteilung*, optional mit eigener
   Verteilstrategie je Zweig.
5. **Einstellungen** vornehmen: Netzgrenze pro Phase, Modus (statisch/dynamisch),
   globale Strategie, Polling-Intervall, Hysterese, Fail-Safe, optional Zähler
   und §14a.
6. **Dashboard** zeigt Live-Last pro Phase gegen die Grenze und alle Ladepunkte
   mit Ist-/Soll-Strom und Status.
7. **Lizenz** (optional) unter *Lizenz* aktivieren, falls mehr als 2
   Ladestationen benötigt werden. **Cloud-Anbindung** (optional) unter
   *Einstellungen*, falls eine selbst gehostete Voltibus-Cloud-Instanz zur
   Fernansicht existiert (siehe `README-cloud.md`).

---

## Test mit dem Simulator (ohne Hardware)

Für einen schnellen, statischen Verbindungstest (konstanter Strom, ein
einzelner Ladepunkt):

```bash
# Terminal 1: simulierte Wallbox auf Port 5020
source .venv/bin/activate
python -m tools.simulator --port 5020 --current 16 --status 2

# Terminal 2: Anwendung
uvicorn app.main:app --port 8000
```

Dann im Web-UI das Beispielprofil importieren, eine Station auf
`127.0.0.1:5020` anlegen und das Dashboard beobachten.

### Realistisches Zeitraffer-Szenario (`tools/scenario.py`)

Für einen aussagekräftigeren Test – kein konstanter Volllast-Dauerzustand,
sondern ein realistischer Tagesablauf: mehrere Ladepunkte (inkl. einer
Doppel-Wallbox), Fahrzeuge stecken sich zufällig an/ab und laden mit
Rampe/Plateau/Taper statt Sprungfunktion, ein Netzanschlusszähler mit
Tagesgang (PV-Überschuss mittags, Bezug abends), ein periodisch
geschaltetes §14a-Steuersignal. Das Skript richtet die laufende Anwendung
dafür automatisch per REST-API ein (Profile, Verteiler, Stationen,
Ladepunkte, Konfiguration).

```bash
# Terminal 1: Anwendung
uvicorn app.main:app --port 8000

# Terminal 2: Zeitraffer-Szenario (1 reale Minute = 1 simulierte Stunde)
python -m tools.scenario
```

Danach im Web-UI zusehen: Dashboard (Energiefluss-Karte reagiert auf den
PV-Überschuss), *Verteilung* (Unterverteilung „UV Carport" mit 25 A
Absicherung, an der beide Connectoren der Doppel-Wallbox hängen), *Lizenz*
unverändert nutzbar. Optionen: `--speed` (Zeitraffer-Faktor, Standard 60),
`--start-hour` (simulierte Startuhrzeit), `python -m tools.scenario --help`
für alle Parameter.

**Hinweis:** Der Zeitplan (`ChargeSchedule`) eines Ladepunkts wertet immer
die echte Systemzeit aus (das ist im Produktivbetrieb so gewollt) – das
Skript legt das Demo-Sperrfenster deshalb relativ zur tatsächlichen
Startzeit an und gibt das reale Zeitfenster beim Start auf der Konsole aus.

---

## Sicherheit & Fail-Safe

- **Harte Grenze:** Die Summe der Sollströme je Phase überschreitet nie
  `grid_limit_current_a` (bzw. die effektive Grenze). Sollwerte werden
  konservativ abgerundet.
- **Kommunikationsverlust:** Bleibt eine Station länger als
  `fail_safe_after_s` ohne gültige Kommunikation, geht sie in ihren
  `safe_state` (`block` = 0 A / `min_current` = Minimalstrom). Für die
  Kapazitätsberechnung wird ihr Strom konservativ reserviert.
- **Watchdog-Annahme:** Kann die Steuerung eine Box nicht mehr erreichen,
  wird angenommen, dass die Box (sofern das Modell dies unterstützt) über
  ihren eigenen Watchdog selbst herunterregelt. Dieses Verhalten ist
  modellabhängig und sollte je Wallbox dokumentiert/geprüft werden.
- **Schreibgrenzen:** Jeder Sollwert wird gegen `writable_min/max` des
  Registers und `station.max_current_a` geklemmt.
- **Logging:** Jede Sollwert-Änderung wird mit Zeitstempel und Begründung
  protokolliert (journald/Datei).

### Dynamisches Lastmanagement – Zähler-Ausfall

Ist im dynamischen Modus der Netzanschlusszähler nicht lesbar, wird
**konservativ** entschieden: Die verfügbare Kapazität wird auf 0 gesetzt, d. h.
die Ladepunkte werden auf ihren sicheren Zustand zurückgefahren, bis der Zähler
wieder liefert. So kann eine unbekannte Grundlast den Hausanschluss nicht
überlasten.

---

## Tests / Qualitätssicherung

```bash
source .venv/bin/activate
pip install -r requirements.txt
pytest
```

Abgedeckt:

- **Codec** (`tests/test_codec.py`): alle Datentypen, Byte-/Wort-Reihenfolgen,
  Skalierung/Offset, Roundtrips, Grenzwert-Clamping, `enum_map`.
- **Lastmanagement** (`tests/test_loadmanager.py`): equal/priority/fifo,
  Phasengenauigkeit, Minimalstrom-Regel, Grenzwert-Garantie unter Zufallslasten,
  Hysterese/Haltezeiten, hierarchische Verteilungshierarchie (`allocate_tree`)
  inkl. Engpass auf Unterverteiler-Ebene und Zufallsbäumen, Strategie-Vererbung
  über mehrere Verteiler-Ebenen, zweistufige PV-Überschuss-Zuteilung
  (`remaining_capacity_tree`/`subtree_totals`).
- **Zeitsteuerung** (`tests/test_schedule.py`): Sperrfenster innerhalb/außerhalb
  eines Tages, Mitternachts-Wrap (inkl. Wochentag-Grenzfälle), mehrere Fenster.
- **API** (`tests/test_api.py`): CRUD für Profile/Stationen/Ladepunkte
  (inkl. Zeitpläne, Doppel-Ladepunkt-Szenario, PV-Flag)/Config/Verteiler
  (inkl. Strategie-Feld), Validierung (u. a. Zyklenschutz im
  Verteilungsbaum), Import/Export, Status, Lizenz-Aktivierung und
  Stationslimit (402).
- **Modbus-Integration** (`tests/test_integration_modbus.py`): End-to-end gegen
  einen echten `pymodbus`-TCP-Server inkl. Schreiben & Clamping.
- **Lizenz** (`tests/test_licensing.py`): Ed25519-Signaturprüfung (eigenes
  Testschlüsselpaar), manipulierte Payload/Signatur, Tier-Limits.
- **Cloud-Relay** (`tests/test_cloud_relay.py`): sendet Status nur bei
  aktivierter Anbindung, schluckt Verbindungsfehler/HTTP-Fehlerstatus ohne
  zu werfen.
- **Voltibus Cloud** (`cloud/tests/`, separat mit `pytest cloud/tests`
  auszuführen): Registrierung/Login/Logout, Installationen (Anlegen,
  Token-Rotation, Löschen, Mandantentrennung zwischen Nutzern), Ingest mit
  gültigem/ungültigem Token.
- **MQTT-Anbindung** (`tests/test_mqtt_publisher.py`): sendet nur bei
  aktivierter Anbindung und vorhandenem Host, Status- und
  Discovery-Topics (retained) korrekt getrennt, schluckt Verbindungs-/
  Publish-Fehler ohne zu werfen.

---

## Konfiguration

Infrastruktur-Einstellungen (DB-Pfad, Timeouts, Logging, Zeitzone) kommen aus
`config.yaml` bzw. Umgebungsvariablen (Präfix `LM_`, z. B. `LM_DATABASE_URL`,
`LM_PORT`, `LM_MODBUS_TIMEOUT_S`, `LM_LOG_LEVEL`, `LM_LOG_FILE`). Reihenfolge:
Defaults → `config.yaml` → Umgebungsvariablen.

Betriebliche Grenzwerte (Netzgrenze, Modus, Strategie, Hysterese, §14a, Zähler)
werden zur Laufzeit über das Web-UI / `PUT /api/config` gepflegt und in der
Datenbank gehalten.

---

## REST-API (Auszug)

| Methode | Pfad | Zweck |
|---|---|---|
| GET/POST | `/api/profiles` | Geräteprofile auflisten/anlegen |
| PUT/DELETE | `/api/profiles/{id}` | Profil ändern/löschen |
| POST | `/api/profiles/import` | Profil aus JSON importieren |
| GET | `/api/profiles/{id}/export` | Profil als JSON exportieren |
| GET/POST | `/api/stations` | Ladestationen (Modbus-Verbindungen) auflisten/anlegen |
| PUT/DELETE | `/api/stations/{id}` | Station ändern/löschen (Kaskade auf ihre Ladepunkte) |
| POST | `/api/stations/{id}/test` | Verbindungs-/Profiltest |
| GET/POST | `/api/charge-points` | Ladepunkte auflisten/anlegen (inkl. Zeitpläne) |
| PUT/DELETE | `/api/charge-points/{id}` | Ladepunkt ändern/löschen |
| GET | `/api/charge-points/{id}/live` | aktuelle Messwerte des Ladepunkts |
| GET/POST | `/api/boards` | Verteiler auflisten/anlegen |
| PUT/DELETE | `/api/boards/{id}` | Verteiler ändern/löschen |
| GET | `/api/boards/tree` | kompletter Verteilungsbaum inkl. Live-Auslastung |
| GET/PUT | `/api/config` | globale Grenzwerte & Modus (inkl. Cloud-/MQTT-Anbindung) |
| POST | `/api/config/cloud-relay/test` | Cloud-Verbindung sofort testen |
| POST | `/api/config/mqtt/test` | MQTT-Verbindung sofort testen |
| GET | `/api/status` | Systemzustand (Last/Reserve/PV-Überschuss/Ladepunkte) |
| GET | `/api/license` | aktuelle Lizenzstufe & Stationsnutzung |
| POST | `/api/license/activate` | Lizenzschlüssel aktivieren |

Vollständige, interaktive Dokumentation unter `/docs` (Swagger UI). Die REST-API
von Voltibus Cloud (separater Dienst) ist in `README-cloud.md` beschrieben.
