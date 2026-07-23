/* WebSocket client with auto-reconnect + renderers for both panels. */
"use strict";

const $ = (id) => document.getElementById(id);

/* ---------- formatting helpers ---------- */

function fmtAgo(epochSecs) {
  if (!epochSecs) return "—";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - epochSecs));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function hopLabel(h) {
  // MeshCore cached path length: 0 = heard directly, N = via N repeaters,
  // <0 or null = flood (no cached path yet).
  if (h == null || h < 0) return "🛰 flood (no direct path)";
  if (h === 0) return "🛰 0 hops (direct)";
  return `🛰 ${h} hop${h === 1 ? "" : "s"}`;
}

function fmtUptime(secs) {
  if (secs == null) return "—";
  const d = Math.floor(secs / 86400), h = Math.floor((secs % 86400) / 3600),
        m = Math.floor((secs % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}

const fmt = (v, suffix = "") => (v == null ? "—" : `${v}${suffix}`);

function setPill(el, ok, label) {
  el.className = "pill " + (ok === true ? "pill-good" : ok === false ? "pill-bad" : "pill-unknown");
  el.textContent = "● " + label;
}

/* ---------- renderers ---------- */

function renderSources(sources) {
  const tcp = sources.meshtastic_tcp;
  const mqtt = sources.mqtt;
  setPill($("mesh-tcp-status"),
    tcp ? tcp.connected : null,
    "node TCP: " + (tcp ? (tcp.connected ? "connected" : tcp.detail || "down") : "—"));
  setPill($("mqtt-status"),
    mqtt ? mqtt.connected : null,
    "MQTT: " + (mqtt ? (mqtt.connected ? "connected" : mqtt.detail || "down") : "—"));

  const mPaused = !!(tcp && (tcp.detail || "").toLowerCase().includes("paused"));
  const mt = $("mesh-toggle");
  mt.textContent = mPaused ? "Reconnect dashboard" : "Release to phone";
  mt.dataset.action = mPaused ? "resume" : "pause";
}

function setPlaceholder(id, val) {
  if (val != null) $(id).placeholder = String(val);
}

function renderMyNode(my) {
  $("tile-battery").textContent = fmt(my.battery, "%");
  $("tile-voltage").textContent = my.voltage != null ? `${my.voltage.toFixed(2)}V` : "—";
  $("tile-snr").textContent = fmt(my.snr, " dB");
  $("tile-uptime").textContent = fmtUptime(my.uptime);
}

// Classify how we actually know about a node: heard directly over our
// radio (the exciting case), heard through the mesh, or only learned via
// the internet (MQTT). via_mqtt from the node DB is authoritative.
function contactBadge(n, myId) {
  if (n.id && n.id === myId) return { label: "this node", cls: "badge-self" };
  if (n.via_mqtt === true) return { label: "MQTT", cls: "badge-mqtt" };
  if (n.hops === 0 && n.snr != null)
    return { label: `RF direct · ${n.snr} dB`, cls: "badge-rf" };
  if (n.hops != null && n.hops >= 1)
    return { label: `RF · ${n.hops} hop${n.hops > 1 ? "s" : ""}`, cls: "badge-relay" };
  if (n.via === "mqtt") return { label: "MQTT", cls: "badge-mqtt" };
  return { label: "—", cls: "badge-unknown" };
}

function renderNodes(nodes, myId) {
  const tbody = $("nodes-table").querySelector("tbody");
  const recentOnly = $("nodes-recent").checked;
  const now = Date.now() / 1000;
  const list = recentOnly
    ? nodes.filter((n) => n.last_heard && now - n.last_heard < 86400)
    : nodes;
  $("node-count").textContent = recentOnly
    ? `${list.length} of ${nodes.length}` : `${nodes.length}`;
  if (!list.length) {
    tbody.innerHTML = `<tr><td colspan="5" class="empty">${
      nodes.length ? "none heard in the last 24h" : "No nodes heard yet"}</td></tr>`;
    return;
  }
  const sorted = [...list].sort((a, b) => (b.last_heard || 0) - (a.last_heard || 0));
  tbody.replaceChildren(...sorted.map((n) => {
    const tr = document.createElement("tr");
    for (const text of [
      n.name || n.short_name || n.id,
      fmt(n.battery, "%"),
      n.snr != null ? `${n.snr} dB` : "—",
      fmtAgo(n.last_heard),
    ]) {
      const td = document.createElement("td");
      td.textContent = text;
      tr.appendChild(td);
    }
    const badge = contactBadge(n, myId);
    const td = document.createElement("td");
    const span = document.createElement("span");
    span.className = "badge " + badge.cls;
    span.textContent = badge.label;
    td.appendChild(span);
    tr.appendChild(td);
    return tr;
  }));
}

function renderMsgList(log, messages) {
  if (!messages.length) {
    log.innerHTML = `<li class="empty">No messages yet</li>`;
    return;
  }
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 30;
  log.replaceChildren(...messages.map((m) => {
    const li = document.createElement("li");
    li.className = m.direction === "tx" ? "msg-tx" : "msg-rx";
    const meta = document.createElement("span");
    meta.className = "msg-meta";
    const when = m.time ? new Date(m.time * 1000).toLocaleTimeString() : "";
    let metaText = `${when} ${m.from || "?"}`;
    if (m.hops != null) metaText += ` · ${m.hops} hop${m.hops === 1 ? "" : "s"}`;
    meta.textContent = metaText;
    li.appendChild(meta);
    li.appendChild(document.createTextNode(m.text || ""));
    return li;
  }));
  if (atBottom) log.scrollTop = log.scrollHeight;
}

function renderMeshCore(snap) {
  const mc = snap.meshcore || {};
  const src = (snap.sources || {}).meshcore;
  setPill($("mc-status"),
    src ? src.connected : null,
    "node: " + (src ? (src.connected ? "connected" : src.detail || "down") : "—"));

  const paused = !!(src && (src.detail || "").toLowerCase().includes("paused"));
  const toggle = $("mc-toggle");
  if (paused) {
    toggle.textContent = "Enable auto-logging";
    toggle.title = "Dashboard holds the connection and logs everything, yielding to the phone when it connects";
    toggle.dataset.action = "resume";
  } else {
    toggle.textContent = "Release to phone";
    toggle.title = "Fully release Board 2 to the phone — dashboard won't reconnect until you re-enable";
    toggle.dataset.action = "pause";
  }

  const self = mc.self || {};
  $("mc-name").textContent = self.name || "—";
  $("mc-battery").textContent = self.battery != null ? `${self.battery}%` : "—";

  const ri = $("mc-radio-info");
  if (self.radio_freq != null) {
    ri.textContent = `${self.radio_freq} MHz · BW ${self.radio_bw} · SF ${self.radio_sf} · CR ${self.radio_cr}`;
    setPlaceholder("rf-freq", self.radio_freq);
    setPlaceholder("rf-bw", self.radio_bw);
    setPlaceholder("rf-sf", self.radio_sf);
    setPlaceholder("rf-cr", self.radio_cr);
  } else {
    ri.textContent = "—";
  }

  const contacts = mc.contacts || [];
  $("mc-contact-count").textContent = contacts.length || "0";
  $("mc-contact-badge").textContent = contacts.length ? `${contacts.length} live` : "";
  $("mc-logged").textContent = mc.logged != null ? mc.logged : 0;
  $("mc-contacts-list").replaceChildren(...contacts.map((c) => {
    const o = document.createElement("option");
    o.value = c.name || c.key;
    return o;
  }));

  const tbody = $("mc-contacts-table").querySelector("tbody");
  if (!contacts.length) {
    tbody.innerHTML = `<tr><td colspan="3" class="empty">No contacts yet</td></tr>`;
  } else {
    const sorted = [...contacts].sort((a, b) => (b.last_advert || 0) - (a.last_advert || 0));
    tbody.replaceChildren(...sorted.map((c) => {
      const tr = document.createElement("tr");
      const name = c.name || c.key;
      const nameTd = document.createElement("td");
      nameTd.textContent = name;
      const whenTd = document.createElement("td");
      whenTd.textContent = c.last_advert ? fmtAgo(c.last_advert) : "—";

      const actTd = document.createElement("td");
      const actions = document.createElement("div");
      actions.className = "row-actions";
      // Only companions (type 1) are direct-message targets.
      if (c.type !== 2 && c.type !== 3) {
        const dm = document.createElement("button");
        dm.className = "icon-btn"; dm.type = "button";
        dm.textContent = "✉"; dm.title = "Message";
        dm.addEventListener("click", () => dmContact(name));
        actions.appendChild(dm);
      }
      const located = c.adv_lat && c.adv_lon && Math.abs(c.adv_lat) > 0.01;
      const map = document.createElement("button");
      map.className = "icon-btn"; map.type = "button";
      map.textContent = "📍"; map.title = located ? "Show on map" : "No location known";
      map.disabled = !located;
      if (located) map.addEventListener("click", () => showOnMap(c.key, c.adv_lat, c.adv_lon));
      actions.appendChild(map);
      actTd.appendChild(actions);

      tr.append(nameTd, whenTd, actTd);
      return tr;
    }));
  }

  renderMsgList($("mc-message-log"), mc.messages || []);
  renderChannels(mc);
}

function renderChannels(mc) {
  const channels = mc.channels || [];
  const chips = $("mc-channels");
  if (!channels.length) {
    chips.innerHTML = `<span class="empty">No channels configured on the node</span>`;
    _selChannel = null;
  } else {
    if (_selChannel == null || !channels.some((c) => c.idx === _selChannel)) {
      _selChannel = channels[0].idx;
    }
    chips.replaceChildren(...channels.map((c) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chan-chip" + (c.idx === _selChannel ? " is-active" : "");
      b.textContent = c.name;
      b.addEventListener("click", () => { _selChannel = c.idx; if (lastSnap) renderMeshCore(lastSnap); });
      return b;
    }));
  }
  const all = mc.channel_messages || [];
  const shown = _selChannel == null ? all : all.filter((m) => m.channel_idx === _selChannel);
  renderMsgList($("mc-channel-log"), shown);
  $("mc-channel-text").disabled = _selChannel == null;
}

// Draw a minimal sparkline: a thin polyline scaled to the 200x40 viewBox.
// Null values (e.g. best_snr with no RF nodes) are skipped, not zeroed.
function sparkline(svgEl, points, key, color) {
  const W = 200, H = 40, pad = 3;
  const valid = points.filter((p) => p[key] != null);
  if (valid.length < 2) { svgEl.innerHTML = ""; return; }
  const nums = valid.map((p) => p[key]);
  let min = Math.min(...nums), max = Math.max(...nums);
  if (min === max) { min -= 1; max += 1; }
  const t0 = points[0].t, t1 = points[points.length - 1].t;
  const span = (t1 - t0) || 1;
  const x = (t) => pad + ((t - t0) / span) * (W - 2 * pad);
  const y = (v) => H - pad - ((v - min) / (max - min)) * (H - 2 * pad);
  const pts = valid.map((p) => `${x(p.t).toFixed(1)},${y(p[key]).toFixed(1)}`).join(" ");
  svgEl.innerHTML =
    `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2" ` +
    `stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`;
}

function renderSignalHistory(snap) {
  const h = snap.history || [];
  sparkline($("spark-snr"), h, "best_snr", "var(--net-mesh)");
  sparkline($("spark-rf"), h, "rf_nodes", "var(--contact-relay)");
  const last = h[h.length - 1] || {};
  $("spark-snr-now").textContent = last.best_snr != null ? `${last.best_snr} dB` : "—";
  $("spark-rf-now").textContent = last.rf_nodes != null ? last.rf_nodes : "—";
}

/* ---------- watchdog / alerts ---------- */

const SRC_LABEL = {
  meshtastic_tcp: "Board 1 (Meshtastic)",
  mqtt: "MQTT broker",
  meshcore: "Board 2 (MeshCore)",
};
const alertPrev = { sources: {}, batteryLow: false };

function fireAlert(msg) {
  const when = new Date().toLocaleTimeString();
  const banner = $("alert-banner");
  banner.hidden = false;
  banner.textContent = `⚠ ${when} — ${msg}  (click to dismiss)`;
  if ("Notification" in window && Notification.permission === "granted") {
    try { new Notification("LoRa Mesh Dashboard", { body: msg }); } catch (e) { /* ignore */ }
  }
}

function checkWatchdog(snap) {
  const sources = snap.sources || {};
  for (const [name, s] of Object.entries(sources)) {
    const connected = !!(s && s.connected);
    const detail = (s && s.detail || "").toLowerCase();
    const intentional = detail.includes("paused") || detail.includes("disabled")
      || detail.includes("yielding");
    if (alertPrev.sources[name] === true && !connected && !intentional) {
      fireAlert(`${SRC_LABEL[name] || name} went offline`);
    }
    alertPrev.sources[name] = connected;
  }
  const b = (snap.my_node || {}).battery;
  // 101 = "on external power" sentinel, not a real low reading.
  const low = b != null && b > 0 && b !== 101 && b <= 20;
  if (low && alertPrev.batteryLow === false) fireAlert(`Board 1 battery low: ${b}%`);
  if (b != null) alertPrev.batteryLow = low;
}

let lastSnap = null;
function render(snap) {
  lastSnap = snap;
  checkWatchdog(snap);
  renderSources(snap.sources || {});
  renderMyNode(snap.my_node || {});
  renderNodes(snap.nodes || [], (snap.my_node || {}).id);
  renderMsgList($("message-log"), snap.messages || []);
  renderSignalHistory(snap);
  renderMeshCore(snap);
}

/* ---------- MeshCore contact map (Leaflet) ---------- */
let _map = null, _markers = null;
let _selChannel = null;              // selected channel idx in the Channels tab
let _markerByKey = {};               // contact key -> Leaflet marker, for "show on map"
let _typeFilter = new Set([1, 2, 3]); // contact types shown on the map

function dmContact(name) {
  if (!name) return;
  const to = $("mc-send-to");
  to.value = name;
  to.scrollIntoView({ behavior: "smooth", block: "center" });
  $("mc-send-text").focus();
}
window._dm = dmContact;

let _allLocated = [];

async function loadMapContacts() {
  if (!_markers) return;
  try {
    const data = await (await fetch("/api/meshcore/contacts")).json();
    _allLocated = (data.contacts || []).filter(
      (c) => c.lat && c.lon && Math.abs(c.lat) > 0.01);
    plotContacts();
  } catch (e) { /* ignore */ }
}

function plotContacts() {
  if (!_markers) return;
  const located = _allLocated.filter((c) => _typeFilter.has(c.type || 1));
  $("map-count").textContent = located.length;
  _markers.clearLayers();
  _markerByKey = {};
  const pts = [];
  {
    located.forEach((c) => {
      // MeshCore contact types: 1 = companion (DM target), 2 = repeater,
      // 3 = room server (you join/post to it, not a DM target).
      const rep = c.type === 2;
      const room = c.type === 3;
      const kind = rep ? "◆ Repeater" : room ? "▣ Room server" : "● Companion";
      const fill = rep ? "#eb6834" : room ? "#a875e0" : "#4bd07a";
      const nm = c.name || c.key || "?";
      const mk = L.circleMarker([c.lat, c.lon], {
        radius: 6, weight: 1, color: "#1a1a19",
        fillColor: fill, fillOpacity: 0.85,
      });
      const esc = (s) => String(s).replace(/[<>&]/g,
        (m) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[m]));
      const info = [
        `<b>${esc(nm)}</b>`,
        `${kind} · <code>${esc((c.key || "").slice(0, 10))}</code>`,
        `📍 ${(+c.lat).toFixed(4)}, ${(+c.lon).toFixed(4)}`,
        hopLabel(c.hops),
        c.last_advert ? `last advert: ${fmtAgo(c.last_advert)}` : null,
        c.first_seen ? `first seen: ${fmtAgo(c.first_seen)}` : null,
        c.last_seen ? `logged: ${fmtAgo(c.last_seen)}` : null,
      ].filter(Boolean).join("<br>");
      mk.bindTooltip(info, { direction: "top", opacity: 0.96 });
      const safe = nm.replace(/[<>]/g, "").replace(/'/g, "\\'");
      // Only companions are direct-message targets.
      const canDM = !rep && !room;
      mk.bindPopup(info +
        (canDM ? `<br><a href="#" onclick="window._dm('${safe}');return false;">✉ message</a>` : ""));
      // don't let the hover tooltip cover the popup's message link
      mk.on("popupopen", () => mk.unbindTooltip());
      mk.on("popupclose", () => mk.bindTooltip(info, { direction: "top", opacity: 0.96 }));
      _markers.addLayer(mk);
      if (c.key) _markerByKey[c.key] = mk;
      pts.push([c.lat, c.lon]);
    });
    if (pts.length) _map.fitBounds(pts, { padding: [30, 30], maxZoom: 12 });
  }
}

// Zoom the map to a contact and open its popup (called from the contacts table).
function showOnMap(key, lat, lon) {
  const mapEl = $("map");
  if (mapEl) mapEl.scrollIntoView({ behavior: "smooth", block: "center" });
  const go = () => {
    const mk = key && _markerByKey[key];
    if (mk) {
      _map.setView(mk.getLatLng(), 14);
      mk.openPopup();
    } else if (lat && lon) {
      _map.setView([lat, lon], 14);
    }
  };
  // If the map hasn't plotted yet, load then zoom.
  if (_map && Object.keys(_markerByKey).length) go();
  else loadMapContacts().then(go);
}
window._showOnMap = showOnMap;

function initMap() {
  if (!window.L || !document.getElementById("map")) return;
  _map = L.map("map", { scrollWheelZoom: false }).setView([30.27, -97.74], 9);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap", maxZoom: 18,
  }).addTo(_map);
  _markers = L.layerGroup().addTo(_map);
  loadMapContacts();
}
{
  const r = $("map-refresh");
  if (r) r.addEventListener("click", (e) => { e.preventDefault(); loadMapContacts(); });
}
// Map type filter
document.querySelectorAll(".map-type").forEach((cb) => {
  cb.addEventListener("change", () => {
    _typeFilter = new Set(
      [...document.querySelectorAll(".map-type:checked")].map((c) => +c.value));
    plotContacts();
  });
});
initMap();

/* ---------- MeshCore Direct/Channels tabs ---------- */
document.querySelectorAll(".tabs .tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const which = tab.dataset.tab;
    document.querySelectorAll(".tabs .tab").forEach((t) => {
      const on = t === tab;
      t.classList.toggle("is-active", on);
      t.setAttribute("aria-selected", on ? "true" : "false");
    });
    document.querySelectorAll(".tabpane").forEach((p) => {
      p.hidden = p.dataset.pane !== which;
    });
  });
});

/* ---------- websocket ---------- */

let retryMs = 1000;

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => {
    retryMs = 1000;
    setPill($("ws-status"), true, "backend: live");
  };
  ws.onmessage = (ev) => {
    try { render(JSON.parse(ev.data)); } catch (e) { console.error(e); }
  };
  ws.onclose = () => {
    setPill($("ws-status"), false, "backend: reconnecting…");
    setTimeout(connect, retryMs);
    retryMs = Math.min(retryMs * 2, 15000);
  };
  ws.onerror = () => ws.close();
}
connect();

/* ---------- send form ---------- */

$("send-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const input = $("send-text");
  const button = ev.target.querySelector("button");
  const result = $("send-result");
  const text = input.value.trim();
  if (!text) return;
  button.disabled = true;
  result.textContent = "sending…";
  try {
    const resp = await fetch("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (resp.ok) {
      result.textContent = "sent ✓ (no ack expected until another node is in range)";
      input.value = "";
    } else {
      const body = await resp.json().catch(() => ({}));
      result.textContent = "failed: " + (body.detail || resp.status);
    }
  } catch (e) {
    result.textContent = "failed: " + e;
  } finally {
    button.disabled = false;
  }
});

$("nodes-recent").addEventListener("change", () => { if (lastSnap) render(lastSnap); });

$("alerts-btn").addEventListener("click", async () => {
  if (!("Notification" in window)) {
    $("alerts-btn").textContent = "🔔 not supported";
    return;
  }
  const perm = await Notification.requestPermission();
  $("alerts-btn").textContent = perm === "granted" ? "🔔 Alerts on" : "🔔 Enable alerts";
});

$("alert-banner").addEventListener("click", (ev) => { ev.currentTarget.hidden = true; });

$("mc-toggle").addEventListener("click", async (ev) => {
  const action = ev.target.dataset.action || "pause";
  ev.target.disabled = true;
  try {
    await fetch("/api/meshcore/" + action, { method: "POST" });
  } catch (e) {
    /* state reflects on the next WS snapshot */
  }
  setTimeout(() => { ev.target.disabled = false; }, 600);
});

$("mesh-toggle").addEventListener("click", async (ev) => {
  const action = ev.target.dataset.action || "pause";
  ev.target.disabled = true;
  try {
    await fetch("/api/meshtastic/" + action, { method: "POST" });
  } catch (e) { /* reflects on next snapshot */ }
  setTimeout(() => { ev.target.disabled = false; }, 600);
});

$("mc-advert").addEventListener("click", async (ev) => {
  ev.target.disabled = true;
  try {
    const r = await fetch("/api/meshcore/advert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ flood: true }),
    });
    ev.target.textContent = r.ok ? "Advert sent ✓" : "advert failed";
  } catch (e) {
    ev.target.textContent = "advert failed";
  }
  setTimeout(() => { ev.target.textContent = "Send advert"; ev.target.disabled = false; }, 2500);
});

$("rf-apply").addEventListener("click", async (ev) => {
  const num = (id) => $(id).value || $(id).placeholder;
  const body = {
    freq: parseFloat(num("rf-freq")),
    bw: parseFloat(num("rf-bw")),
    sf: parseInt(num("rf-sf"), 10),
    cr: parseInt(num("rf-cr"), 10),
  };
  const result = $("rf-result");
  if (Object.values(body).some((v) => Number.isNaN(v))) {
    result.textContent = "fill in all four fields";
    return;
  }
  ev.target.disabled = true;
  result.textContent = "applying…";
  try {
    const r = await fetch("/api/meshcore/radio", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (r.ok) result.textContent = "radio updated ✓";
    else { const b = await r.json().catch(() => ({})); result.textContent = "failed: " + (b.detail || r.status); }
  } catch (e) {
    result.textContent = "failed: " + e;
  } finally {
    ev.target.disabled = false;
  }
});

$("mc-send-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const to = $("mc-send-to").value.trim();
  const text = $("mc-send-text").value.trim();
  const button = ev.target.querySelector("button");
  const result = $("mc-send-result");
  if (!to || !text) {
    result.textContent = "enter a contact name and a message";
    return;
  }
  button.disabled = true;
  result.textContent = "sending…";
  try {
    const resp = await fetch("/api/meshcore/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to, text }),
    });
    if (resp.ok) {
      result.textContent = "sent ✓";
      $("mc-send-text").value = "";
    } else {
      const body = await resp.json().catch(() => ({}));
      result.textContent = "failed: " + (body.detail || resp.status);
    }
  } catch (e) {
    result.textContent = "failed: " + e;
  } finally {
    button.disabled = false;
  }
});

$("mc-import-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const input = $("mc-import-file");
  const result = $("mc-import-result");
  const file = input.files && input.files[0];
  if (!file) { result.textContent = "choose an exported .db file first"; return; }
  const button = ev.target.querySelector("button");
  button.disabled = true;
  result.textContent = "importing…";
  try {
    const fd = new FormData();
    fd.append("file", file);
    const resp = await fetch("/api/meshcore/import", { method: "POST", body: fd });
    const body = await resp.json().catch(() => ({}));
    if (resp.ok) {
      result.textContent =
        `✓ imported ${body.contact_messages} DMs, ${body.channel_messages} channel msgs, ` +
        `${body.contacts} contacts (${body.skipped} already had)`;
      input.value = "";
      loadMapContacts();
    } else {
      result.textContent = "failed: " + (body.detail || resp.status);
    }
  } catch (e) {
    result.textContent = "failed: " + e;
  } finally {
    button.disabled = false;
  }
});

/* ---------- prune contacts ---------- */
function pruneBody(apply) {
  const body = { apply };
  if ($("prune-stale").checked) body.stale_days = Math.max(1, +$("prune-days").value || 90);
  if ($("prune-far").checked) body.max_km = Math.max(1, +$("prune-km").value || 50);
  return body;
}

async function pruneRequest(apply) {
  const body = pruneBody(apply);
  if (body.stale_days == null && body.max_km == null) {
    $("prune-result").textContent = "pick at least one rule (stale and/or distance)";
    return null;
  }
  const resp = await fetch("/api/meshcore/prune", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) { $("prune-result").textContent = "failed: " + (data.detail || resp.status); return null; }
  return data;
}

$("prune-preview").addEventListener("click", async () => {
  $("prune-result").textContent = "checking…";
  const d = await pruneRequest(false);
  if (!d) return;
  $("prune-result").textContent =
    `${d.candidates} of ${d.repeaters} repeaters match (node has ${d.total} contacts).`;
  const list = $("prune-list");
  list.replaceChildren(...(d.sample || []).map((c) => {
    const li = document.createElement("li");
    const nm = document.createElement("span");
    nm.textContent = c.name;
    const meta = document.createElement("span");
    meta.className = "pl-meta";
    const bits = [];
    if (c.km != null) bits.push(`${c.km} km`);
    if (c.age_days != null) bits.push(`${c.age_days}d`);
    meta.textContent = bits.join(" · ") + (c.reasons ? "  (" + c.reasons.join("+") + ")" : "");
    li.append(nm, meta);
    return li;
  }));
  const apply = $("prune-apply");
  apply.disabled = d.candidates === 0;
  apply.textContent = d.candidates ? `Remove ${d.candidates}` : "Remove";
});

$("prune-apply").addEventListener("click", async () => {
  const btn = $("prune-apply");
  btn.disabled = true;
  $("prune-result").textContent = "removing…";
  const d = await pruneRequest(true);
  if (!d) { return; }
  $("prune-result").textContent =
    `✓ removed ${d.removed} repeater${d.removed === 1 ? "" : "s"} — node now has ${d.total - d.removed} contacts.`;
  $("prune-list").replaceChildren();
  btn.textContent = "Remove";
  loadMapContacts();
});

$("mc-channel-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const text = $("mc-channel-text").value.trim();
  const button = ev.target.querySelector("button");
  const result = $("mc-channel-result");
  if (_selChannel == null) { result.textContent = "no channel selected"; return; }
  if (!text) { result.textContent = "enter a message"; return; }
  button.disabled = true;
  result.textContent = "sending…";
  try {
    const resp = await fetch("/api/meshcore/channel/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idx: _selChannel, text }),
    });
    if (resp.ok) {
      result.textContent = "sent ✓";
      $("mc-channel-text").value = "";
    } else {
      const body = await resp.json().catch(() => ({}));
      result.textContent = "failed: " + (body.detail || resp.status);
    }
  } catch (e) {
    result.textContent = "failed: " + e;
  } finally {
    button.disabled = false;
  }
});
