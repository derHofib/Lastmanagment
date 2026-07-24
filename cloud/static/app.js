"use strict";
/* Voltibus Cloud – Frontend-Logik: reines Fetch auf die REST-API, kein Framework
   (gleiches Prinzip wie app/web/static/app.js). */

const nf = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 });

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
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

function val(id) { return document.getElementById(id).value; }

function showView(name) {
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === name));
  document.getElementById("app-header").style.display = name === "dashboard" ? "flex" : "none";
}

// --- Auth --------------------------------------------------------------

function showAuthTab(tab) {
  document.querySelectorAll(".auth-tabs button").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === tab));
  document.getElementById("auth-login").style.display = tab === "login" ? "block" : "none";
  document.getElementById("auth-register").style.display = tab === "register" ? "block" : "none";
}

async function login() {
  try {
    const user = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: val("login-email"), password: val("login-password") }),
    });
    onAuthenticated(user);
  } catch (e) { toast(e.message, true); }
}

async function register() {
  try {
    const user = await api("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ email: val("register-email"), password: val("register-password") }),
    });
    onAuthenticated(user);
  } catch (e) { toast(e.message, true); }
}

async function logout() {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (_) {}
  showView("auth");
}

function onAuthenticated(user) {
  document.getElementById("user-email").textContent = user.email;
  showView("dashboard");
  loadInstallations();
}

async function init() {
  try {
    const user = await api("/api/auth/me");
    onAuthenticated(user);
  } catch (_) {
    showView("auth");
  }
}

// --- Installationen ------------------------------------------------------

function fmtDate(iso) {
  if (!iso) return "–";
  return new Date(iso + (iso.endsWith("Z") ? "" : "Z")).toLocaleString("de-DE");
}

function installationCard(inst) {
  const badge = inst.online
    ? '<span class="badge ok">Online</span>'
    : '<span class="badge idle">Offline</span>';
  const snap = inst.last_snapshot;
  let phaseInfo = '<p class="small-note">Noch keine Daten empfangen.</p>';
  if (snap && snap.phase_load_a) {
    const phases = ["L1", "L2", "L3"].map((p) =>
      `<span>${p}: <b>${nf.format(snap.phase_load_a[p] || 0)} A</b></span>`).join("");
    const cps = (snap.active_charge_points != null && snap.total_charge_points != null)
      ? `<span>Ladepunkte: <b>${snap.active_charge_points} / ${snap.total_charge_points}</b> aktiv</span>` : "";
    phaseInfo = `<div class="phase-mini">${phases}${cps}</div>`;
  }
  return `
    <div class="installation-card">
      <div class="installation-head">
        <span class="name">${esc(inst.name)}</span>
        ${badge}
      </div>
      <div class="installation-meta">
        Angelegt am ${fmtDate(inst.created_at)} · Zuletzt gesehen: ${inst.last_seen_at ? fmtDate(inst.last_seen_at) : "noch nie"}
      </div>
      ${phaseInfo}
      <div class="installation-actions">
        <button class="btn secondary small" onclick="rotateToken(${inst.id})">Token neu erzeugen</button>
        <button class="btn danger small" onclick="deleteInstallation(${inst.id})">Löschen</button>
      </div>
    </div>`;
}

async function loadInstallations() {
  try {
    const list = await api("/api/installations");
    const el = document.getElementById("installations-list");
    el.innerHTML = list.length
      ? list.map(installationCard).join("")
      : '<p class="small-note">Noch keine Installation angelegt.</p>';
  } catch (e) { toast(e.message, true); }
}

function openCreateInstallation() {
  const dlg = document.getElementById("installation-dialog");
  dlg.innerHTML = `
    <h2 style="margin-top:0">Neue Installation</h2>
    <div class="field"><label>Bezeichnung</label><input id="inst-name" placeholder="z. B. Zuhause" /></div>
    <div class="row" style="justify-content:flex-end">
      <button class="btn secondary" onclick="document.getElementById('installation-dialog').close()">Abbrechen</button>
      <button class="btn" onclick="submitCreateInstallation()">Anlegen</button>
    </div>`;
  dlg.showModal();
}

async function submitCreateInstallation() {
  const name = val("inst-name").trim();
  if (!name) return;
  try {
    const inst = await api("/api/installations", { method: "POST", body: JSON.stringify({ name }) });
    showTokenDialog(inst);
    loadInstallations();
  } catch (e) { toast(e.message, true); }
}

async function rotateToken(id) {
  if (!confirm("Neues Token erzeugen? Das alte Token funktioniert danach nicht mehr.")) return;
  try {
    const inst = await api(`/api/installations/${id}/rotate-token`, { method: "POST" });
    showTokenDialog(inst);
  } catch (e) { toast(e.message, true); }
}

async function deleteInstallation(id) {
  if (!confirm("Installation wirklich löschen?")) return;
  try {
    await api(`/api/installations/${id}`, { method: "DELETE" });
    toast("Gelöscht.");
    loadInstallations();
  } catch (e) { toast(e.message, true); }
}

function showTokenDialog(inst) {
  const dlg = document.getElementById("installation-dialog");
  dlg.innerHTML = `
    <h2 style="margin-top:0">Token für „${esc(inst.name)}"</h2>
    <p class="small-note">
      Dieses Token wird nur jetzt angezeigt. Trage es in der lokalen
      Voltibus-Weboberfläche unter „Einstellungen → Cloud-Anbindung" ein.
    </p>
    <div class="token-box">${esc(inst.token)}</div>
    <div class="row" style="justify-content:flex-end">
      <button class="btn" onclick="document.getElementById('installation-dialog').close()">Verstanden</button>
    </div>`;
  dlg.showModal();
}

// Start
init();
