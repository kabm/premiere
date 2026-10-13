/* Rushes Studio front end. Talks to the local server (app.py); no external resources. */
"use strict";

const params = new URLSearchParams(location.search);
const TOKEN = params.get("token") || "";
const $ = (id) => document.getElementById(id);

const FPS_CHOICES = ["23.976", "24", "25", "29.97", "30", "48", "50", "59.94", "60"];
const SIZES = [
  ["1920x1080", "HD 1080p"], ["3840x2160", "UHD 4K"], ["4096x2160", "DCI 4K"], ["2048x1080", "DCI 2K"],
  ["1280x720", "HD 720p"], ["1080x1920", "Vertical 1080x1920"], ["1080x1080", "Square 1080"],
  ["7680x4320", "8K UHD"],
];
const TRANSITIONS = ["", "cross_dissolve", "dip_to_black", "dip_to_white", "film_dissolve", "additive_dissolve",
  "wipeleft", "wiperight", "wipeup", "wipedown", "slideleft", "slideright", "slideup", "slidedown",
  "circleopen", "circleclose", "radial", "zoomin", "pixelize", "smoothleft", "smoothright", "diagtl", "hblur"];

const state = {
  info: null,
  project: null,
  path: null,
  baseDir: null,
  dirty: false,
  sel: null,              // {track: "clips"|"overlays"|"titles"|"audio", index}
  tab: null,
  playhead: 0,
  layout: null,           // from /api/validate
  layoutError: null,
  zoom: 60,               // pixels per second
  history: [],
  future: [],
  media: {},              // path -> probe result
  browserPath: null,
  browserSel: null,
  jobs: [],
  previewJob: null,
};

/* ------------------------------------------------------------------ utils */

async function api(path, body) {
  const opts = { headers: { "X-Rushes-Token": TOKEN } };
  if (body !== undefined) {
    opts.method = "POST";
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  let data;
  try { data = await res.json(); } catch (e) { data = { error: `HTTP ${res.status}` }; }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function toast(msg, isError) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (isError ? " error" : "");
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, isError ? 7000 : 2500);
}

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null) e.append(k.nodeType ? k : String(k));
  return e;
}

function fpsValue() {
  const f = String((state.project.settings || {}).fps || "24");
  if (f.includes("/")) { const [a, b] = f.split("/").map(Number); return a / b; }
  return { "23.976": 24000 / 1001, "29.97": 30000 / 1001, "59.94": 60000 / 1001 }[f] || Number(f) || 24;
}

function timecode(sec) {
  const fps = fpsValue();
  const base = Math.round(fps);
  let frames = Math.round(sec * fps);
  const f = frames % base; frames = (frames - f) / base;
  const s = frames % 60; frames = (frames - s) / 60;
  const m = frames % 60; const h = (frames - m) / 60;
  const p = (n) => String(n).padStart(2, "0");
  return `${p(h)}:${p(m)}:${p(s)}:${p(f)}`;
}

function getPath(obj, path) {
  return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

function setPath(obj, path, value) {
  const keys = path.split(".");
  let o = obj;
  for (const k of keys.slice(0, -1)) {
    if (o[k] == null || typeof o[k] !== "object") o[k] = {};
    o = o[k];
  }
  const last = keys[keys.length - 1];
  if (value === undefined || value === "" || value === null) delete o[last];
  else o[last] = value;
  // Remove objects left empty (e.g. "color": {} after resetting the last colour setting).
  for (let depth = keys.length - 1; depth > 0; depth--) {
    let parent = obj;
    for (const k of keys.slice(0, depth - 1)) parent = parent && parent[k];
    const key = keys[depth - 1];
    const child = parent && parent[key];
    if (child && typeof child === "object" && !Array.isArray(child) && Object.keys(child).length === 0) delete parent[key];
    else break;
  }
}

function basename(p) { return String(p || "").split(/[\\/]/).pop(); }
function dirname(p) { const parts = String(p).split(/[\\/]/); parts.pop(); return parts.join(p.includes("\\") ? "\\" : "/"); }
function clone(x) { return JSON.parse(JSON.stringify(x)); }
function round(n, d = 3) { return Math.round(n * 10 ** d) / 10 ** d; }

/* --------------------------------------------------------------- project */

function newProject() {
  return {
    settings: { title: "Untitled", width: 1920, height: 1080, fps: "24" },
    clips: [], overlays: [], titles: [], audio: [],
    master: {},
    export: { preset: "prores_422hq" },
  };
}

function commit(label) {
  state.history.push(clone(state.project));
  if (state.history.length > 200) state.history.shift();
  state.future = [];
  state.dirty = true;
  try { localStorage.setItem("rushes.autosave", JSON.stringify({ project: state.project, path: state.path, baseDir: state.baseDir })); } catch (e) { /* storage is optional */ }
  return label;
}

// Call after mutating state.project (commit() must be called BEFORE the mutation).
function changed() {
  updateTitle();
  scheduleLayout();
}

function undo() {
  if (!state.history.length) return;
  state.future.push(clone(state.project));
  state.project = state.history.pop();
  state.dirty = true;
  fixSelection(); changed(); renderInspector();
}

function redo() {
  if (!state.future.length) return;
  state.history.push(clone(state.project));
  state.project = state.future.pop();
  state.dirty = true;
  fixSelection(); changed(); renderInspector();
}

function fixSelection() {
  if (!state.sel) return;
  const arr = state.project[state.sel.track] || [];
  if (state.sel.index >= arr.length) state.sel = null;
}

function updateTitle() {
  const name = state.path ? basename(state.path) : ((state.project.settings || {}).title || "Untitled");
  $("projectTitle").textContent = name + (state.dirty ? " •" : "");
  document.title = `${name} – Rushes Studio`;
}

let layoutTimer = null;
function scheduleLayout() {
  clearTimeout(layoutTimer);
  layoutTimer = setTimeout(refreshLayout, 120);
}

async function refreshLayout() {
  if (!state.project.clips.length) {
    state.layout = null; state.layoutError = null;
    renderTimeline(); showEmpty(true); return;
  }
  try {
    state.layout = await api("/api/validate", { project: state.project, base_dir: state.baseDir });
    state.layoutError = null;
    state.playhead = Math.min(state.playhead, Math.max(0, state.layout.duration - 1 / fpsValue()));
  } catch (e) {
    state.layoutError = e.message;
  }
  renderTimeline();
  scheduleFrame();
}

/* ---------------------------------------------------------------- frames */

let frameTimer = null, frameSeq = 0;
function scheduleFrame() {
  clearTimeout(frameTimer);
  frameTimer = setTimeout(refreshFrame, 250);
}

function showEmpty(empty) {
  $("viewerEmpty").hidden = !empty;
  $("frame").hidden = empty || !$("previewVideo").hidden;
  if (empty) { $("scopes").hidden = true; }
}

async function refreshFrame() {
  updateTransport();
  if (!state.project.clips.length || state.layoutError) { showEmpty(!state.project.clips.length); return; }
  const seq = ++frameSeq;
  $("spinner").hidden = false;
  const viewer = $("viewer");
  const width = Math.max(320, Math.min(1920, Math.round(viewer.clientWidth * (window.devicePixelRatio || 1) / ($("compareToggle").checked ? 2 : 1))));
  try {
    const res = await api("/api/frame", {
      project: state.project, base_dir: state.baseDir, time: state.playhead,
      compare: $("compareToggle").checked, scopes: $("scopesToggle").checked, width: width - (width % 2),
    });
    if (seq !== frameSeq) return;
    $("frame").src = res.image;
    showEmpty(false);
    $("scopes").hidden = !res.scopes;
    if (res.scopes) $("scopes").src = res.scopes;
  } catch (e) {
    if (seq === frameSeq) toast(e.message, true);
  } finally {
    if (seq === frameSeq) $("spinner").hidden = true;
  }
}

function updateTransport() {
  $("timecode").textContent = timecode(state.playhead);
  $("duration").textContent = "/ " + timecode(state.layout ? state.layout.duration : 0);
  const ph = $("playhead");
  ph.style.left = (state.playhead * state.zoom) + "px";
}

function setPlayhead(t, now) {
  const max = state.layout ? Math.max(0, state.layout.duration - 1 / fpsValue()) : 0;
  const fps = fpsValue();
  state.playhead = Math.min(max, Math.max(0, Math.round(t * fps) / fps));
  updateTransport();
  if (now) refreshFrame(); else scheduleFrame();
}

/* -------------------------------------------------------------- timeline */

function clipLabel(item) {
  if (item.generator) return item.generator;
  if (item.text) return "T  " + item.text;
  return basename(item.source);
}

function renderTimeline() {
  const z = state.zoom;
  const L = state.layout;
  const total = L ? L.duration : 0;
  const width = Math.max(($("tracks").clientWidth || 800), (total + 10) * z);
  for (const id of ["ruler", "trackV2", "trackV1", "trackT", "trackA"]) {
    $(id).innerHTML = ""; $(id).style.width = width + "px";
  }
  // ruler
  const steps = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
  const step = steps.find((s) => s * z >= 70) || 600;
  for (let t = 0; t * z < width; t += step) {
    $("ruler").append(el("div", { class: "tick", style: `left:${t * z}px` }, timecode(t).slice(3, 8)));
  }
  const status = $("timelineStatus");
  status.className = "status" + (state.layoutError ? " error" : "");
  status.textContent = state.layoutError ? state.layoutError : (L ? `${state.project.clips.length} clips · ${round(total, 2)} s` : "");

  const isSel = (track, i) => state.sel && state.sel.track === track && state.sel.index === i;
  state.project.clips.forEach((c, i) => {
    const lay = L && !state.layoutError ? L.clips[i] : null;
    const start = lay ? lay.timeline_start : i * 3;
    const dur = lay ? lay.duration : 3;
    const block = el("div", { class: "clip video" + (isSel("clips", i) ? " sel" : ""), style: `left:${start * z}px;width:${Math.max(6, dur * z - 1)}px`, title: clipLabel(c) },
      c.transition ? el("div", { class: "tr", style: `width:${(c.transition.duration || 1) * z}px` }) : null,
      el("div", {}, clipLabel(c)),
      el("div", { class: "sub" }, `${round(dur, 2)}s` + (c.speed && c.speed !== 1 ? ` · ${c.speed}x` : "") + (c.color && (c.color.lut || c.color.input_lut || c.color.input_transform) ? " · LUT" : "")),
      el("div", { class: "handle l", "data-edge": "in" }), el("div", { class: "handle r", "data-edge": "out" }));
    attachDrag(block, "clips", i, start, dur);
    $("trackV1").append(block);
  });
  const timed = [["overlays", "trackV2", "overlay"], ["titles", "trackT", "title"], ["audio", "trackA", "audio"]];
  for (const [track, id, cls] of timed) {
    (state.project[track] || []).forEach((item, i) => {
      const start = Number(item.start || 0);
      let dur = Number(item.duration || 0);
      if (!dur) dur = track === "audio" ? Math.max(1, total - start) : 4;
      const block = el("div", { class: `clip ${cls}` + (isSel(track, i) ? " sel" : ""), style: `left:${start * z}px;width:${Math.max(6, dur * z - 1)}px`, title: clipLabel(item) },
        el("div", {}, clipLabel(item)), el("div", { class: "sub" }, `${round(start, 2)}s → ${round(start + dur, 2)}s`),
        el("div", { class: "handle r", "data-edge": "dur" }));
      attachDrag(block, track, i, start, dur);
      $(id).append(block);
    });
  }
  updateTransport();
}

function select(track, index) {
  state.sel = track ? { track, index } : null;
  state.tab = null;
  renderTimeline();
  renderInspector();
}

function attachDrag(block, track, index, start, dur) {
  block.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    ev.preventDefault();
    const edge = ev.target.dataset.edge;
    const x0 = ev.clientX;
    let moved = false;
    select(track, index);
    const item = state.project[track][index];
    const snapshot = clone(state.project);
    const fps = fpsValue();
    const media = item.source ? state.media[item.source] : null;
    const onMove = (e) => {
      const dx = (e.clientX - x0) / state.zoom;
      if (!moved && Math.abs(e.clientX - x0) < 4) return;
      moved = true;
      const blockEl = document.querySelector(`.clip.sel`);
      if (edge === "out" && track === "clips") {
        const speed = Number(item.speed || 1);
        const inP = Number(item.in || 0);
        let out = (snapshot.clips[index].out != null ? Number(snapshot.clips[index].out) : inP + dur * speed) + dx * speed;
        if (media && media.duration) out = Math.min(out, media.duration);
        out = Math.max(inP + 2 / fps, Math.round(out * fps) / fps);
        if (item.generator || (media && media.is_image)) { item.duration = round(Math.max(2 / fps, dur + dx), 3); delete item.out; }
        else { item.out = round(out, 4); delete item.duration; }
        if (blockEl) blockEl.style.width = Math.max(6, (dur + dx) * state.zoom) + "px";
      } else if (edge === "in" && track === "clips") {
        if (item.generator || (media && media.is_image)) return;
        const speed = Number(item.speed || 1);
        const out = snapshot.clips[index].out != null ? Number(snapshot.clips[index].out) : (Number(item.in || 0) + dur * speed);
        let inP = Number(snapshot.clips[index].in || 0) + dx * speed;
        inP = Math.min(out - 2 / fps, Math.max(0, Math.round(inP * fps) / fps));
        item.in = round(inP, 4);
        if (item.out == null) item.out = round(out, 4);
        delete item.duration;
        if (blockEl) { blockEl.style.width = Math.max(6, (dur - dx) * state.zoom) + "px"; }
      } else if (edge === "dur") {
        item.duration = round(Math.max(2 / fps, dur + dx), 3);
        if (blockEl) blockEl.style.width = Math.max(6, (dur + dx) * state.zoom) + "px";
      } else if (track === "clips") {
        if (blockEl) blockEl.style.transform = `translateX(${dx * state.zoom}px)`;
      } else {
        item.start = round(Math.max(0, Math.round((start + dx) * fps) / fps), 3);
        if (blockEl) blockEl.style.left = item.start * state.zoom + "px";
      }
    };
    const onUp = (e) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      if (!moved) return;
      if (!edge && track === "clips") {
        // reorder: find the clip whose middle the drop point passed
        const t = start + (e.clientX - x0) / state.zoom;
        const lays = state.layout ? state.layout.clips : [];
        let target = 0;
        lays.forEach((l, j) => { if (t > l.timeline_start + l.duration / 2) target = j + (j >= index ? 0 : 1); });
        target = Math.min(Math.max(0, target), state.project.clips.length - 1);
        if (target !== index) {
          const after = clone(state.project);
          state.project = snapshot; commit(); state.project = after;
          const [c] = state.project.clips.splice(index, 1);
          state.project.clips.splice(target, 0, c);
          if (target === 0) delete state.project.clips[0].transition;
          state.sel = { track, index: target };
        } else { renderTimeline(); return; }
      } else {
        const after = clone(state.project);
        state.project = snapshot; commit(); state.project = after;
      }
      changed(); renderInspector();
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  });
}

function rulerScrub() {
  const ruler = $("ruler");
  const at = (e) => (e.clientX - ruler.getBoundingClientRect().left) / state.zoom;
  ruler.addEventListener("pointerdown", (e) => {
    setPlayhead(at(e));
    const move = (ev) => setPlayhead(at(ev));
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); refreshFrame(); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  });
  for (const id of ["trackV1", "trackV2", "trackT", "trackA"]) {
    $(id).addEventListener("pointerdown", (e) => {
      if (e.target === $(id)) { select(null); setPlayhead(at(e)); }
    });
  }
}

/* -------------------------------------------------------------- editing */

async function mediaInfo(path) {
  if (!state.media[path]) state.media[path] = await api("/api/probe", { path });
  return state.media[path];
}

async function addMedia(kind, path) {
  path = path || state.browserSel;
  if (!path) { toast("Select a file in the Media panel first"); return; }
  let info;
  try { info = await mediaInfo(path); } catch (e) { toast(e.message, true); return; }
  commit();
  const p = state.project;
  if (kind === "clips") {
    if (!info.has_video) { state.history.pop(); toast("That file has no video. Use + Audio.", true); return; }
    const clip = { source: path };
    if (info.is_image) clip.duration = 5;
    if (!p.clips.length && info.width && info.height && !info.is_image) {
      p.settings.width = info.width - (info.width % 2);
      p.settings.height = info.height - (info.height % 2);
      if (info.fps) p.settings.fps = FPS_CHOICES.find((f) => Math.abs(Number(f) - info.fps) < 0.01) || String(round(info.fps, 3));
      toast(`Sequence set to ${p.settings.width}x${p.settings.height} @ ${p.settings.fps} to match the first clip`);
    }
    p.clips.push(clip);
    state.sel = { track: "clips", index: p.clips.length - 1 };
  } else if (kind === "overlays") {
    if (!info.has_video) { state.history.pop(); toast("That file has no video.", true); return; }
    p.overlays = p.overlays || [];
    p.overlays.push({ source: path, start: round(state.playhead, 3), duration: info.is_image ? 5 : round(Math.min(info.duration || 5, 10), 3), width: 0.35, x: "right", y: "top" });
    state.sel = { track: "overlays", index: p.overlays.length - 1 };
  } else if (kind === "audio") {
    if (!info.has_audio) { state.history.pop(); toast("That file has no audio.", true); return; }
    p.audio = p.audio || [];
    p.audio.push({ source: path, start: round(state.playhead, 3), volume_db: -6, fade_in: 1, fade_out: 2, duck: true });
    state.sel = { track: "audio", index: p.audio.length - 1 };
  }
  changed(); renderInspector();
}

function addTitle() {
  commit();
  state.project.titles = state.project.titles || [];
  state.project.titles.push({ text: "Title", start: round(state.playhead, 3), duration: 4, position: "lower_third", box: true });
  state.sel = { track: "titles", index: state.project.titles.length - 1 };
  changed(); renderInspector();
}

function splitAtPlayhead() {
  if (!state.layout) return;
  const t = state.playhead;
  const i = state.layout.clips.findIndex((l) => t > l.timeline_start + 1e-6 && t < l.timeline_start + l.duration - 1e-6);
  if (i < 0) { toast("Put the playhead over a clip on V1 to split it"); return; }
  const lay = state.layout.clips[i];
  const c = state.project.clips[i];
  const media = c.source ? state.media[c.source] : null;
  commit();
  const a = clone(c), b = clone(c);
  const local = t - lay.timeline_start;
  if (c.generator || (media && media.is_image)) {
    a.duration = round(local, 4); b.duration = round(lay.duration - local, 4);
  } else {
    const speed = Number(c.speed || 1);
    const inP = Number(c.in || 0);
    const cut = round(inP + local * speed, 4);
    a.out = cut; delete a.duration;
    b.in = cut; b.out = round(inP + lay.src_len, 4); delete b.duration;
  }
  delete a.fade_out; delete b.fade_in; delete b.transition;
  if (a.audio) delete a.audio.fade_out;
  if (b.audio) delete b.audio.fade_in;
  state.project.clips.splice(i, 1, a, b);
  state.sel = { track: "clips", index: i + 1 };
  changed(); renderInspector();
}

function deleteSelected() {
  if (!state.sel) return;
  commit();
  const arr = state.project[state.sel.track];
  arr.splice(state.sel.index, 1);
  if (state.sel.track === "clips" && arr.length) delete arr[0].transition;
  state.sel = null;
  changed(); renderInspector();
}

function duplicateSelected() {
  if (!state.sel) return;
  commit();
  const arr = state.project[state.sel.track];
  const copy = clone(arr[state.sel.index]);
  if (state.sel.track !== "clips") copy.start = round(Number(copy.start || 0) + Number(copy.duration || 1), 3);
  arr.splice(state.sel.index + 1, 0, copy);
  state.sel.index += 1;
  changed(); renderInspector();
}

function moveSelected(delta) {
  if (!state.sel || state.sel.track !== "clips") return;
  const arr = state.project.clips;
  const j = state.sel.index + delta;
  if (j < 0 || j >= arr.length) return;
  commit();
  [arr[state.sel.index], arr[j]] = [arr[j], arr[state.sel.index]];
  delete arr[0].transition;
  state.sel.index = j;
  changed(); renderInspector();
}

/* ------------------------------------------------------------- inspector */

// Field spec: [path, label, type, opts]. Types: num, range, text, area, check, select, file, color, pair
const COLOR_FIELDS = () => {
  const tf = (state.info && state.info.transforms) || [];
  const groups = [];
  if (tf.length) {
    groups.push(["Color management", [
      ["color.input_transform", "Camera / log", "select", { options: [["", "None (display-referred)"], ...tf.map((t) => (typeof t === "string" ? [t, t] : [t.name, t.label || t.name]))] }],
    ]]);
  }
  groups.push(["Input", [
    ["color.input_lut", "Input LUT", "file", { filter: "lut", hint: "Technical LUT, e.g. Log → Rec.709" }],
  ]]);
  groups.push(["Basic correction", [
    ["color.temperature", "Temperature", "range", { min: 2000, max: 12000, step: 50, def: 6500, unit: "K" }],
    ["color.tint", "Tint", "range", { min: -100, max: 100, step: 1, def: 0 }],
    ["color.exposure", "Exposure", "range", { min: -4, max: 4, step: 0.05, def: 0, unit: "stops" }],
    ["color.contrast", "Contrast", "range", { min: -100, max: 100, step: 1, def: 0 }],
    ["color.highlights", "Highlights", "range", { min: -100, max: 100, step: 1, def: 0 }],
    ["color.shadows", "Shadows", "range", { min: -100, max: 100, step: 1, def: 0 }],
    ["color.whites", "Whites", "range", { min: -100, max: 100, step: 1, def: 0 }],
    ["color.blacks", "Blacks", "range", { min: -100, max: 100, step: 1, def: 0 }],
    ["color.saturation", "Saturation", "range", { min: 0, max: 200, step: 1, def: 100 }],
    ["color.vibrance", "Vibrance", "range", { min: -100, max: 100, step: 1, def: 0 }],
  ]]);
  groups.push(["Creative", [
    ["color.lut", "Look LUT", "file", { filter: "lut" }],
    ["color.lut_intensity", "Intensity", "range", { min: 0, max: 100, step: 1, def: 100 }],
  ]]);
  groups.push(["Color wheels (R, G, B offsets)", [
    ["color.shadow_tint", "Shadows", "rgb", {}],
    ["color.midtone_tint", "Midtones", "rgb", {}],
    ["color.highlight_tint", "Highlights", "rgb", {}],
  ]]);
  groups.push(["Curves", [
    ["color.curves.master", "Master", "text", { placeholder: "0/0 0.5/0.55 1/1" }],
    ["color.curves.red", "Red", "text", {}],
    ["color.curves.green", "Green", "text", {}],
    ["color.curves.blue", "Blue", "text", {}],
    ["color.output_lut", "Output LUT", "file", { filter: "lut" }],
  ]]);
  return groups;
};

const INSPECTORS = {
  clips: {
    title: (c) => "Clip · " + clipLabel(c),
    tabs: {
      Clip: () => [
        ["Source", [
          ["source", "File", "file", { filter: "media" }],
          ["in", "In (s)", "num", { step: 0.01 }],
          ["out", "Out (s)", "num", { step: 0.01 }],
          ["duration", "Duration (s)", "num", { step: 0.01, hint: "For stills, generators, or instead of Out" }],
          ["speed", "Speed", "range", { min: 0.1, max: 4, step: 0.05, def: 1, unit: "x" }],
          ["reverse", "Reverse", "check", {}],
          ["interpolation", "Retiming", "select", { options: [["", "Frame sampling"], ["frame_blending", "Frame blending"], ["optical_flow", "Optical flow"]] }],
        ]],
        ["Transition in", [
          ["transition.type", "Type", "select", { options: TRANSITIONS.map((t) => [t, t ? t.replace(/_/g, " ") : "Cut (none)"]) }],
          ["transition.duration", "Duration (s)", "num", { step: 0.05, def: 1 }],
          ["fade_in", "Fade from black", "num", { step: 0.05 }],
          ["fade_out", "Fade to black", "num", { step: 0.05 }],
        ]],
        ["Motion", [
          ["fit", "Scale to frame", "select", { options: [["", "Fit (letterbox)"], ["fill", "Fill (crop)"], ["stretch", "Stretch"]] }],
          ["zoom", "Scale %", "range", { min: 10, max: 400, step: 1, def: 100 }],
          ["position", "Position X, Y", "pair", {}],
          ["rotation", "Rotation °", "range", { min: -180, max: 180, step: 0.5, def: 0 }],
          ["flip", "Flip", "select", { options: [["", "None"], ["h", "Horizontal"], ["v", "Vertical"], ["hv", "Both"]] }],
        ]],
      ],
      Color: COLOR_FIELDS,
      Effects: () => [
        ["Effects", [
          ["effects.denoise", "Denoise", "range", { min: 0, max: 100, step: 1, def: 0 }],
          ["effects.sharpen", "Sharpen", "range", { min: 0, max: 100, step: 1, def: 0 }],
          ["effects.blur", "Blur", "range", { min: 0, max: 50, step: 0.5, def: 0 }],
          ["effects.stabilize", "Stabilize", "check", { hint: "Warp-style stabilisation (analyses the clip first)" }],
        ]],
      ],
      Audio: () => [
        ["Clip audio", [
          ["audio.volume_db", "Volume dB", "range", { min: -40, max: 20, step: 0.5, def: 0 }],
          ["audio.mute", "Mute", "check", {}],
          ["audio.fade_in", "Fade in (s)", "num", { step: 0.05 }],
          ["audio.fade_out", "Fade out (s)", "num", { step: 0.05 }],
        ]],
      ],
    },
  },
  overlays: {
    title: (o) => "Overlay · " + clipLabel(o),
    tabs: {
      Overlay: () => [
        ["Source", [
          ["source", "File", "file", { filter: "media" }],
          ["start", "Start (s)", "num", { step: 0.01 }],
          ["in", "Source in (s)", "num", { step: 0.01 }],
          ["duration", "Duration (s)", "num", { step: 0.01 }],
        ]],
        ["Placement", [
          ["width", "Width", "range", { min: 0.05, max: 1, step: 0.01, def: 1, hint: "Fraction of the frame width" }],
          ["x", "X", "text", { placeholder: "center / left / right / pixels" }],
          ["y", "Y", "text", { placeholder: "center / top / bottom / pixels" }],
          ["opacity", "Opacity", "range", { min: 0, max: 1, step: 0.01, def: 1 }],
          ["fade_in", "Fade in (s)", "num", { step: 0.05 }],
          ["fade_out", "Fade out (s)", "num", { step: 0.05 }],
        ]],
        ["Keying", [
          ["chroma_key.color", "Key colour", "color", {}],
          ["chroma_key.similarity", "Similarity", "range", { min: 0.01, max: 0.5, step: 0.01, def: 0.12 }],
          ["chroma_key.blend", "Edge blend", "range", { min: 0, max: 0.3, step: 0.01, def: 0.04 }],
        ]],
      ],
      Color: COLOR_FIELDS,
    },
  },
  titles: {
    title: (t) => "Title",
    tabs: {
      Text: () => [
        ["Text", [
          ["text", "Text", "area", {}],
          ["start", "Start (s)", "num", { step: 0.01 }],
          ["duration", "Duration (s)", "num", { step: 0.01 }],
          ["position", "Position", "select", { options: [["center", "Center"], ["lower_third", "Lower third"], ["top", "Top"], ["bottom", "Bottom"]] }],
          ["size", "Size px", "num", { step: 1 }],
          ["color", "Colour", "color", {}],
          ["box", "Background box", "check", {}],
          ["box_color", "Box colour", "text", { placeholder: "black@0.55" }],
          ["fade", "Fade (s)", "num", { step: 0.05 }],
          ["font", "Font file", "file", { filter: "any" }],
        ]],
      ],
    },
  },
  audio: {
    title: (a) => "Audio · " + clipLabel(a),
    tabs: {
      Audio: () => [
        ["Track", [
          ["source", "File", "file", { filter: "media" }],
          ["start", "Start (s)", "num", { step: 0.01 }],
          ["in", "Source in (s)", "num", { step: 0.01 }],
          ["duration", "Duration (s)", "num", { step: 0.01 }],
          ["volume_db", "Volume dB", "range", { min: -40, max: 12, step: 0.5, def: 0 }],
          ["fade_in", "Fade in (s)", "num", { step: 0.1 }],
          ["fade_out", "Fade out (s)", "num", { step: 0.1 }],
          ["duck", "Auto-duck under dialogue", "check", {}],
          ["loop", "Loop to fill", "check", {}],
        ]],
      ],
    },
  },
  sequence: {
    title: () => "Sequence",
    tabs: {
      Sequence: () => [
        ["Sequence settings", [
          ["settings.title", "Title", "text", {}],
          ["settings.size", "Frame size", "size", {}],
          ["settings.fps", "Frame rate", "select", { options: FPS_CHOICES.map((f) => [f, f + " fps"]) }],
          ["settings.start_timecode", "Start timecode", "text", { placeholder: "01:00:00:00" }],
          ["settings.color_matrix", "Colour matrix", "select", { options: [["", "Rec.709"], ["bt2020", "Rec.2020"]] }],
          ["settings.audio_channels", "Audio channels", "select", { options: [["", "Stereo"], ["1", "Mono"], ["6", "5.1"]] }],
        ]],
        ["Master", [
          ["master.loudness.target", "Loudness", "select", { options: [["", "Off"], ["-14", "-14 LUFS (YouTube, Spotify)"], ["-16", "-16 LUFS (Apple, podcasts)"], ["-23", "-23 LUFS (EBU R128 broadcast)"], ["-24", "-24 LUFS (ATSC A/85)"]] }],
          ["master.loudness.true_peak", "True peak dBTP", "num", { step: 0.5, placeholder: "-1" }],
          ["master.fade_in", "Fade in (s)", "num", { step: 0.1 }],
          ["master.fade_out", "Fade out (s)", "num", { step: 0.1 }],
          ["master.volume_db", "Master gain dB", "num", { step: 0.5 }],
        ]],
      ],
      "Master color": () => COLOR_FIELDS().filter((g) => g[0] !== "Color wheels (R, G, B offsets)").map(([name, fields]) => [name, fields.map((f) => ["master." + f[0], f[1], f[2], f[3]])]),
    },
  },
};

function currentTarget() {
  if (!state.sel) return { kind: "sequence", obj: state.project };
  return { kind: state.sel.track, obj: state.project[state.sel.track][state.sel.index] };
}

function renderInspector() {
  const { kind, obj } = currentTarget();
  if (!obj) { state.sel = null; return renderInspector(); }
  const spec = INSPECTORS[kind];
  $("inspectorTitle").textContent = spec.title(obj);
  const tabNames = [...Object.keys(spec.tabs), "JSON"];
  if (!tabNames.includes(state.tab)) state.tab = tabNames[0];
  const tabs = $("tabs");
  tabs.innerHTML = "";
  for (const name of tabNames) {
    tabs.append(el("button", { class: name === state.tab ? "on" : "", onclick: () => { state.tab = name; renderInspector(); } }, name));
  }
  const body = $("inspector");
  body.innerHTML = "";
  if (state.tab === "JSON") return renderJson(body, kind, obj);
  for (const [groupName, fields] of spec.tabs[state.tab]()) {
    const g = el("div", { class: "group" }, el("h3", {}, groupName));
    for (const f of fields) g.append(renderField(obj, kind, ...f));
    body.append(g);
  }
  if (kind === "clips" && state.tab === "Color") body.append(colorTools());
}

function colorTools() {
  const wrap = el("div", { class: "group" }, el("h3", {}, "Tools"));
  wrap.append(el("div", { class: "field wide" }, el("label", {}, ""),
    el("button", { onclick: () => { commit(); delete currentTarget().obj.color; changed(); renderInspector(); } }, "Reset all colour")));
  return wrap;
}

function renderJson(body, kind, obj) {
  const ta = el("textarea", { rows: 26, spellcheck: "false" });
  ta.value = JSON.stringify(obj, null, 2);
  const apply = el("button", { class: "primary", onclick: () => {
    let val;
    try { val = JSON.parse(ta.value); } catch (e) { toast("Invalid JSON: " + e.message, true); return; }
    commit();
    if (kind === "sequence") state.project = val;
    else state.project[state.sel.track][state.sel.index] = val;
    changed(); renderInspector(); toast("Applied");
  } }, "Apply");
  body.append(el("p", { class: "note" }, "Every setting from the docs can be edited here, including ones without a control in the panels (keyframes, masks, audio effects, captions, ...)."), ta, el("div", { class: "field wide" }, el("span"), apply));
}

function renderField(obj, kind, path, label, type, opts = {}) {
  const cur = getPath(obj, path);
  const row = el("div", { class: "field" + (["file", "area", "text", "select", "check", "rgb", "pair", "size", "color"].includes(type) ? " wide" : "") });
  row.append(el("label", { title: opts.hint || label }, label));
  const set = (v, isCommit = true) => {
    if (isCommit) commit();
    setPath(obj, path, v);
    if (path === "transition.type" && v && !getPath(obj, "transition.duration")) setPath(obj, "transition.duration", 1);
    if (path === "transition.type" && !v) delete obj.transition;
    changed();
    if (isCommit && (path.startsWith("source") || path === "text")) renderTimeline();
  };
  if (type === "range") {
    const def = opts.def ?? 0;
    const val = cur ?? def;
    const r = el("input", { type: "range", min: opts.min, max: opts.max, step: opts.step, value: val });
    const n = el("input", { type: "number", class: "num", min: opts.min, max: opts.max, step: opts.step, value: val, title: opts.unit || "" });
    let committed = false;
    r.addEventListener("input", () => {
      if (!committed) { commit(); committed = true; }
      n.value = r.value;
      setPath(obj, path, Number(r.value) === def ? undefined : Number(r.value));
      changed();
    });
    r.addEventListener("change", () => { committed = false; });
    r.addEventListener("dblclick", () => { r.value = def; n.value = def; set(undefined); });
    n.addEventListener("change", () => { r.value = n.value; set(n.value === "" || Number(n.value) === def ? undefined : Number(n.value)); });
    row.append(r, n);
  } else if (type === "num") {
    const n = el("input", { type: "number", step: opts.step || "any", value: cur ?? "", placeholder: opts.placeholder ?? (opts.def ?? "") });
    n.addEventListener("change", () => set(n.value === "" ? undefined : Number(n.value)));
    row.append(n, el("span"));
  } else if (type === "text") {
    const t = el("input", { type: "text", value: cur ?? "", placeholder: opts.placeholder || "" });
    t.addEventListener("change", () => {
      let v = t.value.trim();
      if (v !== "" && /^-?\d+(\.\d+)?$/.test(v) && !path.includes("timecode") && !path.includes("title") && !path.includes("curves")) v = Number(v);
      set(v === "" ? undefined : v);
    });
    row.append(t);
  } else if (type === "area") {
    const t = el("textarea", { rows: 3 });
    t.value = cur ?? "";
    t.addEventListener("change", () => set(t.value));
    row.append(t);
  } else if (type === "check") {
    const c = el("input", { type: "checkbox" });
    c.checked = !!cur;
    c.addEventListener("change", () => set(c.checked ? true : undefined));
    row.append(el("div", {}, c, opts.hint ? el("span", { class: "note" }, " " + opts.hint) : null));
  } else if (type === "select") {
    const s = el("select");
    for (const [v, text] of opts.options) s.append(el("option", { value: v, selected: String(cur ?? "") === String(v) }, text));
    s.addEventListener("change", () => {
      let v = s.value;
      if (v !== "" && /^-?\d+(\.\d+)?$/.test(v) && !path.endsWith("fps")) v = Number(v);
      set(v === "" ? undefined : v);
    });
    row.append(s);
  } else if (type === "file") {
    const t = el("input", { type: "text", value: cur ?? "", placeholder: opts.hint || "" });
    t.addEventListener("change", () => set(t.value.trim() || undefined));
    const b = el("button", { onclick: () => pickFile({ mode: "open", filter: opts.filter, start: cur ? dirname(cur) : null, title: label }).then((p) => { if (p) { t.value = p; set(p); } }) }, "…");
    const x = el("button", { title: "Clear", onclick: () => { t.value = ""; set(undefined); } }, "✕");
    row.append(el("div", { class: "file-pick" }, t, b, x));
  } else if (type === "color") {
    const v = typeof cur === "string" && /^#[0-9a-f]{6}$/i.test(cur) ? cur : (path.includes("chroma") ? "#00ff00" : "#ffffff");
    const c = el("input", { type: "color", value: v });
    const clear = el("button", { onclick: () => set(undefined) }, "✕");
    c.addEventListener("change", () => set(c.value));
    row.append(el("div", { class: "file-pick" }, c, el("span", { class: "note" }, cur ? String(cur) : "default"), clear));
  } else if (type === "rgb" || type === "pair") {
    const n = type === "rgb" ? 3 : 2;
    const vals = Array.isArray(cur) ? cur : new Array(n).fill(0);
    const wrap = el("div", { class: "file-pick" });
    const inputs = [];
    for (let i = 0; i < n; i++) {
      const inp = el("input", { type: "number", step: type === "rgb" ? 0.01 : 1, value: vals[i], style: "width:64px" });
      inp.addEventListener("change", () => {
        const arr = inputs.map((x) => Number(x.value) || 0);
        set(arr.every((x) => x === 0) ? undefined : arr);
      });
      inputs.push(inp); wrap.append(inp);
    }
    row.append(wrap);
  } else if (type === "size") {
    const s = obj.settings || {};
    const curSize = `${s.width || 1920}x${s.height || 1080}`;
    const sel = el("select");
    let found = false;
    for (const [v, text] of SIZES) { if (v === curSize) found = true; sel.append(el("option", { value: v, selected: v === curSize }, `${text} (${v})`)); }
    if (!found) sel.append(el("option", { value: curSize, selected: true }, `Custom (${curSize})`));
    sel.append(el("option", { value: "custom" }, "Custom…"));
    sel.addEventListener("change", () => {
      let v = sel.value;
      if (v === "custom") { v = prompt("Frame size (WIDTHxHEIGHT, even numbers)", curSize); if (!v) { sel.value = curSize; return; } }
      const m = /^(\d+)\s*[x×]\s*(\d+)$/.exec(v);
      if (!m) { toast("Use WIDTHxHEIGHT", true); return; }
      commit();
      obj.settings = obj.settings || {};
      obj.settings.width = Number(m[1]) - (Number(m[1]) % 2);
      obj.settings.height = Number(m[2]) - (Number(m[2]) % 2);
      changed(); renderInspector();
    });
    row.append(sel);
  }
  return row;
}

/* ------------------------------------------------------------ file panes */

const FILTERS = {
  media: ["video", "image", "audio"], lut: ["lut"], project: ["project"], subtitle: ["subtitle"],
  any: ["video", "image", "audio", "lut", "project", "subtitle"],
};
const ICONS = { video: "🎞", image: "🖼", audio: "🎵", lut: "🎨", project: "📄", subtitle: "💬" };

function fmtSize(n) {
  if (n == null) return "";
  if (n > 1e9) return (n / 1e9).toFixed(1) + " GB";
  if (n > 1e6) return (n / 1e6).toFixed(1) + " MB";
  return Math.max(1, Math.round(n / 1e3)) + " KB";
}

function renderCrumbs(container, path, sep, onGo) {
  container.innerHTML = "";
  const parts = path.split(/[\\/]/).filter((p, i) => p !== "" || i === 0);
  let acc = "";
  parts.forEach((p, i) => {
    acc = i === 0 ? (p === "" ? "/" : p + sep) : (acc.endsWith(sep) ? acc + p : acc + sep + p);
    const target = acc;
    container.append(el("button", { onclick: () => onGo(target) }, p === "" ? "/" : p));
  });
}

async function browse(path) {
  let res;
  try { res = await api("/api/fs?path=" + encodeURIComponent(path || "")); } catch (e) { toast(e.message, true); return; }
  state.browserPath = res.path;
  const drives = $("drives");
  drives.innerHTML = "";
  for (const d of res.drives) drives.append(el("option", { value: d, selected: res.path.startsWith(d) }, d));
  renderCrumbs($("crumbs"), res.path, res.sep, browse);
  const list = $("fileList");
  list.innerHTML = "";
  if (res.parent) list.append(el("div", { class: "file", ondblclick: () => browse(res.parent) }, el("span", { class: "ico" }, "↰"), el("span", { class: "name" }, "..")));
  for (const e of res.entries) {
    if (!e.dir && !FILTERS.any.includes(e.kind)) continue;
    const row = el("div", { class: "file" + (state.browserSel === e.path ? " sel" : ""), title: e.path },
      el("span", { class: "ico" }, e.dir ? "📁" : ICONS[e.kind] || "·"), el("span", { class: "name" }, e.name), el("span", { class: "size" }, e.dir ? "" : fmtSize(e.size)));
    row.addEventListener("click", () => {
      if (e.dir) return;
      state.browserSel = e.path;
      list.querySelectorAll(".file.sel").forEach((x) => x.classList.remove("sel"));
      row.classList.add("sel");
      if (["video", "image", "audio"].includes(e.kind)) {
        mediaInfo(e.path).then((info) => { $("mediaInfo").textContent = info.summary; }).catch((err) => { $("mediaInfo").textContent = err.message; });
      } else if (e.kind === "lut") {
        api("/api/lut", { path: e.path }).then((r) => { $("mediaInfo").textContent = r.description + "\n\nDouble-click to use as the selected clip's look LUT."; }).catch((err) => { $("mediaInfo").textContent = err.message; });
      } else {
        $("mediaInfo").textContent = e.kind === "project" ? "Double-click to open this project." : "";
      }
    });
    row.addEventListener("dblclick", () => {
      if (e.dir) return browse(e.path);
      if (e.kind === "video" || e.kind === "image") return addMedia("clips", e.path);
      if (e.kind === "audio") return addMedia("audio", e.path);
      if (e.kind === "project") return openProject(e.path);
      if (e.kind === "lut") {
        const t = currentTarget();
        if (t.kind !== "clips" && t.kind !== "overlays") { toast("Select a clip first, then double-click a LUT"); return; }
        commit(); setPath(t.obj, "color.lut", e.path); changed(); state.tab = "Color"; renderInspector();
      }
    });
    list.append(row);
  }
}

function pickFile({ mode, filter = "any", start, title, defaultName }) {
  return new Promise((resolve) => {
    const modal = $("fileModal");
    $("fileModalTitle").textContent = title || (mode === "save" ? "Save as" : "Open");
    $("fmNameRow").hidden = mode !== "save";
    $("fmName").value = defaultName || "";
    let current = null, chosen = null;
    const go = async (path) => {
      let res;
      try { res = await api("/api/fs?path=" + encodeURIComponent(path || "")); } catch (e) { toast(e.message, true); return; }
      current = res.path;
      renderCrumbs($("fmCrumbs"), res.path, res.sep, go);
      const list = $("fmList");
      list.innerHTML = "";
      if (res.parent) list.append(el("div", { class: "file", ondblclick: () => go(res.parent) }, el("span", { class: "ico" }, "↰"), el("span", { class: "name" }, "..")));
      if (res.drives.length > 1) for (const d of res.drives) list.append(el("div", { class: "file", ondblclick: () => go(d) }, el("span", { class: "ico" }, "💽"), el("span", { class: "name" }, d)));
      for (const e of res.entries) {
        if (!e.dir && !(FILTERS[filter] || FILTERS.any).includes(e.kind)) continue;
        const row = el("div", { class: "file" }, el("span", { class: "ico" }, e.dir ? "📁" : ICONS[e.kind] || "·"), el("span", { class: "name" }, e.name));
        row.addEventListener("click", () => {
          if (e.dir) return;
          chosen = e.path;
          list.querySelectorAll(".file.sel").forEach((x) => x.classList.remove("sel"));
          row.classList.add("sel");
          if (mode === "save") $("fmName").value = e.name;
        });
        row.addEventListener("dblclick", () => { if (e.dir) go(e.path); else { chosen = e.path; finish(); } });
        list.append(row);
      }
    };
    const finish = () => {
      let result = chosen;
      if (mode === "save") {
        const name = $("fmName").value.trim();
        if (!name) { toast("Type a file name"); return; }
        result = /^([a-zA-Z]:)?[\\/]/.test(name) ? name : current + (current.endsWith("/") || current.endsWith("\\") ? "" : (current.includes("\\") ? "\\" : "/")) + name;
      }
      if (!result) { toast("Choose a file"); return; }
      close(result);
    };
    const close = (value) => { modal.hidden = true; $("fmOk").onclick = null; modal.onclose = null; resolve(value); };
    $("fmOk").onclick = finish;
    modal.onclose = () => close(null);
    modal.hidden = false;
    go(start || state.browserPath);
  });
}

/* ----------------------------------------------------- open / save / new */

async function openProject(path) {
  if (state.dirty && !confirm("Discard unsaved changes?")) return;
  if (!path) path = await pickFile({ mode: "open", filter: "project", title: "Open project" });
  if (!path) return;
  try {
    const res = await api("/api/open", { path });
    loadProject(res.project, res.path, res.base_dir);
    toast("Opened " + basename(res.path));
  } catch (e) { toast(e.message, true); }
}

function loadProject(project, path, baseDir) {
  for (const k of ["clips", "overlays", "titles", "audio"]) project[k] = project[k] || [];
  project.settings = project.settings || {};
  project.master = project.master || {};
  state.project = project;
  state.path = path;
  state.baseDir = baseDir || (state.info && state.info.home);
  state.history = []; state.future = [];
  state.dirty = false; state.sel = null; state.playhead = 0;
  updateTitle(); renderInspector(); scheduleLayout();
  for (const c of project.clips) if (c.source) mediaInfo(resolveMediaPath(c.source)).catch(() => {});
}

function resolveMediaPath(p) {
  if (/^([a-zA-Z]:)?[\\/]/.test(p) || !state.baseDir) return p;
  return state.baseDir + (state.baseDir.includes("\\") ? "\\" : "/") + p;
}

async function saveProject(as) {
  let path = state.path;
  if (as || !path) {
    path = await pickFile({ mode: "save", filter: "project", title: "Save project", defaultName: ((state.project.settings || {}).title || "project").replace(/[^\w\- ]+/g, "") + ".json" });
    if (!path) return;
  }
  try {
    const res = await api("/api/save", { path, project: state.project });
    state.path = res.path; state.baseDir = res.base_dir; state.dirty = false;
    updateTitle(); toast("Saved " + basename(res.path));
  } catch (e) { toast(e.message, true); }
}

/* --------------------------------------------------------------- export */

function openExport() {
  if (!state.project.clips.length) { toast("Add a clip to the timeline first"); return; }
  const sel = $("exportPreset");
  sel.innerHTML = "";
  const cats = {};
  for (const p of state.info.presets) (cats[p.category] = cats[p.category] || []).push(p);
  const current = (state.project.export || {}).preset || "prores_422hq";
  for (const [cat, list] of Object.entries(cats)) {
    const og = el("optgroup", { label: cat });
    for (const p of list) og.append(el("option", { value: p.name, selected: p.name === current }, `${p.name} — ${p.description}`));
    sel.append(og);
  }
  const updateHint = () => {
    const p = state.info.presets.find((x) => x.name === sel.value);
    $("presetHint").textContent = p.sequence ? "Writes a folder of numbered frames plus a WAV of the mix." : p.description;
    const out = $("exportPath");
    const base = out.value || ((state.project.export || {}).output) || defaultOutput();
    out.value = p.sequence ? base.replace(/\.[^.\\/]+$/, "") : base.replace(/(\.[^.\\/]+)?$/, p.ext);
  };
  sel.onchange = updateHint;
  $("exportPath").value = (state.project.export || {}).output || defaultOutput();
  updateHint();
  $("exportModal").hidden = false;
}

function defaultOutput() {
  const dir = state.path ? dirname(state.path) : (state.browserPath || state.info.home);
  const sep = dir.includes("\\") ? "\\" : "/";
  return dir + sep + ((state.project.settings || {}).title || "export").replace(/[^\w\- ]+/g, "").replace(/ /g, "_") + ".mov";
}

async function startExport() {
  const preset = $("exportPreset").value;
  const output = $("exportPath").value.trim();
  if (!output) { toast("Choose an output file", true); return; }
  commit();
  state.project.export = { preset, output };
  try {
    await api("/api/render", { project: state.project, base_dir: state.baseDir, preset, output, label: basename(output) });
    $("exportModal").hidden = true;
    $("queue").hidden = false;
    pollJobs();
  } catch (e) { toast(e.message, true); }
}

async function playPreview() {
  if (!state.project.clips.length) return;
  try {
    const job = await api("/api/render", { project: state.project, base_dir: state.baseDir, preset: "h264_web", draft: true, label: "Preview" });
    state.previewJob = job.id;
    toast("Rendering preview…");
    $("queue").hidden = false;
    pollJobs();
  } catch (e) { toast(e.message, true); }
}

function showVideo(path) {
  const v = $("previewVideo");
  v.src = `/media?t=${encodeURIComponent(TOKEN)}&path=${encodeURIComponent(path)}&v=${Date.now()}`;
  v.hidden = false; $("frame").hidden = true; $("showFrameBtn").hidden = false;
  v.currentTime = state.playhead;
  v.play().catch(() => {});
}

function showFrameView() {
  const v = $("previewVideo");
  if (!v.hidden) setPlayhead(v.currentTime || state.playhead);
  v.pause(); v.hidden = true; $("frame").hidden = false; $("showFrameBtn").hidden = true;
}

let pollTimer = null;
async function pollJobs() {
  clearTimeout(pollTimer);
  try {
    const res = await api("/api/jobs");
    state.jobs = res.jobs;
    renderJobs();
    const active = res.jobs.some((j) => ["queued", "analysing", "rendering"].includes(j.status));
    const pj = res.jobs.find((j) => j.id === state.previewJob);
    if (pj && pj.status === "done") { state.previewJob = null; showVideo(pj.outputs[0]); }
    if (pj && pj.status === "failed") { state.previewJob = null; toast("Preview failed: " + pj.error, true); }
    if (active) pollTimer = setTimeout(pollJobs, 800);
  } catch (e) { pollTimer = setTimeout(pollJobs, 3000); }
}

function renderJobs() {
  const box = $("jobs");
  box.innerHTML = "";
  const active = state.jobs.filter((j) => ["queued", "analysing", "rendering"].includes(j.status)).length;
  $("queueBtn").textContent = active ? `Queue (${active})` : "Queue";
  if (!state.jobs.length) { box.append(el("div", { class: "job meta" }, "Nothing rendered yet.")); return; }
  for (const j of [...state.jobs].reverse()) {
    const row = el("div", { class: "job " + j.status },
      el("div", { class: "row1" }, el("strong", {}, j.label), el("span", { class: "meta" }, `${j.draft ? "draft preview" : (j.preset || "")} · ${j.status}`)),
      el("div", { class: "bar" }, el("div", { style: `width:${j.status === "done" ? 100 : j.progress}%` })));
    if (j.error) row.append(el("div", { class: "err" }, j.error));
    const btns = el("div", { class: "row1" });
    if (["queued", "analysing", "rendering"].includes(j.status)) btns.append(el("button", { onclick: () => api("/api/cancel", { id: j.id }).then(pollJobs) }, "Cancel"));
    if (j.status === "done" && j.outputs.length) {
      btns.append(el("span", { class: "meta", title: j.outputs[0] }, j.outputs[0]));
      btns.append(el("button", { onclick: () => api("/api/reveal", { path: j.outputs[0] }) }, "Show in folder"));
      if (/\.(mp4|webm|m4v)$/i.test(j.outputs[0])) btns.append(el("button", { onclick: () => showVideo(j.outputs[0]) }, "Play"));
    }
    row.append(btns);
    box.append(row);
  }
}

/* -------------------------------------------------------------- startup */

const ACTIONS = {
  new: () => { if (state.dirty && !confirm("Discard unsaved changes?")) return; loadProject(newProject(), null, state.browserPath || state.info.home); },
  open: () => openProject(),
  save: () => saveProject(false),
  saveas: () => saveProject(true),
  undo, redo,
  export: openExport,
  queue: () => { $("queue").hidden = !$("queue").hidden; if (!$("queue").hidden) pollJobs(); },
  clearQueue: () => api("/api/clear", {}).then(pollJobs),
  addV1: () => addMedia("clips"),
  addV2: () => addMedia("overlays"),
  addA: () => addMedia("audio"),
  addTitle,
  split: splitAtPlayhead,
  delete: deleteSelected,
  duplicate: duplicateSelected,
  moveLeft: () => moveSelected(-1),
  moveRight: () => moveSelected(1),
  home: () => setPlayhead(0, true),
  end: () => setPlayhead(1e9, true),
  back: () => setPlayhead(state.playhead - 1 / fpsValue(), true),
  fwd: () => setPlayhead(state.playhead + 1 / fpsValue(), true),
  playPreview,
  showFrame: showFrameView,
  closeModal: () => { for (const m of document.querySelectorAll(".modal")) { if (!m.hidden) { m.hidden = true; if (m.onclose) m.onclose(); } } },
  browseExport: async () => {
    const cur = $("exportPath").value;
    const p = await pickFile({ mode: "save", filter: "media", title: "Export to", start: cur ? dirname(cur) : null, defaultName: basename(cur) });
    if (p) $("exportPath").value = p;
  },
  startExport,
};

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-action]");
  if (b && ACTIONS[b.dataset.action]) ACTIONS[b.dataset.action]();
});

document.addEventListener("keydown", (e) => {
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
  const mod = e.ctrlKey || e.metaKey;
  if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); saveProject(e.shiftKey); return; }
  if (mod && e.key.toLowerCase() === "o") { e.preventDefault(); openProject(); return; }
  if (mod && e.key.toLowerCase() === "e") { e.preventDefault(); openExport(); return; }
  if (typing) return;
  if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); }
  else if (mod && e.key.toLowerCase() === "y") { e.preventDefault(); redo(); }
  else if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); deleteSelected(); }
  else if (e.key === "s" || e.key === "S") splitAtPlayhead();
  else if (e.key === "ArrowLeft") { e.preventDefault(); setPlayhead(state.playhead - (e.shiftKey ? 1 : 1 / fpsValue())); }
  else if (e.key === "ArrowRight") { e.preventDefault(); setPlayhead(state.playhead + (e.shiftKey ? 1 : 1 / fpsValue())); }
  else if (e.key === "Home") setPlayhead(0, true);
  else if (e.key === "End") setPlayhead(1e9, true);
  else if (e.key === "Escape") ACTIONS.closeModal();
});

$("zoom").addEventListener("input", (e) => { state.zoom = Number(e.target.value); renderTimeline(); });
$("drives").addEventListener("change", (e) => browse(e.target.value));
$("compareToggle").addEventListener("change", refreshFrame);
$("scopesToggle").addEventListener("change", refreshFrame);
window.addEventListener("resize", () => { renderTimeline(); scheduleFrame(); });
window.addEventListener("beforeunload", (e) => { if (state.dirty) { e.preventDefault(); e.returnValue = ""; } });

async function boot() {
  if (!TOKEN) { document.body.innerHTML = "<p style='padding:20px'>Open Rushes Studio from its shortcut; this page needs the launch token.</p>"; return; }
  try { state.info = await api("/api/info"); } catch (e) { toast("Cannot reach the Rushes server: " + e.message, true); return; }
  if (!state.info.ffmpeg) toast("FFmpeg was not found. Install FFmpeg or reinstall Rushes.", true);
  rulerScrub();
  let restored = false;
  const startProject = params.get("project");
  if (startProject) { await openProject(startProject); restored = !!state.project; }
  if (!restored) {
    try {
      const saved = JSON.parse(localStorage.getItem("rushes.autosave") || "null");
      if (saved && saved.project && saved.project.clips && saved.project.clips.length) {
        loadProject(saved.project, saved.path, saved.baseDir);
        state.dirty = true; updateTitle();
        toast("Restored your last session (not saved to a file yet).");
        restored = true;
      }
    } catch (e) { /* ignore */ }
  }
  if (!restored) loadProject(newProject(), null, state.info.home);
  await browse(null);
  setInterval(() => api("/api/heartbeat", {}).catch(() => {}), 15000);
  pollJobs();
}

boot();
