"use strict";
/* Frontend-Logik: reines Fetch auf die REST-API, deutsche Zahlenformatierung. */

const nf = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 2 });
const PHASES = ["L1", "L2", "L3"];
const STRATEGY_TXT = { equal: "Gleichmäßig", priority: "Priorität", fifo: "FIFO" };
const TIER_TXT = { free: "Free", pro: "Pro", enterprise: "Enterprise" };

// --- Hilfsfunktionen -------------------------------------------------------
async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return null;
  return res.json();
}

function toast(msg, isErr) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = "show" + (isErr ? " err" : "");
  setTimeout(() => (el.className = ""), 3500);
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// --- Navigation ------------------------------------------------------------
document.querySelectorAll("nav button").forEach((b) =>
  b.addEventListener("click", () => showView(b.dataset.view)));

function showView(name) {
  document.querySelectorAll("nav button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) =>
    v.classList.toggle("active", v.id === name));
  if (name === "stations") loadStations();
  if (name === "boards") loadBoards();
  if (name === "profiles") loadProfiles();
  if (name === "settings") loadSettings();
  if (name === "license") loadLicense();
}

let profileCache = [];
let boardCache = [];
let stationCache = [];
let chargePointCache = [];

// --- Dashboard -------------------------------------------------------------
async function refreshDashboard() {
  let status;
  try { status = await api("/api/status"); }
  catch (e) { return; }

  const limit = status.effective_limit_current_a || status.grid_limit_current_a;
  const bars = PHASES.map((p) => {
    const load = status.phase_load_a[p] || 0;
    const pct = limit > 0 ? Math.min(100, (load / limit) * 100) : 0;
    const warn = pct >= 90;
    return `<div class="phase-bar">
      <div class="label"><span>${p}</span>
        <span>${nf.format(load)} A / ${nf.format(limit)} A</span></div>
      <div class="bar-track"><div class="bar-fill ${warn ? "warn" : ""}" style="width:${pct}%"></div></div>
    </div>`;
  }).join("");
  document.getElementById("phase-bars").innerHTML = bars;

  const modeTxt = { static: "Statisch", dynamic: "Dynamisch" }[status.management_mode] || status.management_mode;
  const stratTxt = STRATEGY_TXT[status.distribution_strategy] || status.distribution_strategy;
  document.getElementById("system-info").innerHTML = `
    <p><strong>Modus:</strong> ${modeTxt} &nbsp; <strong>Strategie:</strong> ${stratTxt}</p>
    <p><strong>Netzgrenze:</strong> ${nf.format(status.grid_limit_current_a)} A/Phase
       ${status.effective_limit_current_a < status.grid_limit_current_a
         ? `→ effektiv ${nf.format(status.effective_limit_current_a)} A` : ""}</p>
    <p><strong>Aktive Ladepunkte:</strong> ${status.active_charge_points} / ${status.total_charge_points}</p>
    ${status.en14a_active ? '<p><span class="badge err">§14a aktiv – Leistung reduziert</span></p>' : ""}`;

  const rows = status.charge_points.map((s) => {
    const v = s.values || {};
    // Wichtig: Ströme verschiedener Phasen dürfen NICHT addiert werden (keine
    // elektrotechnisch sinnvolle Größe, da L1/L2/L3 i. d. R. unsymmetrisch
    // belastet sind). Stattdessen jede vorhandene Phase einzeln ausweisen.
    const perPhase = [["L1", "current_l1"], ["L2", "current_l2"], ["L3", "current_l3"]]
      .map(([label, key]) => (typeof v[key] === "number" ? `${label}: ${nf.format(v[key])} A` : null))
      .filter(Boolean);
    let statusBadge;
    if (!s.online) statusBadge = '<span class="badge err">Offline</span>';
    else if (v.charge_status_text) statusBadge = `<span class="badge idle">${esc(v.charge_status_text)}</span>`;
    else statusBadge = '<span class="badge ok">Online</span>';
    return `<tr>
      <td>${esc(s.name)}</td>
      <td>${statusBadge}</td>
      <td>${perPhase.length ? perPhase.join(" · ") : "–"}</td>
      <td>${s.setpoint_a != null ? nf.format(s.setpoint_a) + " A" : "–"}</td>
      <td>${typeof v.active_power === "number" ? nf.format(v.active_power) + " W" : "–"}</td>
      <td>${typeof v.energy_total === "number" ? nf2.format(v.energy_total) + " kWh" : "–"}</td>
    </tr>`;
  }).join("");
  document.getElementById("live-tbody").innerHTML =
    rows || '<tr><td colspan="6" class="muted">Keine Stationen konfiguriert.</td></tr>';

  if (status.last_cycle) {
    const t = new Date(status.last_cycle);
    document.getElementById("cycle-info").textContent =
      "Letzter Zyklus: " + t.toLocaleTimeString("de-DE");
  }
}

// --- Stationen (physische Verbindung) + Ladepunkte ------------------------

const PHASE_TXT = { "1p_l1": "1p L1", "1p_l2": "1p L2", "1p_l3": "1p L3", "3p": "3-phasig" };

async function loadStations() {
  const [stations, profiles, boards, chargePoints] = await Promise.all([
    api("/api/stations"), api("/api/profiles"), api("/api/boards"), api("/api/charge-points"),
  ]);
  stationCache = stations;
  profileCache = profiles;
  boardCache = boards;
  chargePointCache = chargePoints;

  const pName = (id) => (profiles.find((p) => p.id === id) || {}).name || "?";
  const bName = (id) => (boards.find((b) => b.id === id) || {}).name || "Hauptverteilung";

  const cpRow = (cp) => `
    <tr>
      <td>${esc(cp.name)}${cp.connector_suffix ? ` <span class="muted">(${esc(cp.connector_suffix)})</span>` : ""}</td>
      <td>${bName(cp.distribution_board_id)}${cp.circuit_breaker_a != null ? `<div class="muted">Abgang: ${nf.format(cp.circuit_breaker_a)} A</div>` : ""}</td>
      <td>${PHASE_TXT[cp.phase_config]}</td>
      <td>${cp.priority}</td>
      <td>${nf.format(cp.min_current_a)} / ${nf.format(cp.max_current_a)}</td>
      <td>${cp.pv_surplus_only ? '<span class="badge pv">PV</span>' : "–"}</td>
      <td>${cp.schedules.length ? `<span class="badge idle">${cp.schedules.length} Zeitfenster</span>` : "–"}</td>
      <td>${cp.enabled ? '<span class="badge ok">ja</span>' : '<span class="badge idle">nein</span>'}</td>
      <td style="white-space:nowrap">
        <button class="btn small secondary" onclick='openChargePoint(${cp.id})'>Bearb.</button>
        <button class="btn small danger" onclick='deleteChargePoint(${cp.id})'>Löschen</button>
      </td>
    </tr>`;

  document.getElementById("stations-list").innerHTML = stations.map((s) => {
    const cps = chargePoints.filter((cp) => cp.station_id === s.id);
    return `
    <div class="card">
      <div class="station-card-header">
        <div>
          <span class="name">${esc(s.name)}</span>
          <span class="muted"> · ${esc(s.ip_address)}:${s.tcp_port} · Unit ${s.unit_id} · ${esc(pName(s.profile_id))}</span>
          ${s.location ? `<div class="muted">${esc(s.location)}</div>` : ""}
        </div>
        <div>
          <button class="btn small secondary" onclick='testStation(${s.id})'>Test</button>
          <button class="btn small secondary" onclick='openStation(${s.id})'>Bearb.</button>
          <button class="btn small danger" onclick='deleteStation(${s.id})'>Löschen</button>
          <button class="btn small" onclick='openChargePoint(0, ${s.id})'>+ Ladepunkt</button>
        </div>
      </div>
      <table>
        <thead>
          <tr><th>Ladepunkt</th><th>Verteiler</th><th>Phasen</th><th>Prio</th><th>Min/Max A</th><th>PV</th><th>Zeitplan</th><th>Aktiv</th><th></th></tr>
        </thead>
        <tbody>${cps.map(cpRow).join("") || '<tr><td colspan="9" class="muted">Noch kein Ladepunkt – „+ Ladepunkt" anlegen.</td></tr>'}</tbody>
      </table>
    </div>`;
  }).join("") || '<p class="muted">Noch keine Stationen.</p>';
}

async function openStation(id) {
  if (!profileCache.length) profileCache = await api("/api/profiles");
  if (!profileCache.length) { toast("Bitte zuerst ein Geräteprofil anlegen.", true); return; }
  const s = id ? await api("/api/stations/" + id) : {
    name: "", location: "", ip_address: "", tcp_port: 502, unit_id: 1,
    profile_id: profileCache[0].id,
  };
  const opts = profileCache.map((p) =>
    `<option value="${p.id}" ${p.id === s.profile_id ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  const dlg = document.getElementById("station-dialog");
  dlg.innerHTML = `
    <h2>${id ? "Station bearbeiten" : "Neue Station"}</h2>
    <div class="row">
      <div class="field"><label>Name</label><input id="st-name" value="${esc(s.name)}"></div>
      <div class="field"><label>Standort</label><input id="st-loc" value="${esc(s.location || "")}"></div>
    </div>
    <div class="row">
      <div class="field"><label>IP-Adresse</label><input id="st-ip" value="${esc(s.ip_address)}"></div>
      <div class="field"><label>Port</label><input id="st-port" type="number" value="${s.tcp_port}"></div>
      <div class="field"><label>Unit-ID</label><input id="st-unit" type="number" value="${s.unit_id}"></div>
    </div>
    <div class="row">
      <div class="field"><label>Geräteprofil</label><select id="st-profile">${opts}</select></div>
    </div>
    <p class="small-note">Eine Ladestation ist nur die Modbus-Verbindung. Priorität,
      Phasen, Verteiler, PV-Überschuss und Zeitpläne werden je Ladepunkt eingestellt
      (siehe „+ Ladepunkt" an der Station).</p>
    <div class="row" style="justify-content:flex-end;margin-top:8px">
      <button class="btn secondary" onclick="document.getElementById('station-dialog').close()">Abbrechen</button>
      <button class="btn" onclick="saveStation(${id || 0})">Speichern</button>
    </div>`;
  dlg.showModal();
}

async function saveStation(id) {
  const body = {
    name: val("st-name"), location: val("st-loc") || null,
    ip_address: val("st-ip"), tcp_port: +val("st-port"), unit_id: +val("st-unit"),
    profile_id: +val("st-profile"),
  };
  try {
    await api(id ? "/api/stations/" + id : "/api/stations",
      { method: id ? "PUT" : "POST", body: JSON.stringify(body) });
    document.getElementById("station-dialog").close();
    toast("Station gespeichert.");
    loadStations();
  } catch (e) { toast(e.message, true); }
}

async function deleteStation(id) {
  if (!confirm("Station (inkl. all ihrer Ladepunkte) wirklich löschen?")) return;
  try { await api("/api/stations/" + id, { method: "DELETE" }); toast("Gelöscht."); loadStations(); }
  catch (e) { toast(e.message, true); }
}

async function testStation(id) {
  toast("Verbindungstest läuft …");
  try {
    const r = await api("/api/stations/" + id + "/test", { method: "POST" });
    if (r.online) {
      const keys = Object.keys(r.values).filter((k) => !k.endsWith("_text"));
      toast("Verbindung OK. Gelesen: " + keys.join(", "));
    } else {
      toast("Test fehlgeschlagen: " + (r.error || "keine Antwort"), true);
    }
  } catch (e) { toast(e.message, true); }
}

// --- Ladepunkte (steuerbare Einheit, ggf. zwei je Station) -----------------

function scheduleRow(sch) {
  sch = sch || { weekdays_mask: 0b1111111, start_time: "22:00", end_time: "06:00" };
  const days = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
  const checks = days.map((d, i) => `
    <label><input type="checkbox" class="sch-day" data-bit="${i}" ${(sch.weekdays_mask & (1 << i)) ? "checked" : ""}> ${d}</label>
  `).join("");
  return `
    <div class="schedule-row">
      <div class="weekday-picker">${checks}</div>
      <input class="sch-start" type="time" value="${(sch.start_time || "22:00").slice(0, 5)}" style="width:130px">
      <span class="muted">bis</span>
      <input class="sch-end" type="time" value="${(sch.end_time || "06:00").slice(0, 5)}" style="width:130px">
      <button class="btn small danger" onclick="this.closest('.schedule-row').remove()">×</button>
    </div>`;
}

function collectSchedules() {
  return [...document.querySelectorAll("#sch-list .schedule-row")].map((row) => {
    let mask = 0;
    row.querySelectorAll(".sch-day").forEach((cb) => { if (cb.checked) mask |= (1 << +cb.dataset.bit); });
    const start = row.querySelector(".sch-start").value || "00:00";
    const end = row.querySelector(".sch-end").value || "00:00";
    return { weekdays_mask: mask, start_time: start + ":00", end_time: end + ":00" };
  }).filter((s) => s.weekdays_mask > 0);
}

async function openChargePoint(id, presetStationId) {
  if (!stationCache.length) stationCache = await api("/api/stations");
  if (!stationCache.length) { toast("Bitte zuerst eine Ladestation anlegen.", true); return; }
  if (!boardCache.length) boardCache = await api("/api/boards");
  const cp = id ? await api("/api/charge-points/" + id) : {
    station_id: presetStationId || stationCache[0].id, connector_suffix: "", name: "",
    phase_config: "3p", priority: 0, max_current_a: 32, min_current_a: 6,
    distribution_board_id: null, circuit_breaker_a: null, enabled: true,
    safe_state: "block", pv_surplus_only: false, schedules: [],
  };
  const stationOpts = stationCache.map((s) =>
    `<option value="${s.id}" ${s.id === cp.station_id ? "selected" : ""}>${esc(s.name)}</option>`).join("");
  const boardOpts = boardCache.map((b) =>
    `<option value="${b.id}" ${b.id === cp.distribution_board_id ? "selected" : ""}>${esc(b.name)}</option>`).join("");
  const dlg = document.getElementById("chargepoint-dialog");
  dlg.innerHTML = `
    <h2>${id ? "Ladepunkt bearbeiten" : "Neuer Ladepunkt"}</h2>
    <div class="row">
      <div class="field"><label>Station</label><select id="cp-station">${stationOpts}</select></div>
      <div class="field"><label>Name</label><input id="cp-name" value="${esc(cp.name)}"></div>
      <div class="field"><label>Connector-Suffix</label>
        <input id="cp-suffix" value="${esc(cp.connector_suffix)}" placeholder="z. B. _1 bei Doppel-Wallbox"></div>
    </div>
    <div class="row">
      <div class="field"><label>Phasenanschluss</label>
        <select id="cp-phase">
          ${["3p", "1p_l1", "1p_l2", "1p_l3"].map((p) =>
            `<option value="${p}" ${p === cp.phase_config ? "selected" : ""}>${p}</option>`).join("")}
        </select></div>
      <div class="field"><label>Priorität</label><input id="cp-prio" type="number" value="${cp.priority}"></div>
      <div class="field"><label>Aktiv</label>
        <select id="cp-enabled">
          <option value="true" ${cp.enabled ? "selected" : ""}>ja</option>
          <option value="false" ${!cp.enabled ? "selected" : ""}>nein</option>
        </select></div>
    </div>
    <div class="row">
      <div class="field"><label>Min-Strom (A)</label><input id="cp-min" type="number" step="0.1" value="${cp.min_current_a}"></div>
      <div class="field"><label>Max-Strom (A)</label><input id="cp-max" type="number" step="0.1" value="${cp.max_current_a}"></div>
      <div class="field"><label>Fail-Safe</label>
        <select id="cp-safe">
          <option value="block" ${cp.safe_state === "block" ? "selected" : ""}>Sperren (0 A)</option>
          <option value="min_current" ${cp.safe_state === "min_current" ? "selected" : ""}>Minimalstrom</option>
        </select></div>
    </div>
    <div class="row">
      <div class="field"><label>Verteiler (Abgang hängt an)</label><select id="cp-board">${boardOpts}</select></div>
      <div class="field"><label>Absicherung Abgang (A)</label>
        <input id="cp-breaker" type="number" step="0.1" value="${cp.circuit_breaker_a ?? ""}" placeholder="optional"></div>
      <div class="field"><label>PV-Überschussladen</label>
        <select id="cp-pv">
          <option value="false" ${!cp.pv_surplus_only ? "selected" : ""}>nein (normal laden)</option>
          <option value="true" ${cp.pv_surplus_only ? "selected" : ""}>ja (nur mit PV-Überschuss)</option>
        </select></div>
    </div>
    <p class="small-note">Connector-Suffix nur bei Doppel-Wallboxen ausfüllen (z. B.
      „_1"/„_2") – muss zu den Register-Keys im Geräteprofil passen (z. B.
      <code>set_current_1</code>). PV-Überschussladen lädt nachrangig nur mit
      überschüssiger Solarerzeugung; erfordert dynamisches Lastmanagement mit
      Netzanschlusszähler in den Einstellungen.</p>

    <label>Zeitpläne (wiederkehrende Sperrfenster)</label>
    <div id="sch-list">${(cp.schedules || []).map(scheduleRow).join("")}</div>
    <div class="row" style="margin-top:6px">
      <button class="btn small secondary" onclick="document.getElementById('sch-list').insertAdjacentHTML('beforeend', scheduleRow())">+ Zeitfenster</button>
    </div>
    <p class="small-note">Innerhalb eines Zeitfensters wird der Ladepunkt automatisch
      gesperrt (wie manuell deaktiviert). Fenster über Mitternacht (z. B. 22:00–06:00)
      sind möglich.</p>

    <div class="row" style="justify-content:flex-end;margin-top:8px">
      <button class="btn secondary" onclick="document.getElementById('chargepoint-dialog').close()">Abbrechen</button>
      <button class="btn" onclick="saveChargePoint(${id || 0})">Speichern</button>
    </div>`;
  dlg.showModal();
}

async function saveChargePoint(id) {
  const body = {
    station_id: +val("cp-station"), connector_suffix: val("cp-suffix").trim(),
    name: val("cp-name"), phase_config: val("cp-phase"), priority: +val("cp-prio"),
    min_current_a: +val("cp-min"), max_current_a: +val("cp-max"),
    safe_state: val("cp-safe"), enabled: val("cp-enabled") === "true",
    distribution_board_id: val("cp-board") ? +val("cp-board") : null,
    circuit_breaker_a: val("cp-breaker") ? +val("cp-breaker") : null,
    pv_surplus_only: val("cp-pv") === "true",
    schedules: collectSchedules(),
  };
  try {
    await api(id ? "/api/charge-points/" + id : "/api/charge-points",
      { method: id ? "PUT" : "POST", body: JSON.stringify(body) });
    document.getElementById("chargepoint-dialog").close();
    toast("Ladepunkt gespeichert.");
    loadStations();
  } catch (e) { toast(e.message, true); }
}

async function deleteChargePoint(id) {
  if (!confirm("Ladepunkt wirklich löschen?")) return;
  try { await api("/api/charge-points/" + id, { method: "DELETE" }); toast("Gelöscht."); loadStations(); }
  catch (e) { toast(e.message, true); }
}

// --- Verteilung (Hauptverteilung / Unterverteilung / Abgänge) -------------

function renderBoardNode(node) {
  const bars = PHASES.map((p) => {
    const load = node.load_a[p] || 0;
    const pct = node.incoming_fuse_a > 0 ? Math.min(100, (load / node.incoming_fuse_a) * 100) : 0;
    const warn = pct >= 90;
    return `<div class="mini-bar">
      <div class="label"><span>${p}</span><span>${nf.format(load)}/${nf.format(node.incoming_fuse_a)} A</span></div>
      <div class="bar-track"><div class="bar-fill ${warn ? "warn" : ""}" style="width:${pct}%"></div></div>
    </div>`;
  }).join("");

  const stationRows = node.stations.map((s) => `
    <div class="station-row">
      <span>${esc(s.name)}${s.pv_surplus_only ? ' <span class="badge pv">PV</span>' : ""}${s.circuit_breaker_a != null ? ` <span class="muted">(Abgang ${nf.format(s.circuit_breaker_a)} A)</span>` : ""}</span>
      <span>${s.online
        ? (s.setpoint_a != null ? nf.format(s.setpoint_a) + " A" : '<span class="badge ok">online</span>')
        : '<span class="badge err">offline</span>'}</span>
    </div>`).join("");

  const childrenHtml = node.children.map(renderBoardNode).join("");

  return `
    <div class="tree-node">
      <div class="tree-header">
        <div>
          <span class="tree-title">${esc(node.name)}</span>
          <span class="tree-fuse">Absicherung ${nf.format(node.incoming_fuse_a)} A${node.location ? " · " + esc(node.location) : ""}${node.strategy ? ` · Strategie: ${STRATEGY_TXT[node.strategy] || node.strategy}` : ""}</span>
        </div>
        <div>
          <button class="btn small secondary" onclick="openBoard(0, ${node.id})">+ Unterverteilung</button>
          <button class="btn small secondary" onclick="openBoard(${node.id})">Bearb.</button>
          <button class="btn small danger" onclick="deleteBoard(${node.id})">Löschen</button>
        </div>
      </div>
      <div class="tree-phase-bars">${bars}</div>
      ${stationRows ? `<div class="tree-station-list">${stationRows}</div>` : ""}
      ${childrenHtml ? `<div class="tree-children">${childrenHtml}</div>` : ""}
    </div>`;
}

async function loadBoards() {
  const tree = await api("/api/boards/tree");
  document.getElementById("board-tree").innerHTML = renderBoardNode(tree);
  boardCache = await api("/api/boards");
}

async function openBoard(id, presetParentId) {
  if (!boardCache.length) boardCache = await api("/api/boards");
  const rootId = boardCache.find((x) => x.parent_board_id === null)?.id ?? null;
  const b = id ? await api("/api/boards/" + id) : {
    name: "", parent_board_id: presetParentId ?? rootId,
    incoming_fuse_a: 35, priority: 0, strategy: null, location: "", notes: "",
  };
  const isRoot = id && b.parent_board_id === null;
  const parentOpts = boardCache
    .filter((x) => x.id !== id)
    .map((x) => `<option value="${x.id}" ${x.id === b.parent_board_id ? "selected" : ""}>${esc(x.name)}</option>`)
    .join("");
  const dlg = document.getElementById("board-dialog");
  dlg.innerHTML = `
    <h2>${id ? "Verteiler bearbeiten" : "Neue Unterverteilung"}</h2>
    <div class="row">
      <div class="field"><label>Name</label><input id="b-name" value="${esc(b.name)}"></div>
      ${isRoot ? "" : `<div class="field"><label>Übergeordneter Verteiler</label><select id="b-parent">${parentOpts}</select></div>`}
    </div>
    <div class="row">
      <div class="field"><label>Absicherung (A, je Phase)</label><input id="b-fuse" type="number" step="0.1" value="${b.incoming_fuse_a}"></div>
      <div class="field"><label>Priorität</label><input id="b-prio" type="number" value="${b.priority}"></div>
      <div class="field"><label>Verteilstrategie</label>
        <select id="b-strategy">
          <option value="" ${!b.strategy ? "selected" : ""}>Erben (übergeordnet/global)</option>
          <option value="equal" ${b.strategy === "equal" ? "selected" : ""}>Gleichmäßig</option>
          <option value="priority" ${b.strategy === "priority" ? "selected" : ""}>Priorität</option>
          <option value="fifo" ${b.strategy === "fifo" ? "selected" : ""}>FIFO</option>
        </select></div>
      <div class="field"><label>Standort</label><input id="b-loc" value="${esc(b.location || "")}"></div>
    </div>
    <div class="row" style="justify-content:flex-end;margin-top:8px">
      <button class="btn secondary" onclick="document.getElementById('board-dialog').close()">Abbrechen</button>
      <button class="btn" onclick="saveBoard(${id || 0}, ${isRoot ? "true" : "false"})">Speichern</button>
    </div>`;
  dlg.showModal();
}

async function saveBoard(id, isRoot) {
  const body = {
    name: val("b-name"),
    parent_board_id: isRoot ? null : +val("b-parent"),
    incoming_fuse_a: +val("b-fuse"), priority: +val("b-prio"),
    strategy: val("b-strategy") || null,
    location: val("b-loc") || null,
  };
  try {
    await api(id ? "/api/boards/" + id : "/api/boards",
      { method: id ? "PUT" : "POST", body: JSON.stringify(body) });
    document.getElementById("board-dialog").close();
    toast("Verteiler gespeichert.");
    loadBoards();
  } catch (e) { toast(e.message, true); }
}

async function deleteBoard(id) {
  if (!confirm("Verteiler wirklich löschen?")) return;
  try { await api("/api/boards/" + id, { method: "DELETE" }); toast("Gelöscht."); loadBoards(); }
  catch (e) { toast(e.message, true); }
}

// --- Profile ---------------------------------------------------------------
async function loadProfiles() {
  const profiles = await api("/api/profiles");
  profileCache = profiles;
  document.getElementById("profiles-tbody").innerHTML = profiles.map((p) => `
    <tr>
      <td>${esc(p.name)}</td>
      <td>${esc(p.manufacturer || "–")}</td>
      <td>${p.default_unit_id}</td>
      <td>${p.byte_order}/${p.word_order}</td>
      <td>${p.registers.length}</td>
      <td style="white-space:nowrap">
        <button class="btn small secondary" onclick='exportProfile(${p.id})'>Export</button>
        <button class="btn small secondary" onclick='openProfile(${p.id})'>Bearb.</button>
        <button class="btn small danger" onclick='deleteProfile(${p.id})'>Löschen</button>
      </td>
    </tr>`).join("") || '<tr><td colspan="6" class="muted">Noch keine Profile.</td></tr>';
}

const DTYPES = ["uint16", "int16", "uint32", "int32", "float32", "float64", "bool"];

function regRow(r) {
  r = r || { key: "", role: "read", function_code: 4, register_address: 0, data_type: "uint16", scale: 1, offset: 0, unit: "" };
  const sel = (opts, cur, cls) =>
    `<select class="${cls}">${opts.map((o) =>
      `<option ${String(o) === String(cur) ? "selected" : ""}>${o}</option>`).join("")}</select>`;
  return `<tr>
    <td><input class="r-key" value="${esc(r.key)}" style="width:120px"></td>
    <td>${sel(["read", "write"], r.role, "r-role")}</td>
    <td>${sel([3, 4, 6, 16], r.function_code, "r-fc")}</td>
    <td><input class="r-addr" type="number" value="${r.register_address}" style="width:80px"></td>
    <td>${sel(DTYPES, r.data_type, "r-dt")}</td>
    <td>${sel(["", "big", "little"], r.byte_order || "", "r-bo")}</td>
    <td>${sel(["", "big", "little"], r.word_order || "", "r-wo")}</td>
    <td><input class="r-scale" type="number" step="any" value="${r.scale ?? 1}" style="width:70px"></td>
    <td><input class="r-unit" value="${esc(r.unit || "")}" style="width:55px"></td>
    <td><input class="r-min" type="number" step="any" value="${r.writable_min ?? ""}" style="width:60px"></td>
    <td><input class="r-max" type="number" step="any" value="${r.writable_max ?? ""}" style="width:60px"></td>
    <td><button class="btn small danger" onclick="this.closest('tr').remove()">×</button></td>
  </tr>`;
}

async function openProfile(id) {
  const p = id ? await api("/api/profiles/" + id) : {
    name: "", manufacturer: "", notes: "", default_unit_id: 1,
    byte_order: "big", word_order: "big", registers: [],
  };
  const dlg = document.getElementById("profile-dialog");
  dlg.innerHTML = `
    <h2>${id ? "Profil bearbeiten" : "Neues Geräteprofil"}</h2>
    <div class="row">
      <div class="field"><label>Name</label><input id="p-name" value="${esc(p.name)}"></div>
      <div class="field"><label>Hersteller</label><input id="p-mfr" value="${esc(p.manufacturer || "")}"></div>
      <div class="field"><label>Unit-ID</label><input id="p-unit" type="number" value="${p.default_unit_id}"></div>
      <div class="field"><label>Byte-Order</label>
        <select id="p-bo">${["big", "little"].map((o) => `<option ${o === p.byte_order ? "selected" : ""}>${o}</option>`).join("")}</select></div>
      <div class="field"><label>Word-Order</label>
        <select id="p-wo">${["big", "little"].map((o) => `<option ${o === p.word_order ? "selected" : ""}>${o}</option>`).join("")}</select></div>
    </div>
    <label>Register-Mappings</label>
    <div style="overflow-x:auto">
    <table class="reg-table">
      <thead><tr><th>Key</th><th>Rolle</th><th>FC</th><th>Adresse</th><th>Typ</th><th>Byte</th><th>Word</th><th>Scale</th><th>Einheit</th><th>Min</th><th>Max</th><th></th></tr></thead>
      <tbody id="reg-tbody">${(p.registers || []).map(regRow).join("")}</tbody>
    </table>
    </div>
    <div class="row" style="margin-top:8px">
      <button class="btn small secondary" onclick="document.getElementById('reg-tbody').insertAdjacentHTML('beforeend', regRow())">+ Register</button>
    </div>
    <p class="small-note">Physikalischer Wert = Rohwert × Scale (+ Offset). Byte/Word leer = Profil-Default. Min/Max nur für Schreibregister.</p>
    <div class="row" style="justify-content:flex-end;margin-top:8px">
      <button class="btn secondary" onclick="document.getElementById('profile-dialog').close()">Abbrechen</button>
      <button class="btn" onclick="saveProfile(${id || 0})">Speichern</button>
    </div>`;
  dlg.showModal();
}

function collectRegisters() {
  return [...document.querySelectorAll("#reg-tbody tr")].map((tr) => {
    const g = (cls) => tr.querySelector("." + cls);
    const num = (cls) => { const v = g(cls).value; return v === "" ? null : +v; };
    const reg = {
      key: g("r-key").value.trim(),
      role: g("r-role").value,
      function_code: +g("r-fc").value,
      register_address: +g("r-addr").value,
      data_type: g("r-dt").value,
      scale: +g("r-scale").value || 1,
      unit: g("r-unit").value.trim() || null,
    };
    if (g("r-bo").value) reg.byte_order = g("r-bo").value;
    if (g("r-wo").value) reg.word_order = g("r-wo").value;
    const mn = num("r-min"), mx = num("r-max");
    if (mn != null) reg.writable_min = mn;
    if (mx != null) reg.writable_max = mx;
    return reg;
  }).filter((r) => r.key);
}

async function saveProfile(id) {
  const body = {
    name: val("p-name"), manufacturer: val("p-mfr") || null,
    default_unit_id: +val("p-unit"), byte_order: val("p-bo"), word_order: val("p-wo"),
    registers: collectRegisters(),
  };
  try {
    await api(id ? "/api/profiles/" + id : "/api/profiles",
      { method: id ? "PUT" : "POST", body: JSON.stringify(body) });
    document.getElementById("profile-dialog").close();
    toast("Profil gespeichert.");
    loadProfiles();
  } catch (e) { toast(e.message, true); }
}

async function deleteProfile(id) {
  if (!confirm("Profil wirklich löschen?")) return;
  try { await api("/api/profiles/" + id, { method: "DELETE" }); toast("Gelöscht."); loadProfiles(); }
  catch (e) { toast(e.message, true); }
}

async function exportProfile(id) {
  const data = await api("/api/profiles/" + id + "/export");
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = (data.name || "profil").replace(/\s+/g, "_") + ".json";
  a.click();
}

function importProfile() {
  const inp = document.getElementById("import-file");
  inp.onchange = async () => {
    const file = inp.files[0];
    if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      await api("/api/profiles/import", { method: "POST", body: JSON.stringify(data) });
      toast("Profil importiert.");
      loadProfiles();
    } catch (e) { toast("Import fehlgeschlagen: " + e.message, true); }
    inp.value = "";
  };
  inp.click();
}

// --- Einstellungen ---------------------------------------------------------
async function loadSettings() {
  const c = await api("/api/config");
  if (!profileCache.length) profileCache = await api("/api/profiles");
  const meterOpts = ['<option value="">– keines –</option>'].concat(
    profileCache.map((p) => `<option value="${p.id}" ${p.id === c.meter_profile_id ? "selected" : ""}>${esc(p.name)}</option>`)
  ).join("");
  document.getElementById("settings-form").innerHTML = `
    <div class="row">
      <div class="field"><label>Netzgrenze pro Phase (A)</label><input id="c-limit" type="number" step="0.1" value="${c.grid_limit_current_a}"></div>
      <div class="field"><label>Modus</label>
        <select id="c-mode">
          <option value="static" ${c.management_mode === "static" ? "selected" : ""}>Statisch</option>
          <option value="dynamic" ${c.management_mode === "dynamic" ? "selected" : ""}>Dynamisch</option>
        </select></div>
      <div class="field"><label>Strategie</label>
        <select id="c-strat">
          <option value="equal" ${c.distribution_strategy === "equal" ? "selected" : ""}>Gleichmäßig</option>
          <option value="priority" ${c.distribution_strategy === "priority" ? "selected" : ""}>Priorität</option>
          <option value="fifo" ${c.distribution_strategy === "fifo" ? "selected" : ""}>FIFO</option>
        </select></div>
      <div class="field"><label>Polling-Intervall (s)</label><input id="c-poll" type="number" step="0.5" value="${c.poll_interval_s}"></div>
    </div>
    <h2 style="margin-top:16px">Hysterese &amp; Fail-Safe</h2>
    <div class="row">
      <div class="field"><label>Min. Sollwertänderung (A)</label><input id="c-change" type="number" step="0.1" value="${c.setpoint_min_change_a}"></div>
      <div class="field"><label>Mindesthaltezeit (s)</label><input id="c-hold" type="number" step="1" value="${c.setpoint_min_hold_s}"></div>
      <div class="field"><label>Fail-Safe nach (s)</label><input id="c-fs" type="number" step="1" value="${c.fail_safe_after_s}"></div>
    </div>
    <h2 style="margin-top:16px">Dynamisches Lastmanagement (Netzanschlusszähler)</h2>
    <div class="row">
      <div class="field"><label>Zähler aktiv</label>
        <select id="c-dyn"><option value="true" ${c.dynamic_meter_enabled ? "selected" : ""}>ja</option>
        <option value="false" ${!c.dynamic_meter_enabled ? "selected" : ""}>nein</option></select></div>
      <div class="field"><label>Zähler-Profil</label><select id="c-meter-profile">${meterOpts}</select></div>
      <div class="field"><label>Zähler-IP</label><input id="c-meter-ip" value="${esc(c.meter_ip_address || "")}"></div>
      <div class="field"><label>Port</label><input id="c-meter-port" type="number" value="${c.meter_tcp_port}"></div>
      <div class="field"><label>Unit-ID</label><input id="c-meter-unit" type="number" value="${c.meter_unit_id}"></div>
    </div>
    <h2 style="margin-top:16px">§14a EnWG</h2>
    <div class="row">
      <div class="field"><label>§14a aktiviert</label>
        <select id="c-14a-en"><option value="true" ${c.en14a_enabled ? "selected" : ""}>ja</option>
        <option value="false" ${!c.en14a_enabled ? "selected" : ""}>nein</option></select></div>
      <div class="field"><label>Steuersignal aktiv</label>
        <select id="c-14a-act"><option value="true" ${c.en14a_active ? "selected" : ""}>ja</option>
        <option value="false" ${!c.en14a_active ? "selected" : ""}>nein</option></select></div>
      <div class="field"><label>Reduzierte Grenze (A/Phase)</label><input id="c-14a-lim" type="number" step="0.1" value="${c.en14a_limit_current_a}"></div>
    </div>
    <h2 style="margin-top:16px">Cloud-Anbindung (optional)</h2>
    <p class="small-note" style="margin-bottom:10px">
      Sendet periodisch den Status an eine selbst gehostete Voltibus-Cloud-Instanz
      zur Fernansicht. Ausfälle der Verbindung haben keinerlei Einfluss auf die
      lokale Regelung.
    </p>
    <div class="row">
      <div class="field"><label>Aktiviert</label>
        <select id="c-cloud-en"><option value="true" ${c.cloud_relay_enabled ? "selected" : ""}>ja</option>
        <option value="false" ${!c.cloud_relay_enabled ? "selected" : ""}>nein</option></select></div>
      <div class="field" style="flex:2 1 260px"><label>Cloud-URL</label><input id="c-cloud-url" placeholder="https://cloud.example.org" value="${esc(c.cloud_relay_url || "")}"></div>
      <div class="field" style="flex:2 1 260px"><label>Installations-Token</label><input id="c-cloud-token" placeholder="vltb_..." value="${esc(c.cloud_relay_token || "")}"></div>
      <div class="field"><label>Intervall (s)</label><input id="c-cloud-interval" type="number" step="1" value="${c.cloud_relay_interval_s}"></div>
    </div>
    <div class="row">
      <button class="btn secondary" type="button" onclick="testCloudRelay()">Verbindung jetzt testen</button>
      <span id="cloud-test-result" class="muted"></span>
    </div>
    <div class="row" style="justify-content:flex-end;margin-top:12px">
      <button class="btn" onclick="saveSettings()">Speichern</button>
    </div>`;
}

async function saveSettings() {
  const body = {
    grid_limit_current_a: +val("c-limit"), management_mode: val("c-mode"),
    distribution_strategy: val("c-strat"), poll_interval_s: +val("c-poll"),
    setpoint_min_change_a: +val("c-change"), setpoint_min_hold_s: +val("c-hold"),
    fail_safe_after_s: +val("c-fs"),
    dynamic_meter_enabled: val("c-dyn") === "true",
    meter_profile_id: val("c-meter-profile") ? +val("c-meter-profile") : null,
    meter_ip_address: val("c-meter-ip") || null,
    meter_tcp_port: +val("c-meter-port"), meter_unit_id: +val("c-meter-unit"),
    en14a_enabled: val("c-14a-en") === "true", en14a_active: val("c-14a-act") === "true",
    en14a_limit_current_a: +val("c-14a-lim"),
    cloud_relay_enabled: val("c-cloud-en") === "true",
    cloud_relay_url: val("c-cloud-url") || null,
    cloud_relay_token: val("c-cloud-token") || null,
    cloud_relay_interval_s: +val("c-cloud-interval"),
  };
  try { await api("/api/config", { method: "PUT", body: JSON.stringify(body) }); toast("Einstellungen gespeichert."); }
  catch (e) { toast(e.message, true); }
}

async function testCloudRelay() {
  const el = document.getElementById("cloud-test-result");
  el.textContent = "Sende Test …";
  try {
    // Aktuelle Felder zuerst speichern, damit der Test die eingegebenen
    // (nicht die zuletzt gespeicherten) Zugangsdaten verwendet.
    await saveSettings();
    const r = await api("/api/config/cloud-relay/test", { method: "POST" });
    el.textContent = r.ok ? "✓ Verbindung erfolgreich." : "✗ " + r.error;
    el.style.color = r.ok ? "var(--ok)" : "var(--err)";
  } catch (e) {
    el.textContent = "✗ " + e.message;
    el.style.color = "var(--err)";
  }
}

// --- Lizenz ------------------------------------------------------------

async function loadLicense() {
  const lic = await api("/api/license");
  const unlimited = lic.max_stations === null;
  const pct = unlimited ? 0 : Math.min(100, (lic.used_stations / Math.max(1, lic.max_stations)) * 100);
  const warn = !unlimited && lic.used_stations >= lic.max_stations;
  document.getElementById("license-card").innerHTML = `
    <div class="row" style="align-items:center">
      <span class="badge ${lic.tier === "free" ? "idle" : "ok"}" style="font-size:14px">${TIER_TXT[lic.tier] || lic.tier}</span>
      ${lic.issued_to ? `<span class="muted">Lizenznehmer: ${esc(lic.issued_to)}</span>` : ""}
      ${lic.activated_at ? `<span class="muted">Aktiviert am ${new Date(lic.activated_at).toLocaleDateString("de-DE")}</span>` : ""}
    </div>
    <div style="margin-top:14px">
      <div class="row" style="justify-content:space-between;margin-bottom:4px">
        <span>Ladestationen</span>
        <span>${lic.used_stations} / ${unlimited ? "unbegrenzt" : lic.max_stations}</span>
      </div>
      ${unlimited ? "" : `<div class="bar-track"><div class="bar-fill ${warn ? "warn" : ""}" style="width:${pct}%"></div></div>`}
    </div>
    <p class="small-note" style="margin-top:16px">
      Jede Lizenzstufe hat den vollen Funktionsumfang – nur die Anzahl der
      Ladestationen ist begrenzt: Free bis zu 2, Pro bis zu 10, Enterprise
      unbegrenzt. Ein Schlüssel gilt sofort nach Aktivierung.
    </p>
    <div class="row" style="margin-top:16px">
      <div class="field" style="flex:3 1 320px">
        <label>Lizenzschlüssel</label>
        <input id="lic-key" placeholder="VLTB1....." />
      </div>
      <div class="field" style="flex:0 0 auto;align-self:flex-end">
        <button class="btn" onclick="activateLicense()">Aktivieren</button>
      </div>
    </div>`;
}

async function activateLicense() {
  const key = val("lic-key").trim();
  if (!key) return;
  try {
    await api("/api/license/activate", { method: "POST", body: JSON.stringify({ key }) });
    toast("Lizenz aktiviert.");
    loadLicense();
  } catch (e) { toast(e.message, true); }
}

// --- Utils -----------------------------------------------------------------
function val(id) { return document.getElementById(id).value; }

// Start
refreshDashboard();
setInterval(refreshDashboard, 3000);
