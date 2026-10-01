// Drum sequencer: multi-voice tracks on a single-voice synth. Each hit loads its voice via param diffs
// just after the previous note-on (Engine.hit), so ringing tails keep their own sound.
'use strict';
const Seq = (() => {
  const MAX = 64;                  // steps per track (grid shows 32 per page)
  const PAGE = 32;
  // step length per rate, in quarter notes
  const RATES = { '1/4': 1, '1/8': 1 / 2, '1/16': 1 / 4, '1/32': 1 / 8, '1/8T': 1 / 3, '1/16T': 1 / 6, '1/32T': 1 / 12 };
  const ROLES = ['kick', 'snare', 'hat', 'tom', 'perc'];
  // gate: null = the track's gate (ms), else % of the step; acc: full velocity; slide: legato into the next note (tie over empty steps)
  const newStep = () => ({ on: false, vel: 100, prob: 100, rat: 1, nudge: 0, pitch: 0, pl: null, gate: null, acc: false, slide: false });
  const PLOCK_PARAMS = [                                         // [label, DX7 param index, max]
    ['— none —', -1, 0], ['Algorithm', 134, 31], ['Feedback', 135, 7], ['Transpose', 144, 48],
    ...[1, 2, 3, 4, 5, 6].flatMap(n => [[`OP${n} level`, DX7.paramIndex(n, 'OL'), 99], [`OP${n} coarse`, DX7.paramIndex(n, 'FC'), 31], [`OP${n} decay R2`, DX7.paramIndex(n, 'R2'), 99]]),
    ['PEG L1 (pitch hit)', 130, 99], ['LFO pitch depth', 139, 99],
  ];
  const MODES = [  // paint modes: [id, label, min, max, default]
    ['on', 'Steps', 0, 1, 1], ['vel', 'Velocity', 1, 127, 100], ['prob', 'Chance %', 0, 100, 50], ['rat', 'Ratchet', 1, 4, 2],
    ['nudge', 'Nudge %', -50, 50, 20], ['pitch', 'Pitch', -24, 24, 7], ['pl', 'P-lock', 0, 127, 50],
    ['acc', 'Accent', 0, 1, 1], ['slide', 'Slide', 0, 1, 1], ['gate', 'Gate %', 5, 100, 50],
  ];
  const TOGGLES = new Set(['on', 'acc', 'slide']);

  /* ---------- built-in starter voices (so it works before the library is loaded) ---------- */
  function drumVoice(role) {
    const v = DX7.initVoice(role.toUpperCase().slice(0, 10));
    v.ops.forEach(o => { o.OL = 0; o.R1 = 99; o.R2 = 60; o.R3 = 99; o.R4 = 70; o.L1 = 99; o.L2 = 0; o.L3 = 0; o.L4 = 0; });
    const g = v.g, op = v.ops;
    if (role === 'kick') {        // sine with a fast downward pitch sweep + click
      g.ALG = 0; op[0].OL = 99; op[0].FC = 0; op[0].R2 = 45; op[1].OL = 72; op[1].FC = 1; op[1].R2 = 92;
      g.PR1 = 70; g.PL4 = 78; g.PL1 = 50; g.PL2 = 50; g.PL3 = 50; g.PR4 = 99;
    } else if (role === 'snare') { // body sine + feedback noise
      g.ALG = 31; g.FB = 7; op[0].OL = 88; op[0].FC = 1; op[0].R2 = 72; op[5].OL = 95; op[5].FC = 7; op[5].R2 = 66;
      g.PR1 = 90; g.PL4 = 60; g.PL1 = 50; g.PL2 = 50; g.PL3 = 50;
    } else if (role === 'hat') {   // high feedback noise, very short
      g.ALG = 31; g.FB = 7; op[5].OL = 97; op[5].FC = 15; op[5].R2 = 82; op[4].OL = 80; op[4].FC = 11; op[4].FF = 37; op[4].R2 = 85;
    } else if (role === 'tom') {
      g.ALG = 0; op[0].OL = 99; op[0].FC = 1; op[0].R2 = 52; op[1].OL = 55; op[1].FC = 1; op[1].R2 = 85;
      g.PR1 = 60; g.PL4 = 62; g.PL1 = 50; g.PL2 = 50; g.PL3 = 50;
    } else {                        // perc: inharmonic metal (cowbell-ish)
      g.ALG = 4; op[0].OL = 95; op[0].FC = 1; op[0].R2 = 62; op[1].OL = 70; op[1].FC = 2; op[1].FF = 48; op[1].R2 = 70;
      op[2].OL = 90; op[2].FC = 1; op[2].FF = 48; op[2].R2 = 62;
    }
    return { name: v.name.trim(), vced: DX7.toVced(DX7.clampVoice(v)) };
  }
  function defaultPattern() {
    const mk = (name, role, note, hits, extra = {}) => {
      const steps = Array.from({ length: MAX }, newStep);
      hits.forEach(i => { steps[i].on = true; });
      return { name, role, note, len: 16, gate: 120, mode: 'fixed', mute: false, solo: false, ratPitch: 0, plock: -1,
        voices: [drumVoice(role)], steps, ...extra };
    };
    const p = { bpm: 112, swing: 0, human: 0, glitch: 0, rate: '1/16', len: 16, fx: { cc: -1, len: 16, vals: Array(MAX).fill(null) }, tracks: [
      mk('Kick', 'kick', 48, [0, 7, 10]),
      mk('Snare', 'snare', 60, [4, 12]),
      mk('Hat', 'hat', 72, [0, 2, 4, 6, 8, 10, 12, 14], { gate: 60 }),
      mk('Open hat', 'hat', 72, [15], { gate: 250 }),
      mk('Tom', 'tom', 55, [14], { len: 12 }),
      mk('Perc', 'perc', 67, [3, 11], { len: 7 }),
    ] };
    p.tracks[2].steps.forEach((s, i) => { if (s.on && i % 4 === 2) s.vel = 70; });
    p.tracks[3].voices[0] = { ...p.tracks[3].voices[0], name: 'OPEN HAT', vced: (() => { const v = DX7.fromVced(p.tracks[3].voices[0].vced); v.ops[5].R2 = 55; v.ops[4].R2 = 58; return DX7.toVced(v); })() };
    return p;
  }

  /* ---------- state ---------- */
  let P = load('cur') || defaultPattern();
  let active = 0, mode = 'on', brush = 1, page = 0;
  // chain of saved slots, e.g. "1 2 2 3"; stored apart from patterns
  function chain() {
    try { const c = JSON.parse(localStorage.getItem('fm1.chain') || '{}'); return { on: !!c.on, slots: (c.slots || []).filter(n => n >= 1 && n <= 16) }; }
    catch { return { on: false, slots: [] }; }
  }
  function setChain(c) { try { localStorage.setItem('fm1.chain', JSON.stringify(c)); } catch {} }
  const play = { on: false, k: 0, next: 0, pending: [], marks: [], cyc: [], flashes: [] };
  function load(slot) { try { const j = localStorage.getItem('fm1.pat.' + slot); return j ? normalize(JSON.parse(j)) : null; } catch { return null; } }
  function save(slot = 'cur') { try { localStorage.setItem('fm1.pat.' + slot, JSON.stringify(P)); } catch {} }
  // Patterns and kits come from files and localStorage, so rebuild them from known fields with clamped values:
  // a malformed file then either loads cleanly or is rejected, instead of throwing every scheduler tick.
  const num = (v, a, b, d) => { v = Number(v); return Number.isFinite(v) ? Math.max(a, Math.min(b, Math.round(v))) : d; };
  const MACRO_IDS = new Set(['level', 'decay', 'release', 'tone', 'punch', 'sweep', 'grit', 'dyn']);
  function cleanVoice(v) {
    if (!v || typeof v !== 'object') return null;
    const vc = Array.from(v.vced || []);
    if (vc.length < 155 || vc.slice(0, 155).some(x => !Number.isInteger(x) || x < 0 || x > 127)) return null;
    const macros = {};
    for (const [k, x] of Object.entries(v.macros || {})) if (MACRO_IDS.has(k) && (x === null || Number.isFinite(x))) macros[k] = x === null ? null : Math.round(x);
    const out = { name: String(v.name ?? 'VOICE').slice(0, 16), vced: vc.slice(0, 155), macros };
    if (v.measured && typeof v.measured === 'object') out.measured = v.measured;
    return out;
  }
  function cleanStep(s) {
    s = s && typeof s === 'object' ? s : {};
    return { on: !!s.on, vel: num(s.vel, 1, 127, 100), prob: num(s.prob, 0, 100, 100), rat: num(s.rat, 1, 4, 1), nudge: num(s.nudge, -50, 50, 0),
      pitch: num(s.pitch, -24, 24, 0), pl: s.pl == null ? null : num(s.pl, 0, 127, null),
      gate: s.gate == null ? null : num(s.gate, 5, 100, null), acc: !!s.acc, slide: !!s.slide };
  }
  function cleanTrack(t, i) {
    t = t && typeof t === 'object' ? t : {};
    const steps = Array.isArray(t.steps) ? t.steps.slice(0, MAX).map(cleanStep) : [];
    while (steps.length < MAX) steps.push(newStep());
    return { name: String(t.name ?? 'Track ' + (i + 1)).slice(0, 16), role: ROLES.includes(t.role) ? t.role : 'perc', note: num(t.note, 0, 127, 60),
      len: num(t.len, 1, MAX, 16), gate: num(t.gate, 5, 4000, 120), mode: ['fixed', 'cycle', 'random'].includes(t.mode) ? t.mode : 'fixed',
      mute: !!t.mute, solo: !!t.solo, ratPitch: num(t.ratPitch, -12, 12, 0),
      plock: PLOCK_PARAMS.some(x => x[1] === t.plock) ? t.plock : -1,
      voices: (Array.isArray(t.voices) ? t.voices : []).map(cleanVoice).filter(Boolean).slice(0, 8), steps };
  }
  function normalize(p) {
    if (!p || !Array.isArray(p.tracks) || !p.tracks.length) throw new Error('no tracks');
    const fx = p.fx && typeof p.fx === 'object' ? p.fx : {};
    const vals = Array.isArray(fx.vals) ? fx.vals.slice(0, MAX).map(v => v == null ? null : num(v, 0, 127, null)) : [];
    while (vals.length < MAX) vals.push(null);
    return { bpm: num(p.bpm, 30, 300, 112), swing: num(p.swing, 0, 75, 0), human: num(p.human, 0, 30, 0), glitch: num(p.glitch, 0, 100, 0),
      rate: p.rate in RATES ? p.rate : '1/16', len: num(p.len, 1, MAX, 16),
      fx: { cc: num(fx.cc, -1, 23, -1), len: num(fx.len, 1, MAX, 16), vals }, tracks: p.tracks.slice(0, 16).map(cleanTrack) };
  }
  function cleanKit(k) {
    if (!k || !Array.isArray(k.tracks)) throw new Error('no tracks');
    const tracks = k.tracks.map(cleanTrack).filter(t => t.voices.length).map(({ steps, len, mute, solo, ratPitch, plock, ...t }) => t);
    if (!tracks.length) throw new Error('no track has a valid voice');
    return { name: String(k.name ?? 'Imported kit').slice(0, 40), tracks };
  }

  /* ---------- scheduling ---------- */
  const stepMs = () => 60000 / P.bpm * (RATES[P.rate] || 0.25);
  function pickVoice(tr, ti) {
    if (!tr.voices.length) return null;
    if (P.glitch && Math.random() * 100 < P.glitch) {
      const all = P.tracks.flatMap(t => t.voices);
      return all[Math.floor(Math.random() * all.length)];
    }
    if (tr.mode === 'random') return tr.voices[Math.floor(Math.random() * tr.voices.length)];
    if (tr.mode === 'cycle') { const i = (play.cyc[ti] = (play.cyc[ti] ?? -1) + 1); return tr.voices[i % tr.voices.length]; }
    return tr.voices[0];
  }
  // How long a hit sounds: slide = legato into the next note (tie across empty steps), else step gate %, else track gate.
  function gateMs(tr, kp, st, sd, j) {
    const sub = sd / st.rat;
    if (st.slide && j === st.rat - 1) {
      let n = 1; while (n < tr.len && !tr.steps[(kp + n) % tr.len].on) n++;
      return (st.rat - 1 - j) * sub + sub + (n - 1) * sd + 15;       // overlap the next note by 15 ms
    }
    if (st.gate != null) return Math.max(5, sub * st.gate / 100);
    return st.rat > 1 ? Math.min(tr.gate, sub * 0.9) : tr.gate;
  }
  // Chain: at the end of the pattern (P.len steps) load the next slot in the chain; tracks restart from step 0.
  function advanceChain(k) {
    const ch = chain(); if (!ch.on || !ch.slots.length) return;
    play.chainPos = (play.chainPos + 1) % ch.slots.length;
    const next = load(ch.slots[play.chainPos]);
    if (next) { P = next; refreshControls(); buildGrid(); buildInspector(); buildPads(); }
    play.base = k; play.cyc = [];
  }
  function genStep(k, tBase) {
    if (k > play.base && k - play.base >= P.len && chain().on) advanceChain(k);
    const kp = k - play.base;                                  // step within the current pattern
    const sd = stepMs(), t0 = tBase + (kp % 2 ? P.swing / 100 * sd : 0);
    play.marks.push({ t: t0, k: kp });
    if (P.fx.cc >= 0) { const v = P.fx.vals[kp % P.fx.len]; if (v != null) play.pending.push({ t: t0 - 1, cc: P.fx.cc, v }); }
    const soloing = P.tracks.some(t => t.solo);
    P.tracks.forEach((tr, ti) => {
      if (tr.mute || (soloing && !tr.solo)) return;
      const st = tr.steps[kp % tr.len];
      if (!st.on || Math.random() * 100 >= st.prob) return;
      const voice = pickVoice(tr, ti);
      let vced = DrumMacros.vcedOf(voice);
      if (vced && tr.plock >= 0 && st.pl != null) {
        const [, idx, max] = PLOCK_PARAMS.find(x => x[1] === tr.plock) || [];
        if (idx != null) { vced = vced.slice(); vced[idx] = Math.round(st.pl / 127 * max); }
      }
      const t = t0 + st.nudge / 100 * sd + (P.human ? (Math.random() * 2 - 1) * P.human : 0);
      for (let j = 0; j < st.rat; j++) {
        const vel = st.acc ? 127 : st.vel;
        play.pending.push({ t: t + j * sd / st.rat, vced, note: Math.max(0, Math.min(127, tr.note + st.pitch + j * tr.ratPitch)),
          vel: Math.round(vel * (j && !st.acc ? 0.8 : 1)), gate: gateMs(tr, kp, st, sd, j), ti });
      }
    });
  }
  const producer = {
    schedule(horizon) {
      const sd = stepMs();
      while (play.next < horizon + 2 * sd + 40) { genStep(play.k, play.next); play.k++; play.next += sd; }
      play.pending.sort((a, b) => a.t - b.t);
      let n = 0;
      while (n < play.pending.length && play.pending[n].t < horizon) n++;
      for (const h of play.pending.splice(0, n)) {
        const t = Math.max(h.t, Engine.now());
        if (h.cc != null) cc(h.cc, h.v, t);
        else { Engine.hit(h.vced, h.note, h.vel, t, h.gate); play.flashes.push({ t, ti: h.ti }); }
      }
    },
  };
  function start() {
    if (play.on) return;
    Bus.dispatchEvent(new CustomEvent('stopall', { detail: 'seq' }));
    const ch = chain();
    if (ch.on && ch.slots.length) { const first = load(ch.slots[0]); if (first) { P = first; refreshControls(); buildGrid(); buildInspector(); buildPads(); } }
    Object.assign(play, { on: true, k: 0, base: 0, chainPos: 0, next: Engine.now() + 60, pending: [], marks: [], cyc: [], flashes: [] });
    Engine.start(producer); ui.play.textContent = '■ Stop'; ui.play.classList.add('on');
    requestAnimationFrame(frame);
  }
  function stop() {
    if (!play.on) return;
    play.on = false; play.pending = [];
    Engine.stop(producer); ui.play.textContent = '▶ Play'; ui.play.classList.remove('on');
    document.querySelectorAll('.cell.now').forEach(c => c.classList.remove('now'));
  }
  function frame() {
    if (!play.on) return;
    const now = Engine.now();
    let m = null;
    while (play.marks.length && play.marks[0].t <= now) m = play.marks.shift();
    play.flashes.sort((a, b) => a.t - b.t);
    while (play.flashes.length && play.flashes[0].t <= now) flashPad(play.flashes.shift().ti);
    if (m) {
      document.querySelectorAll('.cell.now').forEach(c => c.classList.remove('now'));
      P.tracks.forEach((tr, ti) => { const c = ui.cells[ti] && ui.cells[ti][m.k % tr.len]; if (c) c.classList.add('now'); });
      const f = ui.fxCells[m.k % P.fx.len]; if (f) f.classList.add('now');
      const ch = chain();
      ui.stat.textContent = (ch.on && ch.slots.length ? `chain ${play.chainPos + 1}/${ch.slots.length} (slot ${ch.slots[play.chainPos]}) · ` : '') +
        `step ${m.k % P.len + 1}/${P.len} · voice switches ${Engine.stats.switches}`;
    }
    requestAnimationFrame(frame);
  }

  /* ---------- UI ---------- */
  const ui = { cells: [], fxCells: [] };
  function modeDef() { return MODES.find(m => m[0] === mode); }
  function cellText(st, tr) {
    if (!st.on) return '';
    switch (mode) {
      case 'acc': return st.acc ? '>' : ''; case 'slide': return st.slide ? '~' : '';
      case 'gate': return st.gate != null ? st.gate : '';
      case 'vel': return st.vel; case 'prob': return st.prob < 100 ? st.prob : '';
      case 'rat': return st.rat > 1 ? '×' + st.rat : ''; case 'nudge': return st.nudge ? (st.nudge > 0 ? '→' : '←') : '';
      case 'pitch': return st.pitch ? (st.pitch > 0 ? '+' : '') + st.pitch : ''; case 'pl': return tr.plock >= 0 && st.pl != null ? st.pl : '';
      default: return (st.acc ? '>' : '') + (st.slide ? '~' : '') + (st.rat > 1 ? '×' + st.rat : '');
    }
  }
  function paintCell(ti, i) {
    const tr = P.tracks[ti], st = tr.steps[i], c = ui.cells[ti] && ui.cells[ti][i];
    if (!c) return;                                              // step is on the other page
    c.classList.toggle('on', st.on);
    c.classList.toggle('out', i >= tr.len);
    c.style.setProperty('--v', (st.acc ? 127 : st.vel) / 127);
    c.classList.toggle('acc', st.on && st.acc); c.classList.toggle('slide', st.on && st.slide);
    c.style.setProperty('--p', st.prob / 100);
    c.textContent = cellText(st, tr);
  }
  function applyBrush(ti, i, erase) {
    const tr = P.tracks[ti], st = tr.steps[i];
    if (i >= tr.len) return;
    if (mode === 'on') st.on = !erase;
    else if (mode === 'acc' || mode === 'slide') { st[mode] = !erase; if (!erase) st.on = true; }
    else if (erase) { if (mode === 'pl' || mode === 'gate') st[mode] = null; else st[mode] = { vel: 100, prob: 100, rat: 1, nudge: 0, pitch: 0 }[mode]; }
    else { st.on = true; st[mode] = brush; }
    paintCell(ti, i); save();
  }
  function buildGrid() {
    const g = ui.grid; g.innerHTML = ''; ui.cells = [];
    P.tracks.forEach((tr, ti) => {
      const row = document.createElement('div'); row.className = 'trk' + (ti === active ? ' active' : '');
      row.innerHTML = `<div class="trk-head"><button class="tname" title="Make active track"></button>
        <button class="mute ${tr.mute ? 'on' : ''}" title="Mute">M</button><button class="solo ${tr.solo ? 'on' : ''}" title="Solo">S</button></div><div class="cells"></div>`;
      row.querySelector('.tname').textContent = tr.name + (tr.voices.length > 1 ? ` (${tr.voices.length})` : '');
      row.querySelector('.tname').onclick = () => { active = ti; buildGrid(); buildInspector(); buildPads(); };
      row.querySelector('.mute').onclick = () => { tr.mute = !tr.mute; save(); buildGrid(); };
      row.querySelector('.solo').onclick = () => { tr.solo = !tr.solo; save(); buildGrid(); };
      const cells = row.querySelector('.cells'), arr = [];
      for (let i = page * PAGE; i < (page + 1) * PAGE; i++) {
        const c = document.createElement('div'); c.className = 'cell' + (i % 4 === 0 ? ' beat' : '') + (i % 16 === 0 ? ' bar' : ''); c.dataset.t = ti; c.dataset.i = i;
        c.title = 'Step ' + (i + 1); cells.appendChild(c); arr[i] = c;
      }
      ui.cells.push(arr); g.appendChild(row);
      arr.forEach((_, i) => paintCell(ti, i));
    });
    buildFxLane();
  }
  // Drag-painting across cells: first cell decides paint vs erase.
  let drag = null;
  function cellFrom(e) { const el = document.elementFromPoint(e.clientX, e.clientY); return el && el.classList.contains('cell') && el.dataset.t != null ? el : null; }
  function onDown(e) {
    const c = e.target.closest('.cell'); if (!c || c.dataset.t == null) return;
    e.preventDefault();
    const ti = +c.dataset.t, i = +c.dataset.i, st = P.tracks[ti].steps[i];
    // Shift-drag moves a step, Alt-drag copies it, with all its settings (onto any step of any track).
    if (e.shiftKey || e.altKey) { drag = { from: [ti, i], copy: e.altKey }; c.classList.add('pick'); return; }
    const erase = e.button === 2 || (mode === 'on' ? st.on : (TOGGLES.has(mode) ? st.on && st[mode] : (mode === 'pl' || mode === 'gate' ? st[mode] === brush : st.on && st[mode] === brush)));
    drag = { erase, seen: new Set([ti + ':' + i]) };
    applyBrush(ti, i, erase);
    if (active !== ti) { active = ti; ui.grid.querySelectorAll('.trk').forEach((r, k) => r.classList.toggle('active', k === ti)); buildInspector(); }
  }
  function onUp(e) {
    if (drag && drag.from) {
      document.querySelectorAll('.cell.pick').forEach(c => c.classList.remove('pick'));
      const c = cellFrom(e);
      if (c) {
        const [fti, fi] = drag.from, tti = +c.dataset.t, ti2 = +c.dataset.i;
        if (fti !== tti || fi !== ti2) {
          P.tracks[tti].steps[ti2] = { ...P.tracks[fti].steps[fi] };
          if (!drag.copy) P.tracks[fti].steps[fi] = newStep();
          save(); buildGrid();
          log(`Step ${fi + 1} (${P.tracks[fti].name}) ${drag.copy ? 'copied' : 'moved'} to step ${ti2 + 1} (${P.tracks[tti].name})`);
        }
      }
    }
    drag = null;
  }
  function onMove(e) {
    if (!drag || drag.from) return; const c = cellFrom(e); if (!c) return;
    const key = c.dataset.t + ':' + c.dataset.i; if (drag.seen.has(key)) return;
    drag.seen.add(key); applyBrush(+c.dataset.t, +c.dataset.i, drag.erase);
  }
  function buildModes() {
    ui.modes.innerHTML = '';
    MODES.forEach(([id, label]) => {
      const b = document.createElement('button'); b.textContent = label; b.className = id === mode ? 'on' : '';
      b.onclick = () => { mode = id; buildModes(); setBrushRange(); P.tracks.forEach((_, ti) => ui.cells[ti].forEach((__, i) => paintCell(ti, i))); };
      ui.modes.appendChild(b);
    });
  }
  function setBrushRange() {
    const [, , min, max, def] = modeDef();
    ui.brush.min = min; ui.brush.max = max; ui.brush.disabled = mode === 'on';
    brush = mode === 'on' ? 1 : (+ui.brush.value >= min && +ui.brush.value <= max && ui.brush.dataset.mode === mode ? +ui.brush.value : def);
    ui.brush.value = brush; ui.brush.dataset.mode = mode;
    ui.brushLbl.textContent = mode === 'on' ? '—' : brush;
  }
  function buildFxLane() {
    const L = ui.fxLane; L.innerHTML = '';
    const opts = [['— no FX automation —', -1], ...FX.flatMap(f => f.params.map(([lab, n]) => [`${f.name} ${lab} (CC${n})`, n]))];
    const head = document.createElement('div'); head.className = 'trk-head';
    const sel = document.createElement('select'); opts.forEach(([l, v]) => sel.add(new Option(l, v))); sel.value = P.fx.cc;
    sel.onchange = () => { P.fx.cc = +sel.value; save(); buildFxLane(); };
    head.appendChild(sel); L.appendChild(head);
    const cells = document.createElement('div'); cells.className = 'cells'; ui.fxCells = {};
    const max = P.fx.cc < 0 ? 127 : (FX.flatMap(f => f.params).find(p => p[1] === P.fx.cc) || [0, 0, 127])[2];
    for (let i = page * PAGE; i < (page + 1) * PAGE; i++) {
      const c = document.createElement('div'); c.className = 'cell fx' + (i % 4 === 0 ? ' beat' : '') + (i % 16 === 0 ? ' bar' : '') + (i >= P.fx.len ? ' out' : '');
      const v = P.fx.vals[i]; c.classList.toggle('on', v != null); c.textContent = v ?? ''; c.style.setProperty('--v', v == null ? 0 : v / max);
      c.onpointerdown = e => {
        e.preventDefault(); if (P.fx.cc < 0) return;
        P.fx.vals[i] = (e.button === 2 || P.fx.vals[i] != null) ? null : Math.round(fxState[P.fx.cc] ?? max / 2);
        save(); buildFxLane();
      };
      cells.appendChild(c); ui.fxCells[i] = c;
    }
    L.appendChild(cells);
    L.title = 'Click a step to write the current value of that effect knob (right panel); click again to clear.';
  }
  function buildInspector() {
    let box = document.getElementById('seqInspector');
    if (!box) { box = document.createElement('div'); box.id = 'seqInspector'; box.className = 'inspector'; ui.fxLane.after(box); }
    const tr = P.tracks[active]; if (!tr) { box.innerHTML = ''; return; }
    box.innerHTML = `<div class="row wrap">
      <input class="iname" value="" maxlength="16" aria-label="Track name">
      <label>Role <select class="irole">${ROLES.map(r => `<option>${r}</option>`).join('')}</select></label>
      <label>Note <input class="inote" type="number" min="0" max="127"></label><b class="inotename"></b>
      <label>Length <input class="ilen" type="number" min="1" max="${MAX}"></label>
      <label>Gate ms <input class="igate" type="number" min="5" max="4000"></label>
      <label title="Semitones added per ratchet repeat">Ratchet pitch <input class="irp" type="number" min="-12" max="12"></label>
      <label>Voices <select class="imode"><option value="fixed">fixed</option><option value="cycle">cycle</option><option value="random">random</option></select></label>
      <label>P-lock <select class="iplock">${PLOCK_PARAMS.map(([l, i]) => `<option value="${i}">${l}</option>`).join('')}</select></label>
      <button class="iedit" title="Add the editor's current voice to this track">+ editor voice</button>
      <button class="idup">Duplicate track</button><button class="idel danger">Delete</button></div>
      <div class="chips"></div><div class="macros"></div>`;
    const q = s => box.querySelector(s);
    q('.iname').value = tr.name; q('.irole').value = tr.role; q('.inote').value = tr.note; q('.ilen').value = tr.len; q('.igate').value = tr.gate;
    q('.irp').value = tr.ratPitch; q('.imode').value = tr.mode; q('.iplock').value = tr.plock;
    q('.inotename').textContent = noteName(tr.note);
    const upd = (fn, rebuild = true) => () => { fn(); save(); if (rebuild) buildGrid(); };
    q('.iname').oninput = upd(() => tr.name = q('.iname').value || 'Track');
    q('.irole').onchange = upd(() => tr.role = q('.irole').value, false);
    q('.inote').onchange = upd(() => { tr.note = clamp(+q('.inote').value, 0, 127); q('.inotename').textContent = noteName(tr.note); audTrack(); }, false);
    q('.ilen').onchange = upd(() => tr.len = clamp(+q('.ilen').value, 1, MAX));
    q('.igate').onchange = upd(() => tr.gate = clamp(+q('.igate').value, 5, 4000), false);
    q('.irp').onchange = upd(() => tr.ratPitch = clamp(+q('.irp').value, -12, 12), false);
    q('.imode').onchange = upd(() => tr.mode = q('.imode').value, false);
    q('.iplock').onchange = upd(() => { tr.plock = +q('.iplock').value; if (mode === 'pl') buildGrid(); }, false);
    q('.iedit').onclick = () => addVoice({ name: S.voice.name.trim(), vced: DX7.toVced(S.voice) });
    q('.idup').onclick = upd(() => { P.tracks.splice(active + 1, 0, JSON.parse(JSON.stringify(tr))); active++; buildInspector(); });
    q('.idel').onclick = () => { if (P.tracks.length < 2) return; P.tracks.splice(active, 1); active = Math.min(active, P.tracks.length - 1); save(); buildGrid(); buildInspector(); };

    const chips = q('.chips');
    if (!tr.voices.length) chips.innerHTML = '<span class="dim">No voice — load a kit, use + in the library, 🎲 Roll kit, or "+ editor voice".</span>';
    const sel = Math.min(selVoice[active] ?? 0, tr.voices.length - 1);
    tr.voices.forEach((v, vi) => {
      const c = document.createElement('span'); c.className = 'chip' + (vi === sel ? ' sel' : '');
      c.innerHTML = `<button class="play" title="Audition">▶</button><span class="vn" title="Select to shape with the macros below"></span>` +
        `<button class="edit" title="Open in the full editor (Save to track brings it back)">✎</button><button class="x" title="Remove">×</button>`;
      c.querySelector('.vn').textContent = v.name + (DrumMacros.isEdited(v) ? ' •' : '');
      c.querySelector('.vn').onclick = () => { selVoice[active] = vi; buildInspector(); };
      c.querySelector('.play').onclick = () => audVoice(tr, v);
      c.querySelector('.edit').onclick = () => openInEditor(tr, v);
      c.querySelector('.x').onclick = () => { tr.voices.splice(vi, 1); save(); buildGrid(); buildInspector(); buildPads(); };
      chips.appendChild(c);
    });
    if (sel >= 0) buildMacros(q('.macros'), tr, tr.voices[sel]);
  }

  /* ---------- drum macros panel ---------- */
  const selVoice = {};
  let audTimer = 0;
  function audVoice(tr, v, vel = 110) {
    Engine.hit(DrumMacros.vcedOf(v), tr.note, vel, Engine.now() + 5, Math.max(tr.gate, 150));
  }
  function buildMacros(box, tr, v) {
    v.macros = v.macros || {};
    const m = v.measured;
    box.innerHTML = `<div class="macro-head"><b></b><span class="dim mm"></span>
      <button class="mplay">▶ Audition</button><button class="mreset" title="Back to the voice as loaded">Reset</button>
      <button class="mbake" title="Make the current macro settings the voice's new starting point">Bake</button>
      <button class="medit">✎ Full editor</button><button class="msave" title="Save this voice (with macros) to the library as a new patch">Save to library</button></div>
      <div class="macro-grid"></div>`;
    box.querySelector('b').textContent = v.name;
    box.querySelector('.mm').textContent = m ? `measured on your FM-1: ${m.level_db} dBFS, decay ${m.decay_ms} ms at ${noteName(m.note)}` : '';
    const grid = box.querySelector('.macro-grid');
    DrumMacros.DEFS.forEach(d => {
      const cur = v.macros[d.id] ?? (d.keep != null ? d.keep : d.def);
      const w = document.createElement('label'); w.className = 'p macro'; w.title = d.title;
      w.innerHTML = `<span class="pl">${d.label}</span><input type="range" min="${d.min}" max="${d.max}" value="${cur}"><output></output>`;
      const inp = w.querySelector('input'), out = w.querySelector('output');
      out.textContent = d.fmt(+inp.value);
      inp.oninput = () => {
        const val = +inp.value;
        v.macros[d.id] = d.keep != null && val === d.keep ? null : val;
        out.textContent = d.fmt(val);
        save(); markChip(v);
        clearTimeout(audTimer); audTimer = setTimeout(() => audVoice(tr, v), 160);     // hear it while dragging
      };
      inp.ondblclick = () => { inp.value = d.keep != null ? d.keep : d.def; inp.oninput(); };
      grid.appendChild(w);
    });
    box.querySelector('.mplay').onclick = () => audVoice(tr, v);
    box.querySelector('.mreset').onclick = () => { v.macros = {}; save(); buildInspector(); audVoice(tr, v); };
    box.querySelector('.mbake').onclick = () => { v.vced = DrumMacros.vcedOf(v).slice(); v.macros = {}; delete v.measured; save(); buildInspector(); log(`Baked macros into ${v.name}`); };
    box.querySelector('.medit').onclick = () => openInEditor(tr, v);
    box.querySelector('.msave').onclick = async () => {
      const voice = DX7.fromVced(DrumMacros.vcedOf(v)); voice.name = DX7.cleanName(v.name);
      await DB.put([recFromVoice(voice, 'My drum voices', null, [tr.role])]); await refreshLib();
      log(`Saved "${v.name}" to library (My drum voices)`, 'ok');
    };
  }
  function markChip(v) {
    const tr = P.tracks[active], vi = tr ? tr.voices.indexOf(v) : -1;
    const el = document.querySelectorAll('#seqInspector .chip .vn')[vi];
    if (el) el.textContent = v.name + (DrumMacros.isEdited(v) ? ' •' : '');
  }
  function openInEditor(tr, v) {
    setVoice(DX7.fromVced(DrumMacros.vcedOf(v)), true);
    sendVoice(S.voice, true);
    setEditTarget({ label: `${tr.name} · ${v.name}`, save: vced => {
      v.vced = Array.from(vced); v.macros = {}; delete v.measured; v.name = S.voice.name.trim() || v.name;
      save(); buildGrid(); buildInspector(); buildPads(); showTab('drums');
      log(`Updated ${v.name} on ${tr.name}`, 'ok');
    } });
    showTab('editor');
  }

  const clamp = (v, a, b) => Math.max(a, Math.min(b, Math.round(v || 0)));
  function audTrack() { const tr = P.tracks[active]; if (tr && tr.voices[0]) audVoice(tr, tr.voices[selVoice[active] ?? 0] || tr.voices[0]); }
  function addVoice(v) {
    const tr = P.tracks[active]; if (!tr) return;
    if (tr.voices.length >= 8) { log('A track holds up to 8 voices', 'err'); return; }
    tr.voices.push({ name: v.name.trim(), vced: Array.from(v.vced), macros: {} });
    selVoice[active] = tr.voices.length - 1;
    save(); buildGrid(); buildInspector(); buildPads(); audTrack();
    log(`Added ${v.name.trim()} to ${tr.name}`);
  }
  function rollKit() {
    if (!S.lib.length) { log('Library is empty — import some voices first', 'err'); return; }
    const pool = role => S.lib.filter(r => r.tags.includes(role));
    P.tracks.forEach(tr => {
      const c = pool(tr.role); if (!c.length) return;
      const r = c[Math.floor(Math.random() * c.length)];
      tr.voices = [{ name: r.name.trim(), vced: Array.from(r.vced), macros: {} }];
    });
    save(); buildGrid(); buildInspector(); buildPads();
    log('Rolled kit: ' + P.tracks.map(t => t.voices[0] ? t.voices[0].name : '—').join(', '));
  }

  /* ---------- pads ---------- */
  function buildPads() {
    const box = ui.pads; if (!box) return;
    box.innerHTML = '';
    P.tracks.forEach((tr, ti) => {
      const b = document.createElement('button'); b.className = 'pad' + (ti === active ? ' active' : '');
      const v = tr.voices[0];
      b.innerHTML = `<span class="pk">${ti < 9 ? ti + 1 : ''}</span><b></b><small></small>`;
      b.querySelector('b').textContent = tr.name;
      b.querySelector('small').textContent = tr.voices.length ? v.name + (tr.voices.length > 1 ? ` +${tr.voices.length - 1}` : '') : 'no voice';
      b.onpointerdown = e => { e.preventDefault(); hitPad(ti, e.shiftKey ? 60 : 110); };
      box.appendChild(b);
    });
  }
  function hitPad(ti, vel = 110) {
    const tr = P.tracks[ti]; if (!tr || !tr.voices.length) return;
    const v = pickVoice(tr, ti);
    Engine.hit(DrumMacros.vcedOf(v), tr.note, vel, Engine.now() + 3, tr.gate);
    flashPad(ti);
    if (active !== ti) { active = ti; ui.grid.querySelectorAll('.trk').forEach((r, k) => r.classList.toggle('active', k === ti)); buildInspector();
      ui.pads.querySelectorAll('.pad').forEach((p, k) => p.classList.toggle('active', k === ti)); }
  }
  function flashPad(ti) {
    const p = ui.pads && ui.pads.children[ti]; if (!p) return;
    p.classList.remove('hit'); void p.offsetWidth; p.classList.add('hit');
  }

  /* ---------- kits ---------- */
  let factoryKits = [];
  const userKits = () => { try { return JSON.parse(localStorage.getItem('fm1.kits') || '[]').map(k => { try { return cleanKit(k); } catch { return null; } }).filter(Boolean); } catch { return []; } };
  const saveUserKits = k => { try { localStorage.setItem('fm1.kits', JSON.stringify(k)); } catch {} };
  const b64ToVced = b64 => DX7.toVced(DX7.fromVmem(Uint8Array.from(atob(b64), c => c.charCodeAt(0))));
  async function loadFactoryKits() {
    try {
      const j = await (await fetch('library/kits.json')).json();
      factoryKits = j.kits.map(k => ({ name: k.name, source: k.source, factory: true, tracks: k.tracks.map(t => ({
        name: t.name, role: t.role, note: t.note,
        gate: t.voice.measured ? Math.max(40, Math.min(800, t.voice.measured.decay_ms + 20)) : t.gate,
        voices: [{ name: t.voice.name, vced: b64ToVced(t.voice.b64), macros: t.macros || {}, measured: t.voice.measured }] })) }));
    } catch (e) { factoryKits = []; }
    fillKitSelect();
  }
  function fillKitSelect() {
    const s = ui.kitSel, cur = s.value; s.innerHTML = '';
    const g1 = document.createElement('optgroup'); g1.label = 'Factory kits (measured on your FM-1)';
    factoryKits.forEach((k, i) => g1.appendChild(new Option(`${k.name} (${k.tracks.length})`, 'f' + i)));
    const g2 = document.createElement('optgroup'); g2.label = 'My kits';
    userKits().forEach((k, i) => g2.appendChild(new Option(`${k.name} (${k.tracks.length})`, 'u' + i)));
    if (factoryKits.length) s.appendChild(g1);
    if (g2.children.length) s.appendChild(g2);
    if ([...s.options].some(o => o.value === cur)) s.value = cur;
  }
  function selectedKit() {
    const v = ui.kitSel.value; if (!v) return null;
    return v[0] === 'f' ? factoryKits[+v.slice(1)] : userKits()[+v.slice(1)];
  }
  // Put a kit onto the pattern's tracks: match by track name, then role, else append. Steps are kept.
  function applyKit(kit) {
    const used = new Set();
    kit.tracks.forEach(kt => {
      let ti = P.tracks.findIndex((t, i) => !used.has(i) && t.name.toLowerCase() === kt.name.toLowerCase());
      if (ti < 0) ti = P.tracks.findIndex((t, i) => !used.has(i) && t.role === kt.role);
      if (ti < 0) { P.tracks.push({ name: kt.name, role: kt.role, note: 60, len: 16, gate: 120, mode: 'fixed', mute: false, solo: false, ratPitch: 0, plock: -1, voices: [], steps: Array.from({ length: MAX }, newStep) }); ti = P.tracks.length - 1; }
      used.add(ti);
      const tr = P.tracks[ti];
      Object.assign(tr, { name: kt.name, role: kt.role, note: kt.note, gate: kt.gate, mode: kt.mode || 'fixed',
        voices: JSON.parse(JSON.stringify(kt.voices)) });
    });
    Object.keys(selVoice).forEach(k => delete selVoice[k]);
    save(); buildGrid(); buildInspector(); buildPads();
    log(`Loaded kit ${kit.name}: ` + kit.tracks.map(t => `${t.name}=${t.voices[0].name}`).join(', '), 'ok');
  }
  function kitFromPattern(name) {
    return { name, tracks: P.tracks.filter(t => t.voices.length).map(t => ({ name: t.name, role: t.role, note: t.note, gate: t.gate, mode: t.mode,
      voices: t.voices.map(v => ({ name: v.name, vced: Array.from(v.vced), macros: v.macros || {}, measured: v.measured })) })) };
  }
  function kitBank(kit) {
    const voices = kit.tracks.flatMap(t => t.voices).slice(0, 32).map(v => { const x = DX7.fromVced(DrumMacros.vcedOf(v)); x.name = v.name; return x; });
    return DX7.vmemSysex(voices);
  }
  function wireKits() {
    ui.kitSel = $('kitSel'); ui.pads = $('pads');
    $('kitLoad').onclick = () => { const k = selectedKit(); if (k) applyKit(k); };
    ui.kitSel.ondblclick = $('kitLoad').onclick;
    $('kitSave').onclick = () => {
      const name = (prompt('Name for this kit:', 'My kit ' + (userKits().length + 1)) || '').trim(); if (!name) return;
      const ks = userKits().filter(k => k.name !== name); ks.push(kitFromPattern(name)); saveUserKits(ks);
      fillKitSelect(); ui.kitSel.value = 'u' + (ks.length - 1); log(`Saved kit "${name}"`, 'ok');
    };
    $('kitDelete').onclick = () => {
      const v = ui.kitSel.value; if (!v || v[0] !== 'u') { log('Only your own kits can be deleted', 'err'); return; }
      const ks = userKits(), k = ks[+v.slice(1)]; if (!confirm(`Delete kit "${k.name}"?`)) return;
      ks.splice(+v.slice(1), 1); saveUserKits(ks); fillKitSelect();
    };
    $('kitSyx').onclick = () => { const k = kitFromPattern('kit'); download('fm1-drumkit.syx', kitBank(k)); log(`Exported ${k.tracks.flatMap(t => t.voices).length} kit voices as a DX7 bank (macros applied)`); };
    $('kitJson').onclick = () => { const k = kitFromPattern(prompt('Kit name:', 'My kit') || 'My kit'); download(k.name.replace(/[^\w-]+/g, '_') + '.fm1kit.json', JSON.stringify(k), 'application/json'); };
    $('kitImport').onchange = async e => {
      const f = e.target.files[0]; e.target.value = ''; if (!f) return;
      try { const k = cleanKit(JSON.parse(await f.text())); applyKit(k); const ks = userKits(); ks.push(k); saveUserKits(ks); fillKitSelect(); }   // validate + apply before saving
      catch (err) { log('Not a kit file: ' + err.message, 'err'); }
    };
    document.addEventListener('keydown', e => {
      if (S.tab !== 'drums' || typingTarget(e) || e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
      if (/^[1-9]$/.test(e.key)) { hitPad(+e.key - 1, e.shiftKey ? 60 : 110); e.preventDefault(); }
    });
    loadFactoryKits();
  }
  function refreshControls() {
    $('seqRate').value = P.rate; $('seqLen').value = P.len;
    ui.bpm.value = P.bpm; ui.swing.value = P.swing; ui.human.value = P.human; ui.glitch.value = P.glitch;
    $('seqSwingLbl').textContent = P.swing + '%'; $('seqHumanLbl').textContent = P.human + ' ms'; $('seqGlitchLbl').textContent = P.glitch + '%';
  }

  function init() {
    Object.assign(ui, { grid: $('seqGrid'), fxLane: $('fxLane'), play: $('seqPlay'), stat: $('seqStat'), modes: $('paintModes'),
      brush: $('brush'), brushLbl: $('brushLbl'), bpm: $('seqBpm'), swing: $('seqSwing'), human: $('seqHuman'), glitch: $('seqGlitch') });
    ui.play.onclick = () => play.on ? stop() : start();
    ui.bpm.onchange = () => { P.bpm = clamp(+ui.bpm.value, 30, 300); save(); };
    ui.swing.oninput = () => { P.swing = +ui.swing.value; $('seqSwingLbl').textContent = P.swing + '%'; save(); };
    ui.human.oninput = () => { P.human = +ui.human.value; $('seqHumanLbl').textContent = P.human + ' ms'; save(); };
    ui.glitch.oninput = () => { P.glitch = +ui.glitch.value; $('seqGlitchLbl').textContent = P.glitch + '%'; save(); };
    ui.brush.oninput = () => { brush = +ui.brush.value; ui.brushLbl.textContent = brush; };
    ui.grid.addEventListener('pointerdown', onDown);
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    ui.grid.addEventListener('contextmenu', e => e.preventDefault());
    ui.fxLane.addEventListener('contextmenu', e => e.preventDefault());
    $('rollKit').onclick = rollKit;
    $('seqClear').onclick = () => { if (!confirm('Clear all steps?')) return; P.tracks.forEach(t => t.steps = Array.from({ length: MAX }, newStep)); P.fx.vals.fill(null); save(); buildGrid(); };
    $('seqAddTrack').onclick = () => {
      P.tracks.push({ name: 'Track ' + (P.tracks.length + 1), role: 'perc', note: 60, len: 16, gate: 120, mode: 'fixed', mute: false, solo: false, ratPitch: 0, plock: -1, voices: [], steps: Array.from({ length: MAX }, newStep) });
      active = P.tracks.length - 1; save(); buildGrid(); buildInspector();
    };
    for (let i = 1; i <= 16; i++) $('patSlot').add(new Option('Slot ' + i, i));
    // pages, rate, pattern length, chain
    const pages = $('seqPage');
    const showPage = p => { page = p; pages.querySelectorAll('button').forEach((b, k) => b.classList.toggle('on', k === p)); buildGrid(); };
    ['1–32', '33–64'].forEach((l, k) => { const b = document.createElement('button'); b.textContent = l; b.onclick = () => showPage(k); pages.appendChild(b); });
    Object.keys(RATES).forEach(r => $('seqRate').add(new Option(r, r)));
    $('seqRate').onchange = e => { P.rate = e.target.value; save(); };
    $('seqLen').onchange = e => { P.len = num(+e.target.value, 1, MAX, 16); e.target.value = P.len; save(); };
    const ch = chain(); $('chainOn').checked = ch.on; $('chainSlots').value = ch.slots.join(' ');
    const saveChain = () => setChain({ on: $('chainOn').checked, slots: $('chainSlots').value.split(/[\s,]+/).map(Number).filter(n => n >= 1 && n <= 16) });
    $('chainOn').onchange = saveChain; $('chainSlots').onchange = saveChain;
    $('patSave').onclick = () => { save($('patSlot').value); log('Pattern saved to slot ' + $('patSlot').value, 'ok'); };
    $('patLoad').onclick = () => { const p = load($('patSlot').value); if (!p) { log('Slot is empty', 'err'); return; } P = p; active = 0; save(); refreshControls(); buildGrid(); buildInspector(); };
    $('patExport').onclick = () => download('fm1-pattern.json', JSON.stringify(P), 'application/json');
    $('patImport').onchange = async e => {
      const f = e.target.files[0]; e.target.value = ''; if (!f) return;
      try { P = normalize(JSON.parse(await f.text())); active = 0; save(); refreshControls(); buildGrid(); buildInspector(); log('Pattern imported', 'ok'); }
      catch (err) { log('Not a pattern file: ' + err.message, 'err'); }
    };
    Bus.addEventListener('addvoice', e => { if (S.tab === 'drums') addVoice(e.detail); });
    Bus.addEventListener('transport', e => { if (e.detail === 'drums') play.on ? stop() : start(); });
    Bus.addEventListener('stopall', e => { if (e.detail !== 'seq') stop(); });
    wireKits();
    refreshControls(); buildModes(); setBrushRange(); showPage(0); buildInspector(); buildPads();
  }
  init();

  // For the MIDI player: GM drum note -> this kit's track (by role).
  function trackForGm(n) {
    const role = [35, 36].includes(n) ? 'kick' : [37, 38, 40].includes(n) ? 'snare' : [42, 44, 46, 49, 51, 52, 53, 55, 57, 59].includes(n) ? 'hat'
      : [41, 43, 45, 47, 48, 50].includes(n) ? 'tom' : 'perc';
    const cands = P.tracks.filter(t => t.role === role && t.voices.length);
    if (!cands.length) return null;
    if (role === 'hat' && [46, 49, 55, 57].includes(n)) return cands.find(t => /open|crash|cym/i.test(t.name)) || cands[cands.length - 1];
    return cands[0];
  }
  const voiceVced = v => DrumMacros.vcedOf(v);
  return { start, stop, trackForGm, voiceVced, get pattern() { return P; }, isPlaying: () => play.on };
})();
