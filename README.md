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
  `equal` / `priority` / `fifo`, Minimalstrom-Regel (Pausieren statt
  Unterschreiten), Hysterese/Haltezeiten gegen Relais-Flattern.
- **Statisch & dynamisch:** feste Gesamtgrenze oder dynamisch abzüglich der
  Haus-Grundlast (Netzanschlusszähler ebenfalls per Geräteprofil eingebunden).
- **Fail-Safe:** definierter sicherer Zustand pro Station bei
  Kommunikationsverlust; harte Summenbegrenzung je Phase; konservative
  Reservierung für unerreichbare Stationen.
- **§14a EnWG:** optionaler Modus, der bei aktivem Steuersignal die verfügbare
  Gesamtleistung auf einen konfigurierbaren Minimalwert begrenzt.
- **REST-API + Web-UI:** Dashboard (Live-Last pro Phase), Verwaltung von
  Stationen und Profilen, Import/Export von Profilen als JSON, Einstellungen.
- **Betrieb:** systemd-Service, Logging nach journald + optional Datei,
  Zeitstempel in Europe/Berlin, ressourcenschonend (asyncio, SQLite).

---

## Architektur / Modulstruktur

```
app/
  config.py           Infrastruktur-Einstellungen (.env / config.yaml)
  db.py               SQLAlchemy-Engine + Session (SQLite, WAL)
  models/             ORM-Datenmodell (Profile, Register, Stationen, Config, Messwerte)
  schemas.py          Pydantic-Schemas (API-Validierung)
  modbus/
    codec.py          Generische Kodierung/Dekodierung (Herzstück, voll getestet)
    client.py         Asynchroner Modbus-Client pro Station (Timeout/Reconnect/Clamping)
    runtime.py        Entkoppelte Laufzeit-Dataclasses (kein ORM im async-Loop)
  loadmanager/
    engine.py         Reine Verteil-Logik (phasengenau, Strategien)
    smoothing.py      Hysterese / Mindesthaltezeiten
    safety.py         Fail-Safe & konservative Kapazitätsreservierung
    loop.py           Regelzyklus (Polling → Verteilung → Sollwerte schreiben)
  api/                REST-Endpunkte (Profile, Stationen, Config, Status)
  web/static/         Schlankes Web-UI (HTML/JS + Fetch)
  main.py             FastAPI-App + Lifespan (startet Regelzyklus)
tools/simulator.py    Modbus-TCP-Wallbox-Simulator zum Verifizieren von Profilen
tests/                Unit- und Integrationstests
```

Datenfluss im Regelzyklus (`app/loadmanager/loop.py`):

1. Konfiguration + aktive Stationen aus der DB laden.
2. Alle Stationen **parallel und isoliert** pollen (eigener Timeout je Station).
3. Effektive Grenze bestimmen (`§14a` hat Vorrang, dann ggf. dynamisch abzüglich
   Grundlast).
4. Reserven für unerreichbare Stationen abziehen (konservativ).
5. Verteilung berechnen (harte Grenzgarantie je Phase).
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
- **ChargingStation** – konkrete Box: IP/Port/Unit-ID, Profil, `phase_config`
  (`1p_l1`/`1p_l2`/`1p_l3`/`3p`), `priority`, `min/max_current_a`, `safe_state`.
- **GlobalConfig** – `grid_limit_current_a`, `management_mode`,
  `distribution_strategy`, `poll_interval_s`, Hysterese, Fail-Safe, Zähler,
  §14a.
- **Measurement** – optionale Messwert-Historie.

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
2. **Ladestation anlegen** unter *Ladestationen* (IP, Port, Unit-ID, Profil,
   Phasenanschluss, Priorität, Min/Max-Strom, Fail-Safe-Verhalten). Mit
   *Test* die Verbindung/Profilzuordnung prüfen.
3. **Einstellungen** vornehmen: Netzgrenze pro Phase, Modus (statisch/dynamisch),
   Strategie, Polling-Intervall, Hysterese, Fail-Safe, optional Zähler und §14a.
4. **Dashboard** zeigt Live-Last pro Phase gegen die Grenze und alle Ladepunkte
   mit Ist-/Soll-Strom und Status.

---

## Test mit dem Simulator (ohne Hardware)

```bash
# Terminal 1: simulierte Wallbox auf Port 5020
source .venv/bin/activate
python -m tools.simulator --port 5020 --current 16 --status 2

# Terminal 2: Anwendung
uvicorn app.main:app --port 8000
```

Dann im Web-UI das Beispielprofil importieren, eine Station auf
`127.0.0.1:5020` anlegen und das Dashboard beobachten.

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
  Hysterese/Haltezeiten.
- **API** (`tests/test_api.py`): CRUD für Profile/Stationen/Config, Validierung,
  Import/Export, Status.
- **Modbus-Integration** (`tests/test_integration_modbus.py`): End-to-end gegen
  einen echten `pymodbus`-TCP-Server inkl. Schreiben & Clamping.

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
| GET/POST | `/api/stations` | Stationen auflisten/anlegen |
| PUT/DELETE | `/api/stations/{id}` | Station ändern/löschen |
| GET | `/api/stations/{id}/live` | aktuelle Messwerte |
| POST | `/api/stations/{id}/test` | Verbindungs-/Profiltest |
| GET/PUT | `/api/config` | globale Grenzwerte & Modus |
| GET | `/api/status` | Systemzustand (Last/Reserve/Ladepunkte) |

Vollständige, interaktive Dokumentation unter `/docs` (Swagger UI).
