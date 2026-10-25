// NQMT laptop console. Reads the hub directly over its own WebSocket (read-only: it never sends
// on it, because its clock is not the laptop's monotonic clock that every hub `t` uses), and sends
// commands, uploads and imports through the library server's HTTP API.
"use strict";

const $ = (id) => document.getElementById(id);
const S = { songs: [], selected: null, hub: null, hubUrl: null, lines: [], lastFletcher: 0, session: {} };
try { S.selected = localStorage.getItem("nqmt.selected"); } catch (e) { /* private mode */ }

function fmtDur(s) {
  if (s == null) return "-";
  const m = Math.floor(s / 60), r = Math.round(s % 60);
  return `${m}:${String(r).padStart(2, "0")}`;
}
function esc(t) { return String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function pill(id, on, text) { const p = $(id); p.className = "pill " + (on ? "on" : "off"); if (text) p.textContent = text; }

// ---- songs ----
function renderSongs() {
  const tb = $("songs").querySelector("tbody");
  tb.innerHTML = "";
  for (const s of S.songs) {
    const tr = document.createElement("tr");
    if (s.id === S.selected) tr.className = "sel";
    const badges = [s.backing ? "backing" : "no backing", s.pickup_s ? "pickup" : null].filter(Boolean)
      .map((b) => `<span class="badge">${b}</span>`).join("");
    tr.innerHTML = `<td>${esc(s.title || s.id)}${badges}<span class="artist">${esc(s.artist || "")} <span class="muted">${esc(s.id)}</span></span></td>
      <td>${s.bpm ?? "-"}</td><td>${s.bars ?? "-"}</td><td>${fmtDur(s.duration_s)}</td><td>${esc(s.source)}</td>
      <td><button class="del" title="delete">&times;</button></td>`;
    tr.addEventListener("click", (ev) => {
      if (ev.target.classList.contains("del")) return deleteSong(s);
      S.selected = s.id;
      try { localStorage.setItem("nqmt.selected", s.id); } catch (e) { /* ignore */ }
      renderSongs();
    });
    tb.appendChild(tr);
  }
  $("no-songs").hidden = S.songs.length > 0;
  $("song-count").textContent = S.songs.length ? `${S.songs.length} in library` : "";
  const sel = S.songs.find((s) => s.id === S.selected);
  $("sel-title").textContent = sel ? `${sel.title} (${sel.bars} bars)` : "none";
  $("btn-start").disabled = !sel;
  $("from-bar").max = sel ? sel.bars : "";
}

async function loadSongs() {
  try {
    const r = await fetch("/api/songs");
    const j = await r.json();
    S.songs = j.songs || [];
    pill("pill-lib", true);
    renderSongs();
  } catch (e) { pill("pill-lib", false); }
}

async function deleteSong(s) {
  if (!confirm(`Delete "${s.title}" from the library?`)) return;
  await fetch(`/api/songs/${encodeURIComponent(s.id)}`, { method: "DELETE" });
  if (S.selected === s.id) S.selected = null;
  loadSongs();
}

// ---- commands ----
async function command(body) {
  $("cmd-status").textContent = "sending...";
  try {
    const r = await fetch("/api/command", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json();
    $("cmd-status").textContent = j.ok ? `sent session.command ${JSON.stringify(j.sent)}` : `not sent: ${j.error}`;
  } catch (e) { $("cmd-status").textContent = "library server unreachable"; }
}
$("btn-start").onclick = () => command({ action: "start", song_id: S.selected, from_bar: Math.max(1, parseInt($("from-bar").value || "1", 10)) });
$("btn-stop").onclick = () => command({ action: "stop", ...(S.session.bar ? { from_bar: S.session.bar } : {}) });
$("btn-quit").onclick = () => command({ action: "quit" });

// ---- upload / import ----
const drop = $("drop");
drop.addEventListener("click", (e) => { if (e.target.tagName !== "INPUT") $("file").click(); });
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); upload([...e.dataTransfer.files]); });
$("file").addEventListener("change", (e) => upload([...e.target.files]));

async function upload(files) {
  for (const f of files) {
    if (!/\.midi?$/i.test(f.name)) { $("import-status").textContent = `${f.name}: not a .mid file`; continue; }
    $("import-status").textContent = `importing ${f.name} (rendering the backing can take a few seconds)...`;
    const fd = new FormData();
    fd.append("file", f);
    if (files.length === 1) {
      if ($("up-title").value.trim()) fd.append("title", $("up-title").value.trim());
      if ($("up-artist").value.trim()) fd.append("artist", $("up-artist").value.trim());
    }
    try {
      const r = await fetch("/api/upload", { method: "POST", body: fd });
      const j = await r.json();
      $("import-status").textContent = j.ok
        ? `imported ${j.song.title}: ${j.song.notes} drum notes, ${j.song.bars} bars, backing ${j.song.backing ? "yes" : "no"}`
        : `${f.name}: ${j.error}`;
      if (j.ok) { S.selected = j.song.id; }
    } catch (e) { $("import-status").textContent = `${f.name}: upload failed`; }
  }
  $("up-title").value = $("up-artist").value = "";
  loadSongs();
}

$("songsterr-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const song = $("songsterr").value.trim();
  if (!song) return;
  $("import-status").textContent = `fetching ${song} from Songsterr...`;
  try {
    const r = await fetch("/api/songsterr", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ song }) });
    const j = await r.json();
    $("import-status").textContent = j.ok ? `imported ${j.song.title} (${j.song.bars} bars, drums only)` : `Songsterr: ${j.error}`;
    if (j.ok) { S.selected = j.song.id; $("songsterr").value = ""; }
  } catch (err) { $("import-status").textContent = "Songsterr import failed"; }
  loadSongs();
});

// ---- live hub view ----
function traffic(msg) {
  if (msg.type === "fletcher.audio") return;
  const li = document.createElement("li");
  li.textContent = `${msg.src || "?"} ${msg.type} ${JSON.stringify(msg.data).slice(0, 220)}`;
  const ol = $("traffic");
  ol.prepend(li);
  while (ol.children.length > 80) ol.lastChild.remove();
}

function onSession(d) {
  S.session = { ...S.session, ...d };
  const st = d.state || "-";
  $("st-state").textContent = st.replace("_", " ");
  $("st-state").className = "big " + st;
  $("st-song").textContent = d.song_id ?? S.session.song_id ?? "-";
  $("st-bar").textContent = d.bar ?? "-";
  $("st-bpm").textContent = d.bpm != null ? Number(d.bpm).toFixed(1) : "-";
  $("st-from").textContent = d.from_bar ?? "-";
}

function onFletcher(d) {
  S.lastFletcher = Date.now();
  pill("pill-fletcher", true);
  if (d.mood) $("fl-mood").textContent = d.mood;
  $("fl-action").textContent = [d.action, d.gesture].filter(Boolean).join(" / ");
  if (typeof d.anger === "number") $("fl-anger").style.width = `${Math.round(Math.max(0, Math.min(1, d.anger)) * 100)}%`;
}

function onSpeech(d) {
  if (!d.text) return;
  S.lines.unshift(d.text);
  S.lines = S.lines.slice(0, 12);
  $("lines").innerHTML = S.lines.map((t) => `<li>${esc(t)}</li>`).join("");
}

// session.result's rushing_bars / dragging_bars: bar numbers -> "4, 31-35" (runs of 3+ as ranges).
function fmtBars(v) {
  if (typeof v === "number") return `${v} bars`;
  const bars = [...new Set((Array.isArray(v) ? v : []).map(Number).filter(Number.isFinite).map(Math.trunc))]
    .sort((a, b) => a - b);
  if (!bars.length) return "-";
  const parts = [];
  for (let i = 0; i < bars.length;) {
    let j = i;
    while (j + 1 < bars.length && bars[j + 1] === bars[j] + 1) j++;
    if (j - i >= 2) parts.push(`${bars[i]}-${bars[j]}`);
    else for (let k = i; k <= j; k++) parts.push(String(bars[k]));
    i = j + 1;
  }
  return `${parts.join(", ")} (${bars.length})`;
}

function onResult(d) {
  const rows = [["song", d.song_id], ["bars played", d.bars_played], ["cutoffs", d.cutoffs], ["notes", d.notes],
    ["perfect / good / off", `${d.perfect ?? 0} / ${d.good ?? 0} / ${d.off ?? 0}`],
    ["miss / extra", `${d.miss ?? 0} / ${d.extra ?? 0}`],
    ["mean error", d.mean_error_ms != null ? `${Number(d.mean_error_ms).toFixed(1)} ms` : "-"],
    ["rushing bars", fmtBars(d.rushing_bars)], ["dragging bars", fmtBars(d.dragging_bars)]];
  $("result").className = "";
  $("result").innerHTML = `<table>${rows.map(([k, v]) => `<tr><td class="muted">${k}</td><td>${esc(v)}</td></tr>`).join("")}</table>`;
}

function connectHub() {
  if (!S.hubUrl) return;
  let ws;
  try { ws = new WebSocket(S.hubUrl); } catch (e) { setTimeout(connectHub, 2000); return; }
  S.hub = ws;
  ws.onopen = () => pill("pill-hub", true, "hub");
  ws.onclose = () => { pill("pill-hub", false, "hub"); setTimeout(connectHub, 1500); };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (!msg || typeof msg.type !== "string") return;
    const d = (msg.data && typeof msg.data === "object") ? msg.data : {};
    traffic({ ...msg, data: d });
    switch (msg.type) {
      case "session.state": onSession(d); break;
      case "fletcher.state": onFletcher(d); break;
      case "fletcher.speech": onSpeech(d); break;
      case "session.result": onResult(d); break;
      case "library.songs": S.songs = d.songs || []; renderSongs(); break;
    }
  };
}

setInterval(() => { if (Date.now() - S.lastFletcher > 15000) pill("pill-fletcher", false); }, 3000);

(async function init() {
  await loadSongs();
  try {
    const c = await (await fetch("/api/config")).json();
    S.hubUrl = c.hub_url;
  } catch (e) { /* no hub */ }
  if (S.hubUrl) connectHub(); else pill("pill-hub", false, "no hub");
})();
