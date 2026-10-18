'use strict';

const $ = (s) => document.querySelector(s);
const cv = $('#cv');
const ctx = cv.getContext('2d');

const state = {
  data: null,
  cases: [],
  sel: null,
  undo: [],
  busy: false,
  drag: null,
  view: { k: 0.01, ox: 0, oy: 0 },
  bounds: null,
  img: null,
  parked: {},
  variant: 0,
  chat_s: null,
  history: [],
  // artery stays off by default: the 900 mm clearance band is loud on a small plan,
  // and the bench row 0c already reports it. Tick the box when you want to see it.
  toggles: { image: true, heat: true, clear: false, doorz: true, corridor: true, artery: false, labels: true, grid: false },
};

const SNAP = 50;
const HIT_TOL = 6;
const $esc = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => $esc[c]);

const CAT_COLORS = {
  bed: '#cfe0f5', wardrobe: '#e3d6c6', sofa: '#d8e6d4', table: '#efe2c8',
  chair: '#e8e8e8', desk: '#e9e2d6', shelf: '#ded0e0', toilet: '#dfeef0',
  shower: '#dfeef0', basin: '#dfeef0', appliance: '#f0dcd6', storage: '#ded8cb',
};
const catColor = (c) => CAT_COLORS[c] || '#d9cfc0';

/* floor tints per function, same palette as scripts/render_plan2d.py:49-50 */
const FLOOR = { living: '#fbf6ea', bedroom: '#f6eef6', kitchen: '#eef7f0',
  bath: '#eaf2fa', storage: '#f3f3f0', dining: '#fdf4ec' };

const METRIC_LABELS = {
  SVR: '\u986f\u5927 SVR', BVC: '\u8996\u89ba\u901a\u900f BVC', CS: '\u52d5\u7dda CS',
  min_corridor_mm: '\u6700\u7a84\u8d70\u5eca (mm)', avg_spaciousness: '\u5e73\u5747\u986f\u5927\u5206',
};

function W() { return cv.clientWidth; }
function H() { return cv.clientHeight; }
function X(x) { return state.view.ox + (x - state.bounds[0]) * state.view.k; }
function Y(y) { return state.view.oy - (y - state.bounds[1]) * state.view.k; }
function PX(sx) { return (sx - state.view.ox) / state.view.k + state.bounds[0]; }
function PY(sy) { return state.bounds[1] - (sy - state.view.oy) / state.view.k; }

function resize() {
  // the canvas owns its own box (flex child), so measure it, not the stage around it
  const w = Math.max(64, cv.clientWidth || cv.parentElement.clientWidth);
  const h = Math.max(64, cv.clientHeight || cv.parentElement.clientHeight);
  const dpr = window.devicePixelRatio || 1;
  cv.width = Math.round(w * dpr);
  cv.height = Math.round(h * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (state.data && state.bounds) fit(); else draw();
}

function fit() {
  if (!state.bounds) return;
  const b = state.bounds;
  const w = b[2] - b[0], h = b[3] - b[1];
  if (!(w > 0) || !(h > 0)) return;
  const k = Math.min((W() - 52) / w, (H() - 52) / h);
  state.view.k = k;
  state.view.ox = (W() - w * k) / 2;
  state.view.oy = (H() - h * k) / 2 + h * k;
  draw();
}

/* ---------- drawing ---------- */

function draw() {
  if (!cv.isConnected) return;
  ctx.clearRect(0, 0, W(), H());
  const d = state.data;
  if (!d || !state.bounds) return;
  const k = state.view.k;

  if (state.toggles.image && state.img) {
    ctx.save();
    ctx.globalAlpha = 0.45;
    ctx.drawImage(state.img, X(state.bounds[0]), Y(state.bounds[3]),
      (state.bounds[2] - state.bounds[0]) * k, (state.bounds[3] - state.bounds[1]) * k);
    ctx.restore();
  }
  // the HKHA shell first, so the flat reads as one envelope instead of floating room boxes
  const outline = d.plan.outline || [];
  if (outline.length) poly(outline, 'rgba(241,236,225,.9)', null);
  for (const rm of d.plan.rooms) {
    poly(rm.polygon, FLOOR[rm.func] || '#fffdf8', null);
    for (const hole of rm.holes || []) poly(hole, '#f4f2ed', null);
  }
  if (outline.length) poly(outline, null, '#8a8880', 2.4);
  if (state.toggles.grid) drawGrid();
  if (state.toggles.heat) drawHeat();
  for (const pt of d.plan.portals || []) {
    ctx.fillStyle = '#b9b1a6';
    ctx.beginPath(); ctx.arc(X(pt[0]), Y(pt[1]), Math.max(2.5, 120 * k), 0, 7); ctx.fill();
  }
  drawWalls();
  for (const o of flatOpenings()) drawOpening(o);

  if (state.toggles.doorz) {
    ctx.save(); ctx.strokeStyle = 'rgba(29,122,77,.5)'; ctx.setLineDash([5, 4]); ctx.lineWidth = 1.2;
    for (const dz of d.plan.door_zones || []) poly(dz, null, 'rgba(29,122,77,.5)');
    ctx.restore();
  }

  const ov = d.overlays || {};
  const art = ov.artery;
  if (state.toggles.artery && art) {
    ctx.save();
    // erosion leaves hairline slivers behind; only real clearance is worth drawing
    const pgs = (art.polygons || []).filter((pg) => areaOf(pg) > 20000);
    const fill = art.ok ? 'rgba(29,122,77,.16)' : 'rgba(179,38,30,.2)';
    const line = art.ok ? '#1d7a4d' : '#b3261e';
    for (const pg of pgs) {
      poly(pg, fill);
      poly(pg, null, line);
    }
    if (pgs.length) {
      const big = pgs.reduce((a, b) => (areaOf(b) > areaOf(a) ? b : a));
      const c = centroid(big);
      const txt = (art.min_width_mm || 900) + ' mm ' + (art.ok ? '\u4e3b\u901a\u9053 \u00b7 \u6de8\u7a7a' : '\u4e3b\u901a\u9053 \u00b7 \u65b7\u958b');
      ctx.font = '11px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      const tw = ctx.measureText(txt).width;
      ctx.fillStyle = 'rgba(255,253,248,.82)';
      ctx.fillRect(X(c[0]) - tw / 2 - 4, Y(c[1]) - 8, tw + 8, 16);
      ctx.fillStyle = line;
      ctx.fillText(txt, X(c[0]), Y(c[1]));
      ctx.textAlign = 'left';
      ctx.textBaseline = 'alphabetic';
    }
    ctx.restore();
  }
  if (state.toggles.corridor) {
    ctx.save(); ctx.setLineDash([7, 4]); ctx.lineWidth = 1.6;
    for (const rid in (ov.corridors || {})) {
      for (const c of ov.corridors[rid]) {
        ctx.strokeStyle = c.blocked ? '#b3261e' : (c.penalty > 0.02 ? '#8a5a2b' : 'rgba(120,120,120,.55)');
        ctx.beginPath(); ctx.moveTo(X(c.a[0]), Y(c.a[1])); ctx.lineTo(X(c.b[0]), Y(c.b[1])); ctx.stroke();
      }
    }
    ctx.restore();
  }

  drawPlacements();
  if (state.toggles.labels) drawLabels();   // after the furniture, so a bed cannot hide the room name
  const ep = d.plan.entry_point;
  if (ep) {
    ctx.fillStyle = '#1d7a4d';
    ctx.beginPath(); ctx.arc(X(ep[0]), Y(ep[1]), 6, 0, 7); ctx.fill();
    ctx.fillStyle = '#1d7a4d'; ctx.font = '11px sans-serif';
    ctx.fillText('\u5165\u53e3', X(ep[0]) + 8, Y(ep[1]) - 6);
  }
}

function poly(pts, fill, stroke, lw) {
  ctx.beginPath();
  pts.forEach((p, i) => { i ? ctx.lineTo(X(p[0]), Y(p[1])) : ctx.moveTo(X(p[0]), Y(p[1])); });
  ctx.closePath();
  if (fill) { ctx.fillStyle = fill; ctx.fill(); }
  if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = lw || 1.2; ctx.stroke(); }
}

function areaOf(pts) {
  let s = 0;
  for (let i = 0; i < pts.length - 1; i++) s += pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1];
  return Math.abs(s / 2);            // mm²
}

function centroid(pts) {
  let x = 0, y = 0;
  for (const p of pts) { x += p[0]; y += p[1]; }
  return [x / pts.length, y / pts.length];
}

function drawGrid() {
  const b = state.bounds, step = 500;
  ctx.strokeStyle = 'rgba(0,0,0,.07)'; ctx.lineWidth = 1;
  for (let x = Math.ceil(b[0] / step) * step; x <= b[2]; x += step) {
    ctx.beginPath(); ctx.moveTo(X(x), Y(b[3])); ctx.lineTo(X(x), Y(b[1])); ctx.stroke();
  }
  for (let y = Math.ceil(b[1] / step) * step; y <= b[3]; y += step) {
    ctx.beginPath(); ctx.moveTo(X(b[0]), Y(y)); ctx.lineTo(X(b[2]), Y(y)); ctx.stroke();
  }
}

function drawHeat() {
  const grids = (state.data.overlays || {}).exposure || {};
  // keep the heat inside the rooms: a grid cell always overhangs the polygon by a bit
  ctx.save();
  ctx.beginPath();
  for (const rm of (state.data.plan.rooms || [])) {
    rm.polygon.forEach((p, i) => { i ? ctx.lineTo(X(p[0]), Y(p[1])) : ctx.moveTo(X(p[0]), Y(p[1])); });
    ctx.closePath();
  }
  ctx.clip();
  for (const rid in grids) {
    const g = grids[rid];
    if (!g || !g.values || !g.cell) continue;
    for (let iy = 0; iy < g.values.length; iy++) {
      for (let ix = 0; ix < g.values[iy].length; ix++) {
        const v = g.values[iy][ix] / 100;
        if (v <= 0.02) continue;
        ctx.fillStyle = 'rgba(214,120,30,' + (v * 0.32).toFixed(3) + ')';
        const px = X(g.x0 + ix * g.cell), py = Y(g.y0 + (iy + 1) * g.cell);
        ctx.fillRect(px, py, g.cell * state.view.k, g.cell * state.view.k);
      }
    }
  }
  ctx.restore();
}

function drawLabels() {
  const k = state.view.k;
  ctx.save();
  ctx.font = Math.max(10, Math.min(15, 380 * k)) + 'px sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  for (const rm of (state.data.plan.rooms || [])) {
    const c = centroid(rm.polygon);
    const txt = rm.label + '  ' + rm.area_m2.toFixed(1) + ' m\u00b2';
    const tw = ctx.measureText(txt).width;
    ctx.fillStyle = 'rgba(255,253,248,.82)';   // halo: the name stays readable over furniture
    ctx.fillRect(X(c[0]) - tw / 2 - 4, Y(c[1]) - 9, tw + 8, 18);
    ctx.fillStyle = 'rgba(30,30,30,.85)';
    ctx.fillText(txt, X(c[0]), Y(c[1]));
  }
  ctx.restore();
}

function drawWalls() {
  const k = state.view.k;
  const ops = flatOpenings();
  for (const w of flatWalls()) {
    const th = Math.max(2, Math.min(20, (w.thickness_mm || 100) * k));
    const a = w.a, b = w.b;
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
    const ux = (b[0] - a[0]) / len, uy = (b[1] - a[1]) / len;
    const spans = [];
    for (const o of ops) {
      if (o.wall !== w.id) continue;
      const h = (o.width_mm || 0) / 2;
      spans.push([(o.center_mm || 0) - h, (o.center_mm || 0) + h]);
    }
    spans.sort((p, q) => p[0] - q[0]);
    const segs = [];
    let cur = 0;
    for (const s of spans) {
      if (s[0] > cur) segs.push([cur, s[0]]);
      cur = Math.max(cur, s[1]);
    }
    if (cur < len) segs.push([cur, len]);
    // partition / load_bearing colours are the repo's own (render_plan2d.py:48);
    // the width is the real thickness in mm, not a constant hairline
    ctx.save();
    ctx.strokeStyle = w.type === 'load_bearing' ? '#4a4a4a' : '#8d8d8d';
    ctx.lineWidth = th;
    ctx.lineCap = 'butt';
    for (const s of segs) {
      if (s[1] - s[0] < 1) continue;
      const p0 = [a[0] + ux * s[0], a[1] + uy * s[0]];
      const p1 = [a[0] + ux * s[1], a[1] + uy * s[1]];
      ctx.beginPath(); ctx.moveTo(X(p0[0]), Y(p0[1])); ctx.lineTo(X(p1[0]), Y(p1[1])); ctx.stroke();
    }
    ctx.restore();
  }
}

function drawOpening(o) {
  const w = wallById(o.wall);
  if (!w) return;
  const a = w.a, b = w.b;
  const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
  const ux = (b[0] - a[0]) / len, uy = (b[1] - a[1]) / len;
  const half = (o.width_mm || 800) / 2, c = o.center_mm || 0;
  const p0 = [a[0] + ux * (c - half), a[1] + uy * (c - half)];
  const p1 = [a[0] + ux * (c + half), a[1] + uy * (c + half)];
  const s0 = [X(p0[0]), Y(p0[1])], s1 = [X(p1[0]), Y(p1[1])];
  const dx = s1[0] - s0[0], dy = s1[1] - s0[1];
  const sl = Math.hypot(dx, dy) || 1;
  const sx = dx / sl, sy = dy / sl;          // along the wall, in screen pixels
  const nx = -sy, ny = sx;                   // wall normal, in screen pixels
  const th = Math.max(2, Math.min(20, (w.thickness_mm || 100) * state.view.k));
  ctx.save();
  if (o.kind === 'window') {
    ctx.strokeStyle = '#2a7ae2'; ctx.lineWidth = 1.3; ctx.lineCap = 'butt';
    for (const f of [-0.28, 0, 0.28]) {
      const ox = nx * th * f, oy = ny * th * f;
      ctx.beginPath(); ctx.moveTo(s0[0] + ox, s0[1] + oy); ctx.lineTo(s1[0] + ox, s1[1] + oy); ctx.stroke();
    }
    ctx.restore();
    return;
  }
  const sw = o.swing || {};
  const hingeB = /end|right|^b$|1/i.test(String(sw.hinge || ''));
  const hx = hingeB ? s1[0] : s0[0], hy = hingeB ? s1[1] : s0[1];
  const r2 = Math.max(6, (sw.width_mm || o.width_mm || 750) * state.view.k);
  const sign = /out|away/i.test(String(sw.dir || 'in')) ? -1 : 1;
  const leafX = nx * sign, leafY = ny * sign;
  const closedX = hingeB ? -sx : sx, closedY = hingeB ? -sy : sy;
  const a0 = Math.atan2(closedY, closedX);
  let d = Math.atan2(leafY, leafX) - a0;
  while (d <= -Math.PI) d += 2 * Math.PI;
  while (d > Math.PI) d -= 2 * Math.PI;
  ctx.strokeStyle = 'rgba(192,57,43,.45)'; ctx.lineWidth = 1; ctx.setLineDash([4, 3]);
  ctx.beginPath(); ctx.arc(hx, hy, r2, a0, a0 + d, d < 0); ctx.stroke();
  ctx.setLineDash([]);
  ctx.strokeStyle = '#c0392b'; ctx.lineWidth = 2; ctx.lineCap = 'round';
  ctx.beginPath(); ctx.moveTo(hx, hy); ctx.lineTo(hx + leafX * r2, hy + leafY * r2); ctx.stroke();
  ctx.strokeStyle = 'rgba(192,57,43,.75)'; ctx.lineWidth = 1; ctx.setLineDash([2, 3]);
  ctx.beginPath(); ctx.moveTo(s0[0], s0[1]); ctx.lineTo(s1[0], s1[1]); ctx.stroke();
  ctx.restore();
}

/* the plan carries walls/openings per room; flatten once so a wall can be found by id */
function flatWalls() {
  const p = state.data.plan;
  if (p.walls && p.walls.length) return p.walls;
  return (p.rooms || []).reduce((acc, rr) => acc.concat(rr.walls || []), []);
}

function flatOpenings() {
  const p = state.data.plan;
  if (p.openings && p.openings.length) return p.openings;
  return (p.rooms || []).reduce((acc, rr) => acc.concat(rr.openings || []), []);
}

function wallById(id) {
  for (const w of flatWalls()) if (w.id === id) return w;
  return null;
}

function drawPlacements() {
  for (const item of state.data.layout.rooms) {
    for (const p of item.placements) {
      if (p.unplaced) continue;
      const fdx = p.rot === 90 || p.rot === 270;
      const fw = fdx ? p.d : p.w, fd = fdx ? p.w : p.d;
      const x0 = p.x, y0 = p.y;
      const sx = X(x0), sy = Y(y0 + fd), sw = fw * state.view.k, sh = fd * state.view.k;
      if (state.toggles.clear && p.clearance_mm) {
        const c = p.clearance_mm;
        ctx.fillStyle = 'rgba(120,120,120,.09)';
        ctx.fillRect(X(x0 - c), Y(y0 + fd + c), (fw + 2 * c) * state.view.k, (fd + 2 * c) * state.view.k);
      }
      if (p.zone) {
        ctx.save(); ctx.setLineDash([5, 4]); ctx.strokeStyle = 'rgba(47,111,94,.4)'; ctx.lineWidth = 1;
        const zr = p.zone;
        ctx.strokeRect(X(zr[0]), Y(zr[3]), (zr[2] - zr[0]) * state.view.k, (zr[3] - zr[1]) * state.view.k);
        ctx.restore();
      }
      ctx.fillStyle = catColor(p.category);
      ctx.fillRect(sx, sy, sw, sh);
      if (p.h >= 1500) {
        ctx.save();
        ctx.beginPath(); ctx.rect(sx, sy, sw, sh); ctx.clip();
        ctx.strokeStyle = 'rgba(0,0,0,.28)'; ctx.lineWidth = 1;
        for (let i = -sh; i < sw; i += 6) {
          ctx.beginPath(); ctx.moveTo(sx + i, sy + sh); ctx.lineTo(sx + i + sh, sy); ctx.stroke();
        }
        ctx.restore();
      }
      ctx.strokeStyle = (state.sel && state.sel.room === item.room && state.sel.inst === p.inst) ? '#2f6f5e' : '#1a1a1a';
      ctx.lineWidth = (state.sel && state.sel.room === item.room && state.sel.inst === p.inst) ? 2.5 : 1;
      ctx.strokeRect(sx, sy, sw, sh);
      const big = Math.min(sw, sh) > 14;
      if (big || state.toggles.labels) {
        ctx.fillStyle = 'rgba(0,0,0,.75)';
        ctx.font = (big ? 11 : 9) + 'px sans-serif';
        ctx.fillText(p.inst, sx + 3, sy + (big ? sh / 2 + 4 : 11));
      }
      drawFront(p, fw, fd);
    }
  }
}

function drawFront(p, fw, fd) {
  const cx = p.x + fw / 2, cy = p.y + fd / 2;
  const dirs = { 0: [0, 1], 90: [-1, 0], 180: [0, -1], 270: [1, 0] };
  const d = dirs[p.front] || dirs[p.rot] || [0, 1];
  const len = Math.max(fw, fd) * 0.28;
  const bx = cx + d[0] * (fw / 2), by = cy + d[1] * (fd / 2);
  const ex = bx + d[0] * len, ey = by + d[1] * len;
  ctx.strokeStyle = '#8a5a2b'; ctx.lineWidth = 1.2;
  ctx.beginPath(); ctx.moveTo(X(bx), Y(by)); ctx.lineTo(X(ex), Y(ey)); ctx.stroke();
}

/* ---------- side panels ---------- */

function panels() {
  const d = state.data;
  if (!d) return;
  const flagMap = d.flags || {};
  const badInsts = Object.keys(flagMap).filter((k) => (flagMap[k] || []).length);
  $('#headline').innerHTML =
    '<div><span class="big">' + d.pass_count + '/' + d.total + '</span> \u6838\u67e5\u901a\u904e</div>' +
    '<div class="sub">' + esc(d.case) + ' \u00b7 ' + d.elapsed_s + 's \u00b7 \u5e73\u5747\u986f\u5927 ' +
    (d.scores && d.scores.average) + '</div>' +
    (badInsts.length ? '<div class="sub">' + badInsts.length + ' \u500b\u5bb6\u5177\u6709\u554f\u984c \u00b7 ' + esc(badInsts.join(', ')) + '</div>' : '');

  $('#tab-checks').innerHTML = (d.checks || []).map((c) => {
    return '<div class="row"><span class="pill ' + (c.ok ? 'ok' : 'no') + '">' +
      (c.ok ? 'PASS' : 'FAIL') + '</span><div style="flex:1;min-width:0">' +
      '<span class="t">' + esc(c.tag) + '</span><br><span class="d">' + esc(c.detail || '') + '</span></div></div>';
  }).join('');

  let sp = '<div class="grid2">';
  const sc = d.scores || {};
  sp += roomBox('\u5e73\u5747\u986f\u5927\u5206', sc.average == null ? '-' : sc.average);
  sp += roomBox('\u6838\u67e5', d.pass_count + '/' + d.total);
  for (const k in (d.metrics || {})) {
    const mv = d.metrics[k];
    if (mv === null || typeof mv === 'object') continue;   // nested sub-dicts belong in the room rows
    sp += roomBox((METRIC_LABELS[k] || k), mv);
  }
  sp += '</div><h3>\u6bc0\u500b\u623f\u9593</h3>';
  const per = sc.per_room || {};
  for (const rid in per) {
    const v = per[rid];
    sp += '<div class="room"><div><b>' + esc(rid) + '</b> \u986f\u5927\u5206 <span class="sc ' +
      (v.score >= 70 ? 'ok' : 'no') + '">' + v.score + '</span></div>';
    if (v.detractors && v.detractors.length) {
      sp += '<div style="color:#b3261e;font-size:11.5px">' + v.detractors.slice(0, 4).map(detText).join(' \u00b7 ') + '</div>';
    }
    sp += '<div style="color:#77705f;font-size:11px">' + metricLine(v.metrics) + '</div></div>';
  }
  $('#tab-space').innerHTML = sp;

  const counts = {};
  for (const inst in flagMap) counts[inst] = (flagMap[inst] || []).length;
  const rows = [];
  for (const item of d.layout.rooms) {
    for (const p of item.placements) {
      rows.push({ room: item.room, p: p, n: counts[p.inst] || 0 });
    }
  }
  for (const inst of d.layout.unplaced || []) {
    const parked = state.parked[inst] || { inst: inst, category: '\u672a\u653e\u6210', w: 0, d: 0, rot: 0, x: 0, y: 0 };
    rows.push({ room: (d.layout.assignment || {})[inst] || '?', p: parked, n: 1 + (counts[inst] || 0), back: true });
  }
  rows.sort((a, b) => b.n - a.n);
  $('#tab-items').innerHTML = rows.map((row) => {
    const p = row.p;
    const cls = (state.sel && state.sel.room === row.room && state.sel.inst === p.inst) ? 'item sel'
      : (row.n ? 'item bad' : 'item');
    const act = row.back ? 'data-back="' + esc(p.inst) + '"' : 'data-i="' + esc(row.room) + '|' + esc(p.inst) + '"';
    return '<div class="' + cls + '" ' + act + '>' +
      '<span class="cat">' + esc(p.category) + '</span><div style="flex:1;min-width:0">' +
      '<div class="t">' + esc(p.inst) + (p.unplaced ? ' \u672a\u653e' : '') + '</div>' +
      (row.back ? '<div class="s">\u672a\u653e\u6210 \u00b7 ' + esc(row.room) + '\uff08\u9ede\u4e00\u4e0b\u653e\u56de\u539f\u4f4d\uff09</div>'
        : '<div class="s">' + p.w + '\u00d7' + p.d + 'mm \u00b7 ' + p.rot + '\u00b0 \u00b7 (' + p.x + ',' + p.y + ')</div>') +
      (row.n ? '<div class="s bad">' + (flagMap[p.inst] || []).slice(0, 3).map((f) => esc(f.rule + (f.detail ? ' ' + f.detail : ''))).join(' \u00b7 ') + '</div>' : '') + '</div>' +
      (row.n ? '<span class="pill no">' + row.n + '</span>' : '') + '</div>';
  }).join('');

  // \u89e3\u7b97 only shows a clock that actually solved something; an imported layout has none
  const solve_s = state.solve_s;
  $('#status').textContent = state.data.case + ' \u00b7 ' + d.pass_count + '/' + d.total +
    ' \u00b7 \u5e73\u5747\u986f\u5927 ' + (sc.average == null ? '-' : sc.average) +
    (solve_s == null ? '' : ' \u00b7 \u89e3\u7b97 ' + solve_s + 's') +
    (state.verify_s == null ? '' : ' \u00b7 \u5373\u6642\u6838\u67e5 ' + state.verify_s + 's') +
    (state.chat_s == null ? '' : ' \u00b7 \u50be\u5048 ' + state.chat_s + 's');
  const notes = $('#notes');
  if (d.notes && d.notes.length) {
    notes.classList.add('on');
    notes.innerHTML = d.notes.map((s) => '\u2022 ' + esc(s)).join('<br>');
  } else {
    notes.classList.remove('on'); notes.innerHTML = '';
  }
}

/* detractors come back as dicts: issue / subject / detail / severity */
function detText(v) {
  if (v == null) return '';
  if (typeof v === 'string') return esc(v);
  let s = [v.issue, v.subject].filter(Boolean).map(esc).join(' ');
  if (v.detail) s += (s ? ' \u2014 ' : '') + esc(v.detail);
  if (v.fix_options && v.fix_options.length) s += ' \u2192 ' + v.fix_options.slice(0, 3).map(esc).join(' / ');
  return s;
}

function metricLine(metrics) {
  return Object.keys(metrics || {}).filter((k) => typeof metrics[k] !== 'object')
    .slice(0, 4).map((k) => (METRIC_LABELS[k] || k) + ' ' + metrics[k]).join(' \u00b7 ');
}

function roomBox(label, v) {
  return '<div class="box"><div class="k">' + esc(label) + '</div><div class="v">' + esc(v) + '</div></div>';
}

/* ---------- api + job polling ---------- */

async function api(path, body) {
  const res = await fetch(path, {
    method: body ? 'POST' : 'GET',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok || j.error) throw new Error(j.error || j.message || ('HTTP ' + res.status));
  return j;
}

function setBusy(msg) {
  state.busy = !!msg;
  $('#hint').classList.toggle('on', !!msg);
  $('#hint').textContent = msg || '';
  ['#solve', '#repair', '#variant', '#chatsend'].forEach((s) => { $(s).disabled = !!msg; });
}

function adopt(data, solve_s, verify_s) {
  state.data = data;
  state.bounds = data.bounds;
  state.undo = [];
  state.parked = {};   // the solver re-decided everything; old parked copies are stale
  // solve_s undefined -> payload came straight from a solve job;
  // solve_s === null -> nothing was solved here, so never label the clock \u89e3\u7b97
  state.solve_s = solve_s === undefined ? data.elapsed_s : solve_s;
  state.verify_s = verify_s === undefined ? null : verify_s;
  state.chat_s = null;          // a fresh solve/verify ends the chat's clock
  panels();
  fit();
}

async function loadCases() {
  const j = await api('/api/cases');
  state.cases = j.cases;
  const sel = $('#case');
  sel.innerHTML = j.cases.map((c) =>
    '<option value="' + esc(c.name) + '">' + esc(c.label) + ' \u2014 ' + c.area_m2 + ' m\u00b7 ' +
    c.rooms.length + ' \u623f \u00b7 ' + esc(c.solver) + '</option>').join('');
  if (!state.cases.length) {
    setBusy('\u627e\u554f\u552f\u4e00\u500b\u6709\u6548\u7684 case\uff1a\u5148\u8dd1\u5b8c fixture \u518d\u4f86');
  }
}

async function solve(extra) {
  if (state.busy) return;
  state.history = [];        // a fresh solve is a fresh conversation context
  const solver = $('#solver').value;
  setBusy('\u6c42\u89e3\u4e2d\u2026 \u9019\u500b\u5c08\u6bb5\u6703\u8abf CP-SAT \u7d66\u6bc0\u9593\u623f\u653e\u5bb6\u5177');
  try {
    const j = await api('/api/solve', {
      case: $('#case').value,
      solver: solver === 'auto' ? undefined : solver,
      time_limit: parseFloat($('#timelimit').value) || 20,
      parallel: $('#parallel').checked,
      variant: state.variant,
    });
    for (let i = 0; i < 240; i++) {
      await new Promise((r2) => setTimeout(r2, 700));
      const st = await api('/api/jobs/' + j.job);
      if (st.state === 'done') {
        adopt(st.result);
        setBusy('');
        return;
      }
      if (st.state === 'error') {
        setBusy('\u5931\u6557\uff1a' + st.error);
        return;
      }
      setBusy('\u6c42\u89e3\u4e2d\u2026 ' + st.state + ' \u00b7 ' + ((i + 1) * 0.7).toFixed(1) + 's');
    }
    setBusy('\u5de5\u4f5c\u5340\u592a\u9577\u6642\u9593\uff0c\u6539\u7528\u8f03\u77ed\u7684\u9650\u6642\u518d\u8a66');
  } catch (e) {
    setBusy('\u555f\u52d5\u5931\u6557\uff1a' + e.message);
  }
}

let verifyTimer = null;
function queueVerify() {
  if (verifyTimer) clearTimeout(verifyTimer);
  verifyTimer = setTimeout(verifyNow, 350);
}

async function verifyNow() {
  if (!state.data) return;
  try {
    const view = state.view;
    const d = await api('/api/verify', { case: $('#case').value, layout: state.data.layout });
    state.data = Object.assign(state.data, d);
    state.view = view;
    state.verify_s = d.elapsed_s;   // re-verify is fast; do not overwrite the solve time
    panels();
    draw();
  } catch (e) { console.warn('verify failed', e); }
}

function pushUndo() {
  state.undo.push(JSON.stringify(state.data.layout));
  if (state.undo.length > 50) state.undo.shift();
}

function restore() {
  if (!state.undo.length || !state.data) return;
  state.data.layout = JSON.parse(state.undo.pop());
  state.sel = null;
  panels();
  draw();
  queueVerify();
}

async function loadImage() {
  state.img = null;
  const src = state.data && state.data.plan.meta && state.data.plan.meta.image;
  if (!src) return;
  const url = '/api/file?p=' + encodeURIComponent(src);
  const img = new Image();
  await new Promise((res) => { img.onload = res; img.onerror = res; img.src = url; });
  if (img.width) { state.img = img; draw(); }
}

/* ---------- picking + drag + zoom ---------- */

function hitTest(wx, wy) {
  const tol = HIT_TOL / state.view.k;
  const rooms = state.data.layout.rooms;
  for (let i = rooms.length - 1; i >= 0; i--) {
    const ps = rooms[i].placements;
    for (let j = ps.length - 1; j >= 0; j--) {
      const p = ps[j];
      if (p.unplaced) continue;
      const fdx = p.rot === 90 || p.rot === 270;
      const fw = fdx ? p.d : p.w, fd = fdx ? p.w : p.d;
      if (wx >= p.x - tol && wx <= p.x + fw + tol && wy >= p.y - tol && wy <= p.y + fd + tol) {
        return { room: rooms[i].room, inst: p.inst };
      }
    }
  }
  return null;
}

cv.addEventListener('pointerdown', (ev) => {
  if (!state.data) return;
  const rect = cv.getBoundingClientRect();
  const sx = ev.clientX - rect.left, sy = ev.clientY - rect.top;
  const hit = hitTest(PX(sx), PY(sy));
  cv.setPointerCapture(ev.pointerId);
  if (hit) {
    state.sel = hit;
    state.drag = {
      hit: hit, sx: sx, sy: sy, ox: state.view.ox, oy: state.view.oy,
      push: true, moved: false,
      p0: placementOf(hit).x, p1: placementOf(hit).y,
    };
  } else {
    state.sel = null;
    state.drag = { pan: true, sx: sx, sy: sy, ox: state.view.ox, oy: state.view.oy };
  }
  panels();
  draw();
});

cv.addEventListener('pointermove', (ev) => {
  if (!state.drag || !state.data) return;
  const rect = cv.getBoundingClientRect();
  const sx = ev.clientX - rect.left, sy = ev.clientY - rect.top;
  if (state.drag.pan) {
    state.view.ox = state.drag.ox + (sx - state.drag.sx);
    state.view.oy = state.drag.oy + (sy - state.drag.sy);
    draw();
    return;
  }
  const p = placementOf(state.drag.hit);
  if (!p) { state.drag = null; return; }
  const dx = (sx - state.drag.sx) / state.view.k;
  const dy = -(sy - state.drag.sy) / state.view.k;
  if (!state.drag.moved) {
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
    if (state.drag.push) pushUndo();
    state.drag.moved = true;
  }
  p.x = Math.round((state.drag.p0 + dx) / SNAP) * SNAP;
  p.y = Math.round((state.drag.p1 + dy) / SNAP) * SNAP;
  draw();
});

cv.addEventListener('pointerup', () => {
  if (state.drag && state.drag.moved) queueVerify();
  state.drag = null;
});

cv.addEventListener('wheel', (ev) => {
  if (!state.data) return;
  ev.preventDefault();
  const rect = cv.getBoundingClientRect();
  const sx = ev.clientX - rect.left, sy = ev.clientY - rect.top;
  const wx = PX(sx), wy = PY(sy);
  const k2 = Math.max(0.002, Math.min(1, state.view.k * Math.exp(-ev.deltaY * 0.0012)));
  state.view.k = k2;
  state.view.ox = sx - (wx - state.bounds[0]) * k2;
  state.view.oy = sy + (wy - state.bounds[1]) * k2;
  draw();
}, { passive: false });

cv.addEventListener('dblclick', () => fit());

function placementOf(sel) {
  if (!sel || !state.data) return null;
  const item = state.data.layout.rooms.find((r) => r.room === sel.room);
  if (!item) return null;
  return item.placements.find((p) => p.inst === sel.inst) || null;
}

/* The payload contract: a placed item lives in its room block, an unplaced one
   lives in layout.unplaced as an inst string. The solver reads only those two,
   so removing an item means splicing it out and parking a copy client-side. */
function unplace(sel) {
  const item = sel && state.data.layout.rooms.find((r) => r.room === sel.room);
  if (!item) return false;
  const i = item.placements.findIndex((p) => p.inst === sel.inst);
  if (i < 0) return false;
  const parked = item.placements.splice(i, 1)[0];
  state.parked[parked.inst] = parked;
  const up = state.data.layout.unplaced || (state.data.layout.unplaced = []);
  if (!up.includes(parked.inst)) up.push(parked.inst);
  return true;
}

function replace(inst) {
  const parked = state.parked[inst];
  if (!parked) return false;
  const rid = (state.data.layout.assignment || {})[inst];
  const item = state.data.layout.rooms.find((r) => r.room === rid);
  if (!item) return false;
  item.placements.push(parked);
  delete state.parked[inst];
  state.data.layout.unplaced = (state.data.layout.unplaced || []).filter((x) => x !== inst);
  return true;
}

document.addEventListener('keydown', (ev) => {
  const t = ev.target;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
  if (!state.data) return;
  const p = placementOf(state.sel);
  if (ev.key === 'z') { restore(); return; }
  if (ev.key === 'Escape') { state.sel = null; panels(); draw(); return; }
  if (!p) return;
  const big = ev.shiftKey ? 250 : SNAP;
  if (ev.key === 'r') { pushUndo(); p.rot = (p.rot + 90) % 360; }
  else if (ev.key === 'Delete' || ev.key === 'Backspace') { pushUndo(); unplace(state.sel); state.sel = null; }
  else if (ev.key === 'ArrowLeft') { pushUndo(); p.x -= big; }
  else if (ev.key === 'ArrowRight') { pushUndo(); p.x += big; }
  else if (ev.key === 'ArrowUp') { pushUndo(); p.y += big; }
  else if (ev.key === 'ArrowDown') { pushUndo(); p.y -= big; }
  else return;
  ev.preventDefault();
  panels();
  draw();
  queueVerify();
});

/* ---------- chat: the LLM only picks actions; deterministic code applies
   them and the engine re-solves + re-checks ---------- */

function chatSay(who, html, cls) {
  const log = $('#chatlog');
  const row = document.createElement('div');
  row.className = 'msg ' + who + (cls ? ' ' + cls : '');
  row.innerHTML = '<b>' + (who === 'me' ? '\u4f60' : '\u8a2d\u8a08\u5e2b') + '</b><div class="txt">' + html + '</div>';
  log.appendChild(row);
  log.scrollTop = log.scrollHeight;
  return row;
}

function chatResult(d, prevPass) {
  const c = d.chat || {};
  let html = esc(c.say || '\uff08\u6a21\u578b\u5187\u8b1b\u5622\uff09');
  const acts = (c.actions || []).map((a) => {
    const p = a.params || {};
    const head = [a.action, a.target || ''].join(' ').trim() + (p.to_room ? ' \u2192 ' + p.to_room : '');
    return '<div class="act">\u00b7 <code>' + esc(head) + '</code> ' + esc(a.reason || '') + '</div>';
  }).join('');
  if (acts) html += acts;
  if (d.total) {                     // the engine's verdict, not the model's opinion
    let line = '\u5f15\u64ce\u6838\u67e5 ' + d.pass_count + '/' + d.total;
    if (prevPass != null && prevPass !== d.pass_count) {
      line += (d.pass_count < prevPass ? ' \u2193 \u5c11\u5497 ' : ' \u2191 \u591a\u5497 ') +
        Math.abs(prevPass - d.pass_count) + ' \u9805';
    }
    if (!d.solved) line += ' \u00b7 \u5187\u91cd\u65b0\u89e3\u7b97';
    html += '<div class="eng">' + esc(line) + '</div>';
    const bad = (d.checks || []).filter((r) => !r.ok).slice(0, 3)
      .map((r) => '\u2717 ' + r.tag + (r.detail ? ' ' + r.detail : ''));
    if (bad.length) html += '<div class="eng">' + bad.map((s) => esc(s)).join('<br>') + '</div>';
  }
  const eng = [].concat(c.dropped || [], d.notes || []);
  if (eng.length) html += '<div class="eng">' + eng.slice(0, 6).map((s) => esc(s)).join('<br>') + '</div>';
  return html;
}

async function chatSend(text) {
  const msg = String(text || '').trim();
  if (!msg || state.busy || !state.data) return;
  const prevPass = state.data ? state.data.pass_count : null;
  $('#chatmsg').value = '';
  chatSay('me', esc(msg));
  const pending = chatSay('bot', '\u8ad7\u7dca\u2026\uff08LLM \u63c0\u52d5\u4f5c + \u5f15\u64ce\u91cd\u89e3\uff0c\u901a\u5e38 5-20 \u79d2\uff09');
  setBusy('\u8ad7\u7dca\u2026');
  const hist = (state.history || []).slice(-8);
  try {
    const sv = $('#solver').value;
    const d = await api('/api/chat', {
      case: $('#case').value,
      layout: state.data.layout,
      message: msg,
      history: hist,
      solver: sv === 'auto' ? undefined : sv,
      time_limit: Number($('#timelimit').value) || 20,
      parallel: $('#parallel').checked,
      variant: state.variant,
    });
    pending.remove();
    state.history.push({ role: 'user', text: msg });
    state.history.push({ role: 'assistant', text: (d.chat || {}).say || '' });
    if (d.plan) {
      // \u89e3\u7b97 clock = solver time only; a refusal / nudge-only reply has none
      adopt(d, d.solved ? d.elapsed_s : null, null);
    }
    // \u50be\u5071 clock = the LLM call itself, even when nothing changed
    state.chat_s = (d.chat || {}).llm_s == null ? null : d.chat.llm_s;
    panels();
    draw();
    chatSay('bot', chatResult(d, prevPass));
    setBusy('');
  } catch (e) {
    pending.remove();
    chatSay('bot', esc('\u6539\u5514\u5230\uff1a' + e.message), 'bad');
    setBusy('');
  }
}

$('#chatf').addEventListener('submit', (ev) => { ev.preventDefault(); chatSend($('#chatmsg').value); });
$('#chatmsg').addEventListener('keydown', (ev) => {
  if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); chatSend($('#chatmsg').value); }
});
document.querySelectorAll('#chat .chip').forEach((b) => b.addEventListener('click', () => chatSend(b.textContent)));

/* ---------- buttons + boot ---------- */

$('#solve').addEventListener('click', () => solve());
$('#case').addEventListener('change', () => solve());
$('#repair').addEventListener('click', async () => {
  if (!state.data || state.busy) return;
  setBusy('\u4fee\u5b9a\u4e2d\u2026 \u53ea\u63a8\u5bb6\u5177\uff0c\u4e0d\u6539\u671d\u5411\u8207\u95dc\u4fc2\u898f\u5247');
  try {
    const j = await api('/api/repair', { case: $('#case').value, layout: state.data.layout });
    state.data = j;
    state.verify_s = j.elapsed_s;   // a repair is a full engine re-check, not a solve
    panels();
    draw();
    setBusy('');
  } catch (e) { setBusy('\u4fee\u5b9a\u5931\u6557\uff1a' + e.message); }
});
$('#variant').addEventListener('click', () => { state.variant = (state.variant + 1) % 8; solve(); });
$('#undo').addEventListener('click', restore);
$('#reset').addEventListener('click', () => { state.undo = []; solve(); });
$('#export').addEventListener('click', () => {
  if (!state.data) return;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(state.data.layout, null, 2)], { type: 'application/json' }));
  a.download = ($('#case').value || 'layout') + '_edited.json';
  a.click();
});
/* export -> edit elsewhere -> import: the layout goes back through the engine's own checks */
$('#import').addEventListener('change', async (ev) => {
  const inp = ev.target;
  const f = inp.files[0];
  inp.value = '';        // so the very same file can be loaded again
  if (!f) return;
  inp.disabled = true;
  setBusy('\u8f09\u5165\u4e2d\u2026');
  try {
    const payload = JSON.parse(await f.text());
    const layout = payload.layout || payload;
    if (!Array.isArray(layout.rooms)) {
      throw new Error('\u5462\u500b\u6a94\u6848\u5187 layout.rooms \u9663\u5217\uff08\u8acb\u7528 Export JSON \u5c0e\u51fa\u6a94\u6848\u5148\u81f3\u53ef\u4ee5\u8f09\u5165\uff09');
    }
    const d = await api('/api/verify', { case: $('#case').value, layout });
    adopt(d, null, d.elapsed_s);   // nothing was solved here, but the engine did re-verify it
    const hint = $('#hint');
    setBusy('');
    hint.textContent = '\u5df2\u8f09\u5165 ' + f.name + ' \u00b7 \u5f15\u64ce\u91cd\u65b0\u6838\u67e5\u5b8c\u6210';
    hint.classList.add('on');
    setTimeout(() => { hint.classList.remove('on'); if (!state.busy) hint.textContent = ''; }, 2500);
  } catch (e) {
    setBusy('\u8f09\u5165\u5931\u6557\uff1a' + e.message);
  } finally {
    inp.disabled = false;
  }
});
$('#tab-items').addEventListener('click', (ev) => {
  const back = ev.target.closest('[data-back]');
  if (back) {
    if (replace(back.dataset.back)) { state.sel = null; panels(); draw(); queueVerify(); }
    return;
  }
  const row = ev.target.closest('[data-i]');
  if (!row) return;
  const [room, inst] = row.dataset.i.split('|');
  state.sel = { room: room, inst: inst };
  const p = placementOf(state.sel);
  if (p) centerOn(p);
  panels();
  draw();
});
function centerOn(p) {
  const fdx = p.rot === 90 || p.rot === 270;
  const fw = fdx ? p.d : p.w, fd = fdx ? p.w : p.d;
  state.view.ox = W() / 2 - (p.x + fw / 2 - state.bounds[0]) * state.view.k;
  state.view.oy = H() / 2 + (p.y + fd / 2 - state.bounds[1]) * state.view.k;
}
document.querySelectorAll('#toolbar [data-t]').forEach((el) => {
  el.checked = state.toggles[el.dataset.t] !== false;
  el.addEventListener('change', () => { state.toggles[el.dataset.t] = el.checked; draw(); });
});
window.addEventListener('resize', resize);

(async function boot() {
  resize();
  await loadCases();
  await solve();
  await loadImage();
}());
