"use strict";
/* Frontend-Logik: reines Fetch auf die REST-API, deutsche Zahlenformatierung. */

const nf = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 2 });
const PHASES = ["L1", "L2", "L3"];

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
}

let profileCache = [];
let boardCache = [];

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
  const stratTxt = { equal: "Gleichmäßig", priority: "Priorität", fifo: "FIFO" }[status.distribution_strategy];
  document.getElementById("system-info").innerHTML = `
    <p><strong>Modus:</strong> ${modeTxt} &nbsp; <strong>Strategie:</strong> ${stratTxt}</p>
    <p><strong>Netzgrenze:</strong> ${nf.format(status.grid_limit_current_a)} A/Phase
       ${status.effective_limit_current_a < status.grid_limit_current_a
         ? `→ effektiv ${nf.format(status.effective_limit_current_a)} A` : ""}</p>
    <p><strong>Aktive Ladepunkte:</strong> ${status.active_stations} / ${status.total_stations}</p>
    ${status.en14a_active ? '<p><span class="badge err">§14a aktiv – Leistung reduziert</span></p>' : ""}`;

  const rows = status.stations.map((s) => {
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

// --- Stationen -------------------------------------------------------------
async function loadStations() {
  const [stations, profiles, boards] = await Promise.all([
    api("/api/stations"), api("/api/profiles"), api("/api/boards"),
  ]);
  profileCache = profiles;
  boardCache = boards;
  const pName = (id) => (profiles.find((p) => p.id === id) || {}).name || "?";
  const bName = (id) => (boards.find((b) => b.id === id) || {}).name || "Hauptverteilung";
  const phaseTxt = { "1p_l1": "1p L1", "1p_l2": "1p L2", "1p_l3": "1p L3", "3p": "3-phasig" };
  document.getElementById("stations-tbody").innerHTML = stations.map((s) => `
    <tr>
      <td>${esc(s.name)}${s.location ? `<div class="muted">${esc(s.location)}</div>` : ""}</td>
      <td>${esc(s.ip_address)}:${s.tcp_port}</td>
      <td>${s.unit_id}</td>
      <td>${esc(pName(s.profile_id))}</td>
      <td>${esc(bName(s.distribution_board_id))}${s.circuit_breaker_a != null ? `<div class="muted">Abgang: ${nf.format(s.circuit_breaker_a)} A</div>` : ""}</td>
      <td>${phaseTxt[s.phase_config]}</td>
      <td>${s.priority}</td>
      <td>${nf.format(s.min_current_a)} / ${nf.format(s.max_current_a)}</td>
      <td>${s.enabled ? '<span class="badge ok">ja</span>' : '<span class="badge idle">nein</span>'}</td>
      <td style="white-space:nowrap">
        <button class="btn small secondary" onclick='testStation(${s.id})'>Test</button>
        <button class="btn small secondary" onclick='openStation(${s.id})'>Bearb.</button>
        <button class="btn small danger" onclick='deleteStation(${s.id})'>Löschen</button>
      </td>
    </tr>`).join("") || '<tr><td colspan="10" class="muted">Noch keine Stationen.</td></tr>';
}

async function openStation(id) {
  if (!profileCache.length) profileCache = await api("/api/profiles");
  if (!profileCache.length) { toast("Bitte zuerst ein Geräteprofil anlegen.", true); return; }
  if (!boardCache.length) boardCache = await api("/api/boards");
  const s = id ? await api("/api/stations/" + id) : {
    name: "", location: "", ip_address: "", tcp_port: 502, unit_id: 1,
    profile_id: profileCache[0].id, phase_config: "3p", priority: 0,
    max_current_a: 32, min_current_a: 6, enabled: true, safe_state: "block",
    distribution_board_id: null, circuit_breaker_a: null,
  };
  const opts = profileCache.map((p) =>
    `<option value="${p.id}" ${p.id === s.profile_id ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  const boardOpts = boardCache.map((b) =>
    `<option value="${b.id}" ${b.id === s.distribution_board_id ? "selected" : ""}>${esc(b.name)}</option>`).join("");
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
      <div class="field"><label>Phasenanschluss</label>
        <select id="st-phase">
          ${["3p", "1p_l1", "1p_l2", "1p_l3"].map((p) =>
            `<option value="${p}" ${p === s.phase_config ? "selected" : ""}>${p}</option>`).join("")}
        </select></div>
      <div class="field"><label>Priorität</label><input id="st-prio" type="number" value="${s.priority}"></div>
    </div>
    <div class="row">
      <div class="field"><label>Verteiler (Abgang hängt an)</label><select id="st-board">${boardOpts}</select></div>
      <div class="field"><label>Absicherung Abgang (A)</label>
        <input id="st-breaker" type="number" step="0.1" value="${s.circuit_breaker_a ?? ""}" placeholder="optional"></div>
    </div>
    <div class="row">
      <div class="field"><label>Min-Strom (A)</label><input id="st-min" type="number" step="0.1" value="${s.min_current_a}"></div>
      <div class="field"><label>Max-Strom (A)</label><input id="st-max" type="number" step="0.1" value="${s.max_current_a}"></div>
      <div class="field"><label>Fail-Safe</label>
        <select id="st-safe">
          <option value="block" ${s.safe_state === "block" ? "selected" : ""}>Sperren (0 A)</option>
          <option value="min_current" ${s.safe_state === "min_current" ? "selected" : ""}>Minimalstrom</option>
        </select></div>
      <div class="field"><label>Aktiv</label>
        <select id="st-enabled">
          <option value="true" ${s.enabled ? "selected" : ""}>ja</option>
          <option value="false" ${!s.enabled ? "selected" : ""}>nein</option>
        </select></div>
    </div>
    <p class="small-note">Die Absicherung des Abgangs ist die Installationssicherung
      des Kabels zur Station – getrennt vom technischen Max-Strom der Wallbox selbst.
      Leer lassen, wenn keine gesonderte Abgangssicherung bekannt ist.</p>
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
    profile_id: +val("st-profile"), phase_config: val("st-phase"),
    priority: +val("st-prio"), min_current_a: +val("st-min"), max_current_a: +val("st-max"),
    safe_state: val("st-safe"), enabled: val("st-enabled") === "true",
    distribution_board_id: val("st-board") ? +val("st-board") : null,
    circuit_breaker_a: val("st-breaker") ? +val("st-breaker") : null,
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
  if (!confirm("Station wirklich löschen?")) return;
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
      <span>${esc(s.name)}${s.circuit_breaker_a != null ? ` <span class="muted">(Abgang ${nf.format(s.circuit_breaker_a)} A)</span>` : ""}</span>
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
          <span class="tree-fuse">Absicherung ${nf.format(node.incoming_fuse_a)} A${node.location ? " · " + esc(node.location) : ""}</span>
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
    incoming_fuse_a: 35, priority: 0, location: "", notes: "",
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
  };
  try { await api("/api/config", { method: "PUT", body: JSON.stringify(body) }); toast("Einstellungen gespeichert."); }
  catch (e) { toast(e.message, true); }
}

// --- Utils -----------------------------------------------------------------
function val(id) { return document.getElementById(id).value; }

// Start
refreshDashboard();
setInterval(refreshDashboard, 3000);
