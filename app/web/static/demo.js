"use strict";
/*
 * Demo-Modus (nur GitHub Pages): Es gibt kein Backend, daher wird window.fetch
 * so gepatcht, dass alle /api/...-Aufrufe im Browser mit simulierten, aber
 * realistischen Daten beantwortet werden. Der Zustand (Profile, Stationen,
 * Ladepunkte, Verteiler, Konfiguration) wird in localStorage gehalten, sodass
 * Anlegen/Bearbeiten/Löschen wie in der echten Anwendung funktioniert.
 *
 * Vereinfacht gegenüber der echten Engine (app/loadmanager/engine.py):
 * keine echte hierarchische Absicherungs-Durchsetzung je Verteilerknoten,
 * kein echter Zwei-Lauf-PV-Algorithmus – nur plausible, aber nicht
 * physikalisch exakte Werte für die UI-Demo.
 *
 * Wird nur aktiv, wenn window.LM_DEMO === true (siehe config.js). Im echten
 * Betrieb (FastAPI) bleibt fetch unverändert.
 */
(function () {
  if (!window.LM_DEMO) return;

  const LS_KEY = "lm_demo_state_v5";
  const PHASES = ["L1", "L2", "L3"];
  // Spiegelt app.licensing.TIER_LIMITS (None = unbegrenzt -> null)
  const TIER_LIMITS = { free: 2, pro: 10, enterprise: null };

  // --- Seed-Daten (entspricht examples/beispiel_wallbox.json) --------------
  function seedState() {
    const registers = [
      { key: "current_l1", role: "read", function_code: 4, register_address: 100, data_type: "float32", scale: 1, offset: 0, unit: "A" },
      { key: "current_l2", role: "read", function_code: 4, register_address: 102, data_type: "float32", scale: 1, offset: 0, unit: "A" },
      { key: "current_l3", role: "read", function_code: 4, register_address: 104, data_type: "float32", scale: 1, offset: 0, unit: "A" },
      { key: "active_power", role: "read", function_code: 4, register_address: 120, data_type: "uint32", scale: 1, offset: 0, unit: "W" },
      { key: "energy_total", role: "read", function_code: 4, register_address: 130, data_type: "uint32", scale: 0.1, offset: 0, unit: "kWh" },
      { key: "charge_status", role: "read", function_code: 3, register_address: 200, data_type: "uint16", scale: 1, offset: 0, enum_map: { "0": "Verfügbar", "1": "Fahrzeug verbunden", "2": "Lädt", "3": "Fehler" } },
      { key: "set_current", role: "write", function_code: 6, register_address: 300, data_type: "uint16", scale: 1, offset: 0, unit: "A", writable_min: 6, writable_max: 32 },
      { key: "enable", role: "write", function_code: 6, register_address: 301, data_type: "uint16", scale: 1, offset: 0, writable_min: 0, writable_max: 1 },
    ].map((r, i) => ({ id: i + 1, ...r }));

    return {
      seqProfile: 2,
      seqStation: 2,
      seqChargePoint: 3,
      seqSchedule: 1,
      seqBoard: 3,
      boards: [
        { id: 1, name: "Hauptverteilung", parent_board_id: null, incoming_fuse_a: 63, priority: 0, strategy: null, location: "Hausanschluss", notes: null },
        { id: 2, name: "UV Garage", parent_board_id: 1, incoming_fuse_a: 35, priority: 0, strategy: null, location: "Carport", notes: null },
      ],
      config: {
        id: 1, grid_limit_current_a: 32, management_mode: "static",
        distribution_strategy: "equal", poll_interval_s: 3,
        setpoint_min_change_a: 1, setpoint_min_hold_s: 30, fail_safe_after_s: 15,
        dynamic_meter_enabled: false, meter_profile_id: null, meter_ip_address: null,
        meter_tcp_port: 502, meter_unit_id: 1,
        en14a_enabled: false, en14a_active: false, en14a_limit_current_a: 6,
        cloud_relay_enabled: false, cloud_relay_url: null, cloud_relay_token: null,
        cloud_relay_interval_s: 30,
        mqtt_enabled: false, mqtt_host: null, mqtt_port: 1883, mqtt_username: null,
        mqtt_password: null, mqtt_topic_prefix: "voltibus", mqtt_interval_s: 10,
        mqtt_ha_discovery: true,
      },
      profiles: [{
        id: 1, name: "Beispiel-Wallbox 22kW", manufacturer: "Muster GmbH",
        notes: "Demo-Profil", default_unit_id: 1, byte_order: "big", word_order: "big",
        registers,
      }],
      stations: [
        { id: 1, name: "Garage", location: "Carport", ip_address: "192.168.1.50", tcp_port: 502, unit_id: 1, profile_id: 1 },
      ],
      chargePoints: [
        { id: 1, station_id: 1, connector_suffix: "", name: "Ladepunkt links", phase_config: "3p", priority: 5, max_current_a: 32, min_current_a: 6, distribution_board_id: 2, circuit_breaker_a: 16, enabled: true, safe_state: "block", pv_surplus_only: false, schedules: [] },
        { id: 2, station_id: 1, connector_suffix: "_2", name: "Ladepunkt rechts", phase_config: "3p", priority: 1, max_current_a: 32, min_current_a: 6, distribution_board_id: 2, circuit_breaker_a: 16, enabled: true, safe_state: "min_current", pv_surplus_only: false, schedules: [] },
      ],
      license: { tier: "free", raw_key: null, max_stations: null, issued_to: null, activated_at: null },
    };
  }

  function load() {
    try {
      const raw = localStorage.getItem(LS_KEY);
      if (raw) return JSON.parse(raw);
    } catch (_) {}
    const s = seedState();
    save(s);
    return s;
  }
  function save(s) {
    try { localStorage.setItem(LS_KEY, JSON.stringify(s)); } catch (_) {}
  }

  let state = load();
  const energyRuntime = {}; // charge_point_id -> kWh (nur zur Laufzeit hochzählen)

  // --- Zeitplan-Prüfung (spiegelt app.loadmanager.schedule.is_blocked) -----
  function isBlocked(schedules, now) {
    const weekday = (now.getDay() + 6) % 7; // JS: So=0..Sa=6 -> Mo=0..So=6
    const prevWeekday = (weekday + 6) % 7;
    const current = now.getHours() * 60 + now.getMinutes();
    const toMinutes = (t) => { const [h, m] = t.split(":").map(Number); return h * 60 + m; };
    for (const s of schedules || []) {
      const start = toMinutes(s.start_time), end = toMinutes(s.end_time);
      if (start <= end) {
        if ((s.weekdays_mask & (1 << weekday)) && current >= start && current < end) return true;
      } else {
        if ((s.weekdays_mask & (1 << weekday)) && current >= start) return true;
        if ((s.weekdays_mask & (1 << prevWeekday)) && current < end) return true;
      }
    }
    return false;
  }

  // --- Vereinfachte Verteil-Logik (spiegelt engine.py grob, ohne echte
  // Hierarchie-Durchsetzung je Verteilerknoten) -----------------------------
  function phasesOf(cfg) {
    return { "1p_l1": ["L1"], "1p_l2": ["L2"], "1p_l3": ["L3"], "3p": ["L1", "L2", "L3"] }[cfg];
  }

  function waterFill(items, limit) {
    // items: [{id, phases, min_current_a, max_current_a}]
    const targets = {};
    items.forEach((s) => (targets[s.id] = 0));
    let pool = items.slice();
    for (let iter = 0; iter < pool.length + 1; iter++) {
      const remaining = { L1: limit, L2: limit, L3: limit };
      const countOnPhase = { L1: 0, L2: 0, L3: 0 };
      pool.forEach((s) => phasesOf(s.phase_config).forEach((p) => countOnPhase[p]++));
      const round = {};
      pool.forEach((s) => {
        const ph = phasesOf(s.phase_config);
        let share = Math.min(...ph.map((p) => (countOnPhase[p] ? remaining[p] / countOnPhase[p] : limit)));
        round[s.id] = Math.min(s.max_current_a, share);
      });
      const below = pool.filter((s) => round[s.id] < s.min_current_a - 1e-6);
      if (below.length === 0) {
        pool.forEach((s) => (targets[s.id] = Math.floor(round[s.id] + 1e-6)));
        break;
      }
      below.sort((a, b) => round[a.id] - round[b.id]);
      pool = pool.filter((s) => s.id !== below[0].id);
      if (pool.length === 0) break;
    }
    return targets;
  }

  const SIMULATED_SURPLUS_A = 12; // Demo-Fixwert für PV-Überschuss je Phase

  function allocate() {
    const cfg = state.config;
    let limit = cfg.grid_limit_current_a;
    if (cfg.en14a_enabled && cfg.en14a_active) limit = Math.min(limit, cfg.en14a_limit_current_a);
    const now = new Date();

    const eligible = state.chargePoints.filter((cp) => cp.enabled && !isBlocked(cp.schedules, now));
    const gridCps = eligible.filter((cp) => !cp.pv_surplus_only);
    const pvCps = eligible.filter((cp) => cp.pv_surplus_only);

    const setpoints = {};
    state.chargePoints.forEach((cp) => (setpoints[cp.id] = 0));
    Object.assign(setpoints, waterFill(gridCps, limit));

    // Meter-Überschuss: im Demo-Modus unabhängig davon simuliert, ob gerade
    // ein PV-Ladepunkt angeschlossen ist (spiegelt die echte Anwendung, wo
    // der Zähler den Überschuss unabhängig von der Nachfrage meldet).
    const dynamicMeterActive = cfg.management_mode === "dynamic" && cfg.dynamic_meter_enabled;
    const surplus = dynamicMeterActive
      ? { L1: SIMULATED_SURPLUS_A, L2: SIMULATED_SURPLUS_A, L3: SIMULATED_SURPLUS_A }
      : { L1: 0, L2: 0, L3: 0 };

    // PV-Only: nur eine plausible Demo-Zuteilung, wenn dynamisches Lastmanagement
    // aktiv ist (simulierter Überschuss) – sonst 0 A (konservativ, wie in der
    // echten Anwendung ohne Überschussdaten).
    if (dynamicMeterActive && pvCps.length) {
      Object.assign(setpoints, waterFill(pvCps, SIMULATED_SURPLUS_A));
    } else {
      pvCps.forEach((cp) => (setpoints[cp.id] = 0));
    }

    return {
      setpoints, limit, surplus,
      blockedIds: new Set(state.chargePoints.filter((cp) => cp.enabled && isBlocked(cp.schedules, now)).map((cp) => cp.id)),
    };
  }

  function buildStatusSnapshot() {
    const { setpoints, limit, surplus, blockedIds } = allocate();
    const load = { L1: 0, L2: 0, L3: 0 };
    const chargePoints = state.chargePoints.map((cp) => {
      const sp = setpoints[cp.id] || 0;
      const online = cp.enabled; // im Demo sind aktivierte Ladepunkte "online"
      const ph = phasesOf(cp.phase_config);
      const ist = sp > 0 ? sp * (0.92 + Math.random() * 0.08) : 0;
      ph.forEach((p) => (load[p] += sp));
      energyRuntime[cp.id] = (energyRuntime[cp.id] || 30 + cp.id * 5) + ist * ph.length * 0.0003;
      const blocked = blockedIds.has(cp.id);
      const values = online ? {
        current_l1: ph.includes("L1") ? +ist.toFixed(1) : 0,
        current_l2: ph.includes("L2") ? +ist.toFixed(1) : 0,
        current_l3: ph.includes("L3") ? +ist.toFixed(1) : 0,
        active_power: Math.round(ist * ph.length * 230),
        energy_total: +energyRuntime[cp.id].toFixed(1),
        charge_status: sp > 0 ? 2 : 1,
        charge_status_text: blocked ? "Zeitplan-Sperre" : (sp > 0 ? "Lädt" : "Fahrzeug verbunden"),
      } : {};
      return {
        charge_point_id: cp.id, name: cp.name, online, values,
        setpoint_a: online ? sp : null, error: online ? null : "deaktiviert",
      };
    });
    const cfg = state.config;
    return {
      management_mode: cfg.management_mode,
      distribution_strategy: cfg.distribution_strategy,
      grid_limit_current_a: cfg.grid_limit_current_a,
      effective_limit_current_a: limit,
      en14a_active: !!(cfg.en14a_enabled && cfg.en14a_active),
      phase_load_a: load,
      phase_available_a: { L1: limit, L2: limit, L3: limit },
      phase_surplus_a: surplus,
      active_charge_points: chargePoints.filter((cp) => cp.online && cp.setpoint_a > 0).length,
      total_charge_points: state.chargePoints.length,
      last_cycle: new Date().toISOString(),
      charge_points: chargePoints,
    };
  }

  // --- Verteilungsbaum (vereinfacht: nutzt die flache allocate()-Simulation
  // oben, prüft im Demo-Modus KEINE Absicherung je Baumebene – nur die
  // echte Anwendung (app/loadmanager/engine.allocate_tree) tut das) --------
  function buildBoardTree() {
    const snap = buildStatusSnapshot();
    const liveByCp = {};
    snap.charge_points.forEach((cp) => (liveByCp[cp.charge_point_id] = cp));

    const childrenOf = {};
    state.boards.forEach((b) => {
      if (b.parent_board_id != null) {
        (childrenOf[b.parent_board_id] = childrenOf[b.parent_board_id] || []).push(b);
      }
    });
    const cpsOf = {};
    state.chargePoints.forEach((cp) => {
      const key = cp.distribution_board_id ?? "root";
      (cpsOf[key] = cpsOf[key] || []).push(cp);
    });
    const root = state.boards.find((b) => b.parent_board_id === null);

    function build(board) {
      const isRoot = board.parent_board_id === null;
      const myCps = (cpsOf[board.id] || []).concat(isRoot ? (cpsOf["root"] || []) : []);
      const load = { L1: 0, L2: 0, L3: 0 };
      const cpNodes = myCps.map((cp) => {
        const live = liveByCp[cp.id] || {};
        if (live.online) phasesOf(cp.phase_config).forEach((p) => (load[p] += live.setpoint_a || 0));
        return {
          id: cp.id, name: cp.name, circuit_breaker_a: cp.circuit_breaker_a ?? null,
          max_current_a: cp.max_current_a, online: !!live.online, setpoint_a: live.setpoint_a ?? null,
          pv_surplus_only: cp.pv_surplus_only,
        };
      });
      const childNodes = (childrenOf[board.id] || []).map((c) => {
        const cn = build(c);
        PHASES.forEach((p) => (load[p] += cn.load_a[p]));
        return cn;
      });
      return {
        id: board.id, name: board.name, incoming_fuse_a: board.incoming_fuse_a,
        priority: board.priority, strategy: board.strategy ?? null, location: board.location ?? null,
        load_a: load, stations: cpNodes, children: childNodes,
      };
    }
    return build(root);
  }

  // --- Hilfen für CRUD -----------------------------------------------------
  const json = (data, status = 200) =>
    new Response(status === 204 ? null : JSON.stringify(data),
      { status, headers: { "Content-Type": "application/json" } });
  const err = (status, detail) => json({ detail }, status);

  function withRegisterIds(regs) {
    return (regs || []).map((r, i) => ({
      id: i + 1, scale: 1, offset: 0, byte_order: null, word_order: null,
      unit: null, enum_map: null, writable_min: null, writable_max: null, ...r,
    }));
  }
  function withScheduleIds(schedules) {
    return (schedules || []).map((s) => ({ id: state.seqSchedule++, ...s }));
  }
  function exportOf(p) {
    const out = {
      name: p.name, manufacturer: p.manufacturer, notes: p.notes,
      default_unit_id: p.default_unit_id, byte_order: p.byte_order,
      word_order: p.word_order,
      registers: p.registers.map((r) => {
        const o = { key: r.key, role: r.role, function_code: r.function_code, register_address: r.register_address, data_type: r.data_type, scale: r.scale, offset: r.offset };
        if (r.byte_order) o.byte_order = r.byte_order;
        if (r.word_order) o.word_order = r.word_order;
        if (r.unit) o.unit = r.unit;
        if (r.enum_map) o.enum_map = r.enum_map;
        if (r.writable_min != null) o.writable_min = r.writable_min;
        if (r.writable_max != null) o.writable_max = r.writable_max;
        return o;
      }),
    };
    return out;
  }

  // --- Router --------------------------------------------------------------
  function handle(method, path, body) {
    // /healthz
    if (path === "/healthz") return json({ status: "ok", version: "demo" });

    // /api/status
    if (path === "/api/status" && method === "GET") return json(buildStatusSnapshot());

    // /api/config
    if (path === "/api/config") {
      if (method === "GET") return json(state.config);
      if (method === "PUT") {
        Object.entries(body || {}).forEach(([k, v]) => {
          if (v !== undefined) state.config[k] = v;
        });
        save(state);
        return json(state.config);
      }
    }
    if (path === "/api/config/cloud-relay/test" && method === "POST") {
      // Demo-Modus: kein echtes Backend, daher nur eine plausible Antwort
      // ohne tatsächlichen Netzwerkzugriff.
      if (!state.config.cloud_relay_url || !state.config.cloud_relay_token)
        return json({ ok: false, error: "Cloud-URL oder Token fehlt" });
      return json({ ok: false, error: "Demo-Modus: keine echte Cloud-Verbindung möglich" });
    }
    if (path === "/api/config/mqtt/test" && method === "POST") {
      if (!state.config.mqtt_host)
        return json({ ok: false, error: "MQTT-Host fehlt" });
      return json({ ok: false, error: "Demo-Modus: keine echte MQTT-Verbindung möglich" });
    }

    // /api/boards ...
    let m;
    if (path === "/api/boards" && method === "GET") return json(state.boards);
    if (path === "/api/boards" && method === "POST") return createBoard(body);
    if (path === "/api/boards/tree" && method === "GET") return json(buildBoardTree());
    if ((m = path.match(/^\/api\/boards\/(\d+)$/))) {
      const id = +m[1];
      const idx = state.boards.findIndex((b) => b.id === id);
      if (idx < 0) return err(404, "Verteiler nicht gefunden");
      if (method === "GET") return json(state.boards[idx]);
      if (method === "PUT") {
        const existing = state.boards[idx];
        if ((existing.parent_board_id === null) !== (body.parent_board_id === null))
          return err(400, "Der Wurzel-Status eines Verteilers kann nicht geändert werden");
        if (body.parent_board_id != null && !state.boards.some((b) => b.id === body.parent_board_id))
          return err(400, `Übergeordneter Verteiler ${body.parent_board_id} existiert nicht`);
        state.boards[idx] = { id, ...body };
        save(state);
        return json(state.boards[idx]);
      }
      if (method === "DELETE") {
        if (state.boards[idx].parent_board_id === null)
          return err(409, "Die Hauptverteilung kann nicht gelöscht werden");
        if (state.boards.some((b) => b.parent_board_id === id))
          return err(409, "Verteiler hat noch Unterverteilungen");
        if (state.chargePoints.some((cp) => cp.distribution_board_id === id))
          return err(409, "Verteiler hat noch zugewiesene Ladepunkte");
        state.boards.splice(idx, 1);
        save(state);
        return json(null, 204);
      }
    }

    // /api/profiles ...
    if (path === "/api/profiles" && method === "GET") return json(state.profiles);
    if (path === "/api/profiles" && method === "POST") return createProfile(body);
    if (path === "/api/profiles/import" && method === "POST") return createProfile(body);
    if ((m = path.match(/^\/api\/profiles\/(\d+)$/))) {
      const id = +m[1];
      const idx = state.profiles.findIndex((p) => p.id === id);
      if (idx < 0) return err(404, "Geräteprofil nicht gefunden");
      if (method === "GET") return json(state.profiles[idx]);
      if (method === "PUT") {
        const p = state.profiles[idx];
        Object.assign(p, {
          name: body.name, manufacturer: body.manufacturer ?? null,
          notes: body.notes ?? null, default_unit_id: body.default_unit_id,
          byte_order: body.byte_order, word_order: body.word_order,
        });
        if (body.registers) p.registers = withRegisterIds(body.registers);
        save(state);
        return json(p);
      }
      if (method === "DELETE") {
        if (state.stations.some((s) => s.profile_id === id))
          return err(409, "Profil wird von Station(en) verwendet");
        state.profiles.splice(idx, 1);
        save(state);
        return json(null, 204);
      }
    }
    if ((m = path.match(/^\/api\/profiles\/(\d+)\/export$/)) && method === "GET") {
      const p = state.profiles.find((x) => x.id === +m[1]);
      return p ? json(exportOf(p)) : err(404, "Geräteprofil nicht gefunden");
    }

    // /api/stations (nur Verbindung: IP/Port/Unit/Profil) ---------------------
    if (path === "/api/stations" && method === "GET") return json(state.stations);
    if (path === "/api/stations" && method === "POST") return createStation(body);
    if ((m = path.match(/^\/api\/stations\/(\d+)$/))) {
      const id = +m[1];
      const idx = state.stations.findIndex((s) => s.id === id);
      if (idx < 0) return err(404, "Ladestation nicht gefunden");
      if (method === "GET") return json(state.stations[idx]);
      if (method === "PUT") {
        if (!state.profiles.some((p) => p.id === body.profile_id))
          return err(400, "Geräteprofil existiert nicht");
        state.stations[idx] = { id, ...body };
        save(state);
        return json(state.stations[idx]);
      }
      if (method === "DELETE") {
        state.stations.splice(idx, 1);
        state.chargePoints = state.chargePoints.filter((cp) => cp.station_id !== id);
        save(state);
        return json(null, 204);
      }
    }
    if ((m = path.match(/^\/api\/stations\/(\d+)\/test$/)) && method === "POST") {
      const station = state.stations.find((s) => s.id === +m[1]);
      if (!station) return err(404, "Ladestation nicht gefunden");
      const cps = state.chargePoints.filter((cp) => cp.station_id === station.id);
      const values = {};
      cps.forEach((cp) => {
        values["current_l1" + cp.connector_suffix] = 0;
        values["charge_status" + cp.connector_suffix] = 1;
        values["charge_status" + cp.connector_suffix + "_text"] = "Fahrzeug verbunden";
      });
      return json({ station_id: station.id, name: station.name, online: true, values, error: null });
    }

    // /api/charge-points --------------------------------------------------
    if (path === "/api/charge-points" && method === "GET") return json(state.chargePoints);
    if (path === "/api/charge-points" && method === "POST") return createChargePoint(body);
    if ((m = path.match(/^\/api\/charge-points\/(\d+)$/))) {
      const id = +m[1];
      const idx = state.chargePoints.findIndex((cp) => cp.id === id);
      if (idx < 0) return err(404, "Ladepunkt nicht gefunden");
      if (method === "GET") return json(state.chargePoints[idx]);
      if (method === "PUT") {
        if (!state.stations.some((s) => s.id === body.station_id))
          return err(400, "Ladestation existiert nicht");
        const schedules = body.schedules ? withScheduleIds(body.schedules) : state.chargePoints[idx].schedules;
        state.chargePoints[idx] = { id, ...body, schedules };
        save(state);
        return json(state.chargePoints[idx]);
      }
      if (method === "DELETE") {
        state.chargePoints.splice(idx, 1);
        save(state);
        return json(null, 204);
      }
    }
    if ((m = path.match(/^\/api\/charge-points\/(\d+)\/live$/)) && method === "GET") {
      const snap = buildStatusSnapshot().charge_points.find((cp) => cp.charge_point_id === +m[1]);
      return snap ? json(snap) : err(404, "Ladepunkt nicht gefunden");
    }

    // /api/license -------------------------------------------------------
    if (path === "/api/license" && method === "GET") return json(licenseRead());
    if (path === "/api/license/activate" && method === "POST") return activateLicense(body);

    return err(404, "Demo: unbekannter Endpunkt " + path);
  }

  // Lizenz: effektives Stationslimit (explizite Override vor Tier-Standard,
  // spiegelt app.licensing.effective_max_stations).
  function effectiveMaxStations() {
    if (state.license.max_stations != null) return state.license.max_stations;
    return TIER_LIMITS[state.license.tier];
  }
  function licenseRead() {
    return {
      tier: state.license.tier,
      max_stations: effectiveMaxStations(),
      used_stations: state.stations.length,
      issued_to: state.license.issued_to,
      activated_at: state.license.activated_at,
    };
  }
  // Demo-Vereinfachung: dekodiert nur den Payload, prüft KEINE Ed25519-
  // Signatur (die Demo läuft komplett im Browser ohne echte Sicherheitsgrenze
  // – die echte Prüfung sitzt in app/licensing.py::verify_license_key).
  function decodeLicenseKey(key) {
    const parts = String(key || "").trim().split(".");
    if (parts.length !== 3 || parts[0] !== "VLTB1") return null;
    try {
      let b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
      while (b64.length % 4) b64 += "=";
      return JSON.parse(decodeURIComponent(escape(atob(b64))));
    } catch (_) {
      return null;
    }
  }
  function activateLicense(body) {
    const payload = decodeLicenseKey(body && body.key);
    if (!payload || !Object.prototype.hasOwnProperty.call(TIER_LIMITS, payload.tier))
      return err(400, "Ungültiger Lizenzschlüssel: unbekanntes Schlüsselformat");
    state.license = {
      tier: payload.tier, raw_key: body.key,
      max_stations: payload.max_stations ?? null,
      issued_to: payload.issued_to ?? null,
      activated_at: new Date().toISOString(),
    };
    save(state);
    return json(licenseRead());
  }

  function createProfile(body) {
    const p = {
      id: state.seqProfile++, name: body.name, manufacturer: body.manufacturer ?? null,
      notes: body.notes ?? null, default_unit_id: body.default_unit_id ?? 1,
      byte_order: body.byte_order ?? "big", word_order: body.word_order ?? "big",
      registers: withRegisterIds(body.registers),
    };
    if (state.profiles.some((x) => x.name === p.name))
      return err(409, `Profilname '${p.name}' existiert bereits`);
    state.profiles.push(p);
    save(state);
    return json(p, 201);
  }
  function createBoard(body) {
    if (body.parent_board_id == null)
      return err(400, "Neue Verteiler benötigen einen übergeordneten Verteiler");
    if (!state.boards.some((b) => b.id === body.parent_board_id))
      return err(400, `Übergeordneter Verteiler ${body.parent_board_id} existiert nicht`);
    const b = { id: state.seqBoard++, ...body };
    state.boards.push(b);
    save(state);
    return json(b, 201);
  }
  function createStation(body) {
    if (!state.profiles.some((p) => p.id === body.profile_id))
      return err(400, "Geräteprofil existiert nicht");
    const limit = effectiveMaxStations();
    if (limit != null && state.stations.length >= limit) {
      return err(402, "Lizenzgrenze erreicht: Die " + state.license.tier +
        "-Lizenz erlaubt maximal " + limit + " Ladestation(en). Bitte Lizenz im Reiter 'Lizenz' upgraden.");
    }
    const s = { id: state.seqStation++, ...body };
    state.stations.push(s);
    save(state);
    return json(s, 201);
  }
  function createChargePoint(body) {
    if (!state.stations.some((s) => s.id === body.station_id))
      return err(400, "Ladestation existiert nicht");
    const cp = { id: state.seqChargePoint++, ...body, schedules: withScheduleIds(body.schedules) };
    state.chargePoints.push(cp);
    save(state);
    return json(cp, 201);
  }

  // --- fetch patchen -------------------------------------------------------
  window.fetch = async function (url, opts) {
    opts = opts || {};
    let path;
    try { path = new URL(url, location.href).pathname; }
    catch (_) { path = String(url); }
    if (!/\/(api|healthz)/.test(path)) {
      // Nicht-API-Ressourcen normal laden (sollte hier nicht vorkommen)
      return json({ detail: "not found" }, 404);
    }
    // Pfad auf den Teil ab /api bzw. /healthz normalisieren
    const idx = path.indexOf("/api");
    if (idx > 0) path = path.slice(idx);
    else if (path.indexOf("/healthz") > 0) path = "/healthz";

    let body = null;
    if (opts.body) { try { body = JSON.parse(opts.body); } catch (_) {} }
    const method = (opts.method || "GET").toUpperCase();
    try {
      return handle(method, path, body);
    } catch (e) {
      return err(500, "Demo-Fehler: " + e.message);
    }
  };

  // --- Sichtbarer Demo-Hinweis --------------------------------------------
  window.addEventListener("DOMContentLoaded", function () {
    const bar = document.createElement("div");
    bar.textContent =
      "🧪 Demo-Modus – simulierte Daten im Browser (kein Backend, keine echte Modbus-Kommunikation). Änderungen bleiben nur lokal.";
    bar.style.cssText =
      "background:#f59e0b;color:#04263a;padding:8px 16px;text-align:center;font-size:13px;font-weight:600";
    document.body.insertBefore(bar, document.body.firstChild);
  });
})();
