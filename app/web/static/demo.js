"use strict";
/*
 * Demo-Modus (nur GitHub Pages): Es gibt kein Backend, daher wird window.fetch
 * so gepatcht, dass alle /api/...-Aufrufe im Browser mit simulierten, aber
 * realistischen Daten beantwortet werden. Der Zustand (Profile, Stationen,
 * Konfiguration) wird in localStorage gehalten, sodass Anlegen/Bearbeiten/
 * Löschen wie in der echten Anwendung funktioniert.
 *
 * Wird nur aktiv, wenn window.LM_DEMO === true (siehe config.js). Im echten
 * Betrieb (FastAPI) bleibt fetch unverändert.
 */
(function () {
  if (!window.LM_DEMO) return;

  const LS_KEY = "lm_demo_state_v3";
  const PHASES = ["L1", "L2", "L3"];

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
      seqStation: 3,
      seqBoard: 3,
      boards: [
        { id: 1, name: "Hauptverteilung", parent_board_id: null, incoming_fuse_a: 63, priority: 0, location: "Hausanschluss", notes: null },
        { id: 2, name: "UV Garage", parent_board_id: 1, incoming_fuse_a: 35, priority: 0, location: "Carport", notes: null },
      ],
      config: {
        id: 1, grid_limit_current_a: 32, management_mode: "static",
        distribution_strategy: "equal", poll_interval_s: 3,
        setpoint_min_change_a: 1, setpoint_min_hold_s: 30, fail_safe_after_s: 15,
        dynamic_meter_enabled: false, meter_profile_id: null, meter_ip_address: null,
        meter_tcp_port: 502, meter_unit_id: 1,
        en14a_enabled: false, en14a_active: false, en14a_limit_current_a: 6,
      },
      profiles: [{
        id: 1, name: "Beispiel-Wallbox 22kW", manufacturer: "Muster GmbH",
        notes: "Demo-Profil", default_unit_id: 1, byte_order: "big", word_order: "big",
        registers,
      }],
      stations: [
        { id: 1, name: "Garage links", location: "Carport", ip_address: "192.168.1.50", tcp_port: 502, unit_id: 1, profile_id: 1, phase_config: "3p", priority: 5, max_current_a: 32, min_current_a: 6, enabled: true, safe_state: "block", distribution_board_id: 2, circuit_breaker_a: 16 },
        { id: 2, name: "Garage rechts", location: "Carport", ip_address: "192.168.1.51", tcp_port: 502, unit_id: 1, profile_id: 1, phase_config: "3p", priority: 1, max_current_a: 32, min_current_a: 6, enabled: true, safe_state: "min_current", distribution_board_id: 2, circuit_breaker_a: 16 },
      ],
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
  const energyRuntime = {}; // station_id -> kWh (nur zur Laufzeit hochzählen)

  // --- Vereinfachte Verteil-Logik (spiegelt engine.py grob) ----------------
  function phasesOf(cfg) {
    return { "1p_l1": ["L1"], "1p_l2": ["L2"], "1p_l3": ["L3"], "3p": ["L1", "L2", "L3"] }[cfg];
  }

  function allocate() {
    const cfg = state.config;
    let limit = cfg.grid_limit_current_a;
    if (cfg.en14a_enabled && cfg.en14a_active) limit = Math.min(limit, cfg.en14a_limit_current_a);
    const active = state.stations.filter((s) => s.enabled);
    const setpoints = {};
    state.stations.forEach((s) => (setpoints[s.id] = 0));

    // Iterativ: pro Phase gleichmäßig teilen, Minimalstrom-Regel anwenden
    let pool = active.slice();
    for (let iter = 0; iter < pool.length + 1; iter++) {
      const remaining = { L1: limit, L2: limit, L3: limit };
      const countOnPhase = { L1: 0, L2: 0, L3: 0 };
      pool.forEach((s) => phasesOf(s.phase_config).forEach((p) => countOnPhase[p]++));
      const targets = {};
      pool.forEach((s) => {
        const ph = phasesOf(s.phase_config);
        let share = Math.min(...ph.map((p) => (countOnPhase[p] ? remaining[p] / countOnPhase[p] : limit)));
        targets[s.id] = Math.min(s.max_current_a, share);
      });
      // Station unter Minimalstrom pausieren und neu verteilen
      const below = pool.filter((s) => targets[s.id] < s.min_current_a - 1e-6);
      if (below.length === 0) {
        pool.forEach((s) => (setpoints[s.id] = Math.floor(targets[s.id] + 1e-6)));
        break;
      }
      below.sort((a, b) => targets[a.id] - targets[b.id]);
      pool = pool.filter((s) => s.id !== below[0].id);
      if (pool.length === 0) break;
    }
    return { setpoints, limit };
  }

  function buildStatusSnapshot() {
    const { setpoints, limit } = allocate();
    const load = { L1: 0, L2: 0, L3: 0 };
    const stations = state.stations.map((s) => {
      const sp = setpoints[s.id] || 0;
      const online = s.enabled; // im Demo sind aktivierte Stationen "online"
      const ph = phasesOf(s.phase_config);
      // Ist-Strom leicht schwankend um den Sollwert
      const ist = sp > 0 ? sp * (0.92 + Math.random() * 0.08) : 0;
      ph.forEach((p) => (load[p] += sp));
      energyRuntime[s.id] = (energyRuntime[s.id] || 30 + s.id * 5) + ist * ph.length * 0.0003;
      const values = online ? {
        current_l1: ph.includes("L1") ? +ist.toFixed(1) : 0,
        current_l2: ph.includes("L2") ? +ist.toFixed(1) : 0,
        current_l3: ph.includes("L3") ? +ist.toFixed(1) : 0,
        active_power: Math.round(ist * ph.length * 230),
        energy_total: +energyRuntime[s.id].toFixed(1),
        charge_status: sp > 0 ? 2 : 1,
        charge_status_text: sp > 0 ? "Lädt" : "Fahrzeug verbunden",
      } : {};
      return {
        station_id: s.id, name: s.name, online, values,
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
      active_stations: stations.filter((s) => s.online && s.setpoint_a > 0).length,
      total_stations: state.stations.length,
      last_cycle: new Date().toISOString(),
      stations,
    };
  }

  // --- Verteilungsbaum (vereinfacht: nutzt die flache allocate()-Simulation
  // oben, prüft im Demo-Modus KEINE Absicherung je Baumebene – nur die
  // echte Anwendung (app/loadmanager/engine.allocate_tree) tut das) --------
  function buildBoardTree() {
    const snap = buildStatusSnapshot();
    const liveByStation = {};
    snap.stations.forEach((s) => (liveByStation[s.station_id] = s));

    const childrenOf = {};
    state.boards.forEach((b) => {
      if (b.parent_board_id != null) {
        (childrenOf[b.parent_board_id] = childrenOf[b.parent_board_id] || []).push(b);
      }
    });
    const stationsOf = {};
    state.stations.forEach((s) => {
      const key = s.distribution_board_id ?? "root";
      (stationsOf[key] = stationsOf[key] || []).push(s);
    });
    const root = state.boards.find((b) => b.parent_board_id === null);

    function build(board) {
      const isRoot = board.parent_board_id === null;
      const myStations = (stationsOf[board.id] || []).concat(isRoot ? (stationsOf["root"] || []) : []);
      const load = { L1: 0, L2: 0, L3: 0 };
      const stationNodes = myStations.map((s) => {
        const live = liveByStation[s.id] || {};
        if (live.online) phasesOf(s.phase_config).forEach((p) => (load[p] += live.setpoint_a || 0));
        return {
          id: s.id, name: s.name, circuit_breaker_a: s.circuit_breaker_a ?? null,
          max_current_a: s.max_current_a, online: !!live.online, setpoint_a: live.setpoint_a ?? null,
        };
      });
      const childNodes = (childrenOf[board.id] || []).map((c) => {
        const cn = build(c);
        PHASES.forEach((p) => (load[p] += cn.load_a[p]));
        return cn;
      });
      return {
        id: board.id, name: board.name, incoming_fuse_a: board.incoming_fuse_a,
        priority: board.priority, location: board.location ?? null,
        load_a: load, stations: stationNodes, children: childNodes,
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
        if (state.stations.some((s) => s.distribution_board_id === id))
          return err(409, "Verteiler hat noch zugewiesene Ladestationen");
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

    // /api/stations ...
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
        save(state);
        return json(null, 204);
      }
    }
    if ((m = path.match(/^\/api\/stations\/(\d+)\/live$/)) && method === "GET") {
      const snap = buildStatusSnapshot().stations.find((s) => s.station_id === +m[1]);
      return snap ? json(snap) : err(404, "Ladestation nicht gefunden");
    }
    if ((m = path.match(/^\/api\/stations\/(\d+)\/test$/)) && method === "POST") {
      const snap = buildStatusSnapshot().stations.find((s) => s.station_id === +m[1]);
      if (!snap) return err(404, "Ladestation nicht gefunden");
      return json({ station_id: snap.station_id, name: snap.name, online: true,
        values: snap.values.charge_status != null ? snap.values : {
          current_l1: 0, charge_status: 1, charge_status_text: "Fahrzeug verbunden" },
        error: null });
    }

    return err(404, "Demo: unbekannter Endpunkt " + path);
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
    const s = { id: state.seqStation++, ...body };
    state.stations.push(s);
    save(state);
    return json(s, 201);
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
