// FM-1 Workbench core: Web MIDI ports, IndexedDB patch library, DX7 editor, generators, morph, FX, keyboard, tabs.
// Real-time voice changes go through Engine (param diffs) — see engine.js for why.
'use strict';
const $ = id => document.getElementById(id);
const S = {
  midi: null, inp: null, fxCh: 1, seen: new Set(),
  voice: DX7.initVoice(), undo: [], A: null, B: null,
  lib: [], filtered: [], sel: -1, picks: new Set(),
  octave: 4, vel: 100, held: new Map(), tab: 'editor',
};
const Bus = new EventTarget();     // app-wide events: 'addvoice' (library + button), 'tab'

/* ---------------- log ---------------- */
function log(msg, cls = '') {
  const el = $('log'), line = document.createElement('div');
  line.textContent = new Date().toLocaleTimeString() + '  ' + msg;
  if (cls) line.className = cls;
  el.prepend(line);
  while (el.childNodes.length > 80) el.lastChild.remove();
}

/* ---------------- MIDI ---------------- */
async function connect() {
  if (!navigator.requestMIDIAccess) { log('Web MIDI is not available in this browser — playing through the Software FM-1.'); fillPorts(); return; }
  try {
    S.midi = await navigator.requestMIDIAccess({ sysex: true });
  } catch (e) { log('MIDI access denied: ' + e.message + ' (SysEx permission is required) — playing through the Software FM-1.', 'err'); fillPorts(); return; }
  S.midi.onstatechange = fillPorts;
  fillPorts();
}
// Outputs: the Software FM-1 (always available) plus the Web MIDI ports. A connected FM-1 wins; otherwise the software one.
const outputs = () => new Map([[SoftFM1.id, SoftFM1], ...(S.midi ? S.midi.outputs : new Map())]);
function setOut(p) {
  p = p || null;
  if (Engine.out !== p) { Engine.forgetDevice(); if (Engine.out && Engine.out.isSoft) SoftFM1.reset(); }
  Engine.out = p;
  Engine.timing = p && p.isSoft ? 'timestamp' : 'jit';     // the software synth schedules to the sample
  if (p && p.isSoft) {
    SoftFM1.setFxChannel(S.fxCh);
    SoftFM1.start().catch(e => log('Software FM-1 unavailable: ' + e.message, 'err'));
    Object.entries(fxState).forEach(([n, v]) => cc(+n, v));   // the software effects start from the panel's settings
  }
}
function fillPorts() {
  // keep the current choice, but switch to a hardware FM-1 when one is plugged in
  const fill = (sel, ports, want, fallback) => {
    const prev = sel.value, list = [...ports.values()];
    sel.innerHTML = '';
    for (const p of list) sel.add(new Option(p.name, p.id));
    const hw = list.find(p => /FM-1/i.test(p.name) && !p.isSoft);
    const pick = (hw && !S.seen.has(hw.id) ? hw : list.find(p => p.id === prev)) || hw || fallback;
    if (pick) sel.value = pick.id;
    return want(pick);
  };
  fill($('outSel'), outputs(), setOut, SoftFM1);
  fill($('inSel'), S.midi ? S.midi.inputs : new Map(), p => {
    if (S.inp) S.inp.onmidimessage = null;
    S.inp = p || null;
    if (S.inp) S.inp.onmidimessage = onMidiIn;
  });
  const out = Engine.out, soft = out && out.isSoft, ok = out && /FM-1/i.test(out.name);
  $('midiStatus').textContent = soft ? 'Software FM-1' : ok ? 'MIDI: ' + out.name : out ? 'MIDI: ' + out.name + ' (not FM-1?)' : 'MIDI: no output';
  $('midiStatus').className = 'pill ' + (ok ? 'on' : 'off') + (soft ? ' soft' : '');
  S.seen = new Set([...outputs().keys(), ...(S.midi ? S.midi.inputs.keys() : [])]);
}
const send = (bytes, t) => Engine.send(bytes, t);
function onMidiIn(e) {
  const [st, d1, d2] = e.data, type = st & 0xF0;
  if (type === 0x90 && d2 > 0) keyVisual(d1, true);
  else if (type === 0x80 || (type === 0x90 && d2 === 0)) keyVisual(d1, false);
}
const manual = new Map();
const noteOn = (n, v = S.vel) => { manual.set(n, Engine.note(n, v, 0, null)); keyVisual(n, true); };
const noteOff = n => { Engine.noteOff(n, 0, manual.get(n)); manual.delete(n); keyVisual(n, false); };
function panic() {
  Engine.panic();
  S.held.clear(); manual.clear();
  document.querySelectorAll('.key.down').forEach(k => k.classList.remove('down'));
  log('Panic: note-off sent for all 128 keys');
}

// Live editor edits: coalesce per param, send through the Engine so its device state stays true.
const pendingParams = new Map();
let paramTimer = null, voiceTimer = null;
function sendParam(p, val) {
  if (!$('liveSend').checked) return;
  pendingParams.set(p, val);
  if (!paramTimer) paramTimer = setTimeout(() => {
    for (const [pp, vv] of pendingParams) Engine.setParam(pp, vv);
    pendingParams.clear(); paramTimer = null;
  }, 15);
}
function sendVoice(v = S.voice, immediate = false) {
  const go = () => { voiceTimer = null; Engine.setVoice(DX7.toVced(v), 0, { opMask: DX7.opMaskValue(v.on), withName: true }); };
  if (immediate) { clearTimeout(voiceTimer); go(); return; }
  if (!voiceTimer) voiceTimer = setTimeout(go, 30);
}
function audition(v, note = 12 * (S.octave + 1)) {
  Engine.hit(DX7.toVced(v), note, S.vel, Engine.now() + 5, 450, { opMask: DX7.opMaskValue(v.on), withName: true });
}

/* ---------------- IndexedDB library ---------------- */
const DB = {
  db: null,
  open() {
    return new Promise((res, rej) => {
      const r = indexedDB.open('fm1-workbench', 1);
      r.onupgradeneeded = () => r.result.createObjectStore('voices', { keyPath: 'id' });
      r.onsuccess = () => { this.db = r.result; res(); };
      r.onerror = () => rej(r.error);
    });
  },
  tx(mode) { return this.db.transaction('voices', mode).objectStore('voices'); },
  all() { return new Promise(res => { const r = this.tx('readonly').getAll(); r.onsuccess = () => res(r.result); }); },
  put(recs) { return new Promise(res => { const s = this.tx('readwrite'); recs.forEach(r => s.put(r)); s.transaction.oncomplete = res; }); },
  del(ids) { return new Promise(res => { const s = this.tx('readwrite'); ids.forEach(id => s.delete(id)); s.transaction.oncomplete = res; }); },
};
// Record id = hash of the sound bytes + hash of the full voice incl. name: exact re-imports are skipped,
// while the same sound under another name is kept as its own entry.
function hashBytes(d) { let h = 2166136261; for (const x of d) { h ^= x; h = Math.imul(h, 16777619); } return (h >>> 0).toString(36); }
function recFromVoice(v, bank, slot, tags) {
  const vced = Uint8Array.from(DX7.toVced(v));
  return { id: hashBytes(vced.subarray(0, 145)) + '-' + hashBytes(vced), name: v.name.trim() || '(unnamed)', bank, slot: slot ?? null,
    vced, tags: tags || guessTags(v.name), fav: false, added: Date.now() };
}
const TAG_RX = [['kick', /\b(KICK|KIK|BD|B\.?DRUM|BASS ?DRUM)/], ['snare', /(SNARE|SNR|\bSD\b|RIMSHOT|\bRIM)/], ['hat', /(HAT|\bHH\b|HI-?H|CYMB|CRASH|RIDE)/],
  ['tom', /(\bTOMS?\b|TOM-|TIMPANI|TIMBAL|CONGA|BONGO|TABLA)/], ['perc', /(PERC|CLAP|COWB|CLAVE|WOOD|BLOCK|SHAK|TAMB|MARIMB|XYLO|GLOCK|DRUM|NOISE|GONG)/],
  ['bass', /(BASS|\bBS\b)/], ['keys', /(PIANO|PNO|E\.?P\b|RHODE|CLAV|HARPS|KEY)/], ['bell', /(BELL|CHIME|GLASS)/]];
const guessTags = name => { const u = (name || '').toUpperCase(); return TAG_RX.filter(([, rx]) => rx.test(u)).map(([t]) => t); };
const DRUM_TAGS = ['kick', 'snare', 'hat', 'tom', 'perc'];

async function refreshLib() {
  S.lib = (await DB.all()).sort((a, b) => a.bank.localeCompare(b.bank) || (a.slot ?? 99) - (b.slot ?? 99) || a.name.localeCompare(b.name));
  S.lib.forEach(r => { r.lname = (r.name + ' ' + r.bank).toLowerCase(); if (!(r.vced instanceof Uint8Array)) r.vced = Uint8Array.from(r.vced); r.tags = r.tags || []; });
  const banks = [...new Set(S.lib.map(r => r.bank))];
  const bf = $('bankFilter'), cur = bf.value;
  bf.innerHTML = '<option value="">All banks (' + banks.length + ')</option>';
  banks.forEach(b => bf.add(new Option(b, b)));
  bf.value = banks.includes(cur) ? cur : '';
  const counts = {};
  S.lib.forEach(r => r.tags.forEach(t => counts[t] = (counts[t] || 0) + 1));
  const cf = $('catFilter'), cc = cf.value;
  cf.innerHTML = '<option value="">All categories</option>';
  const drumN = S.lib.filter(r => r.tags.some(t => DRUM_TAGS.includes(t))).length;
  if (drumN) cf.add(new Option(`Drums & perc (${drumN})`, '@drums'));
  Object.keys(counts).sort().forEach(t => cf.add(new Option(`${t} (${counts[t]})`, t)));
  cf.value = [...cf.options].some(o => o.value === cc) ? cc : '';
  renderList();
}
function libQuery({ q = '', bank = '', cat = '', fav = false } = {}) {
  q = q.trim().toLowerCase();
  return S.lib.filter(r => (!bank || r.bank === bank) && (!fav || r.fav) && (!q || r.lname.includes(q)) &&
    (!cat || (cat === '@drums' ? r.tags.some(t => DRUM_TAGS.includes(t)) : r.tags.includes(cat))));
}
function renderList() {
  S.filtered = libQuery({ q: $('search').value, bank: $('bankFilter').value, cat: $('catFilter').value, fav: $('favOnly').checked });
  $('libCount').textContent = S.filtered.length + ' / ' + S.lib.length;
  const ul = $('voiceList');
  ul.innerHTML = '';
  const frag = document.createDocumentFragment();
  S.filtered.slice(0, 1500).forEach((r, i) => {
    const li = document.createElement('li');
    li.dataset.i = i;
    if (i === S.sel) li.className = 'sel';
    li.innerHTML = `<input type="checkbox" class="pick" ${S.picks.has(r.id) ? 'checked' : ''} title="Pick for bank">` +
      `<button class="star ${r.fav ? 'on' : ''}" title="Favorite">★</button>` +
      `<span class="nm"></span><span class="bk"></span><button class="add" title="Add to active drum track / player channel">+</button>`;
    li.querySelector('.nm').textContent = r.name;
    li.querySelector('.bk').textContent = (r.tags[0] ? r.tags[0] + ' · ' : '') + r.bank.split('/').pop() + (r.slot != null ? ' #' + (r.slot + 1) : '');
    li.title = r.bank;
    frag.appendChild(li);
  });
  ul.appendChild(frag);
  if (S.filtered.length > 1500) { const li = document.createElement('li'); li.className = 'more'; li.textContent = `…${S.filtered.length - 1500} more — refine search`; ul.appendChild(li); }
  $('pickCount').textContent = S.picks.size;
}
async function importFiles(files) {
  let n = 0, dup = 0;
  const known = new Set(S.lib.map(r => r.id));
  for (const f of files) {
    const buf = new Uint8Array(await f.arrayBuffer());
    const found = DX7.parseSyx(buf);
    if (!found.length) { log(`${f.name}: no DX7 voice/bank data found`, 'err'); continue; }
    const bank = f.name.replace(/\.[^.]+$/, '');
    const recs = found.map(x => recFromVoice(x.voice, bank, x.slot)).filter(r => known.has(r.id) ? (dup++, false) : (known.add(r.id), true));
    await DB.put(recs);
    n += recs.length;
    log(`${f.name}: ${found.length} voice(s), ${recs.length} new`);
  }
  log(`Imported ${n} voices` + (dup ? ` (${dup} duplicates skipped)` : ''), 'ok');
  await refreshLib();
}
async function importBundled() {
  const btn = $('loadBundled');
  btn.disabled = true; btn.textContent = 'Loading…';
  try {
    const res = await fetch('library/index.json');
    if (!res.ok) throw new Error('No built library (library/index.json). Drop .syx files here, or build one: see "Voices" in the README.');
    const lib = await res.json();
    const known = new Set(S.lib.map(r => r.id));
    const recs = [];
    for (const [name, bi, slot, b64, tags] of lib.voices) {
      const raw = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      const r = recFromVoice(DX7.fromVmem(raw), lib.banks[bi], slot, tags);
      if (!known.has(r.id)) { known.add(r.id); recs.push(r); }
    }
    for (let i = 0; i < recs.length; i += 5000) { await DB.put(recs.slice(i, i + 5000)); btn.textContent = `Saving ${Math.min(i + 5000, recs.length)}/${recs.length}…`; }
    log(`Library: ${lib.voices.length} voices, ${recs.length} new`, 'ok');
    await refreshLib();
  } catch (e) { log(e.message, 'err'); }
  btn.disabled = false; btn.textContent = 'Load built library';
}
function selectRow(i, play = true) {
  if (i < 0 || i >= S.filtered.length) return;
  S.sel = i;
  document.querySelectorAll('#voiceList li.sel').forEach(li => li.classList.remove('sel'));
  const li = document.querySelector(`#voiceList li[data-i="${i}"]`);
  if (li) { li.classList.add('sel'); li.scrollIntoView({ block: 'nearest' }); }
  if (play) audition(DX7.fromVced(S.filtered[i].vced));
}
// When the editor is working on a voice that belongs somewhere else (a drum track), show 'Save to …'.
function setEditTarget(t) {
  S.editTarget = t || null;
  const b = $('saveToTarget');
  b.hidden = !t; b.textContent = t ? 'Save to ' + t.label : '';
}
function loadRow(i) {
  const r = S.filtered[i]; if (!r) return;
  setEditTarget(null);
  setVoice(DX7.fromVced(r.vced), true);
  sendVoice(S.voice, true);
  showTab('editor');
  log('Loaded ' + r.name.trim() + ' into editor');
}
function download(name, data, type = 'application/octet-stream') {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([typeof data === 'string' ? data : new Uint8Array(data)], { type }));
  a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
function pickedVoices() {
  const byId = new Map(S.lib.map(r => [r.id, r]));
  return [...S.picks].map(id => byId.get(id)).filter(Boolean).slice(0, 32).map(r => DX7.fromVced(r.vced));
}

/* ---------------- editor ---------------- */
const OP_LAYOUT = [
  ['OL','Level'], ['FC','Coarse'], ['FF','Fine'], ['DT','Detune', v => (v - 7 > 0 ? '+' : '') + (v - 7)], ['MODE','Fixed', v => v ? 'fixed' : 'ratio'],
  ['KVS','Vel sens'], ['AMS','AM sens'], ['RS','Rate scl'],
  ['R1','R1'], ['R2','R2'], ['R3','R3'], ['R4','R4'], ['L1','L1'], ['L2','L2'], ['L3','L3'], ['L4','L4'],
  ['BP','Break pt', v => noteName(v + 21)], ['LD','L depth'], ['RD','R depth'],
  ['LC','L curve', v => ['-LIN','-EXP','+EXP','+LIN'][v]], ['RC','R curve', v => ['-LIN','-EXP','+EXP','+LIN'][v]],
];
const G_LAYOUT = [
  ['FB','Feedback'], ['TRNP','Transpose', v => noteName(v + 36)], ['OKS','Osc sync', v => v ? 'on' : 'off'],
  ['LFW','LFO wave', v => ['TRI','SAW↓','SAW↑','SQR','SINE','S/H'][v]], ['LFS','LFO speed'], ['LFD','LFO delay'],
  ['LPMD','LFO pitch'], ['LAMD','LFO amp'], ['LPMS','Pitch sens'], ['LFKS','LFO sync', v => v ? 'on' : 'off'],
  ['PR1','PEG R1'], ['PR2','PEG R2'], ['PR3','PEG R3'], ['PR4','PEG R4'],
  ['PL1','PEG L1'], ['PL2','PEG L2'], ['PL3','PEG L3'], ['PL4','PEG L4'],
];
const NOTE_NAMES = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'];
const noteName = n => NOTE_NAMES[n % 12] + (Math.floor(n / 12) - 1);

function slider(key, label, max, fmt, get, onInput, undoable = true) {
  const w = document.createElement('label');
  w.className = 'p';
  w.innerHTML = `<span class="pl">${label}</span><input type="range" min="0" max="${max}"><output></output>`;
  const inp = w.querySelector('input'), out = w.querySelector('output');
  const show = () => { inp.value = get(); out.textContent = fmt ? fmt(+inp.value) : inp.value; };
  if (undoable) { inp.addEventListener('pointerdown', pushUndo); inp.addEventListener('keydown', pushUndo); }
  inp.addEventListener('input', () => { onInput(+inp.value); out.textContent = fmt ? fmt(+inp.value) : inp.value; });
  w.refresh = show;
  return w;
}
const refreshers = [];
function buildEditor() {
  const gp = $('globalParams');
  G_LAYOUT.forEach(([k, label, fmt]) => {
    const s = slider(k, label, DX7.G_MAX[k], fmt, () => S.voice.g[k], v => { S.voice.g[k] = v; sendParam(DX7.paramIndex(null, k), v); });
    gp.appendChild(s); refreshers.push(s.refresh);
  });
  const ops = $('ops');
  for (let n = 1; n <= 6; n++) {
    const card = document.createElement('div');
    card.className = 'op'; card.id = 'op' + n;
    card.innerHTML = `<div class="op-head"><label class="chk"><input type="checkbox" class="opOn"> OP${n}</label><span class="role"></span><span class="freq"></span></div><canvas class="env" width="220" height="46"></canvas><div class="param-grid"></div>`;
    const grid = card.querySelector('.param-grid');
    OP_LAYOUT.forEach(([k, label, fmt]) => {
      const s = slider(k, label, DX7.OP_MAX[k], fmt, () => S.voice.ops[n-1][k], v => {
        S.voice.ops[n-1][k] = v; sendParam(DX7.paramIndex(n, k), v);
        if (/^[RL]\d$/.test(k)) drawEnv(n);
        if (['FC','FF','MODE'].includes(k)) card.querySelector('.freq').textContent = DX7.freqLabel(S.voice.ops[n-1]);
      });
      grid.appendChild(s); refreshers.push(s.refresh);
    });
    card.querySelector('.opOn').addEventListener('change', e => {
      pushUndo(); S.voice.on[n-1] = e.target.checked; card.classList.toggle('muted', !e.target.checked);
      sendParam(155, DX7.opMaskValue(S.voice.on)); drawAlg();
    });
    ops.appendChild(card);
  }
}
function refreshEditor() {
  $('voiceName').value = S.voice.name.trimEnd();
  $('algNum').value = S.voice.g.ALG + 1;
  refreshers.forEach(f => f());
  const alg = DX7.ALGS[S.voice.g.ALG];
  for (let n = 1; n <= 6; n++) {
    const card = $('op' + n);
    card.querySelector('.opOn').checked = S.voice.on[n-1];
    card.classList.toggle('muted', !S.voice.on[n-1]);
    card.classList.toggle('carrier', alg.carriers.includes(n));
    card.querySelector('.role').textContent = alg.carriers.includes(n) ? 'carrier' : 'mod → ' + alg.edges.filter(([f]) => f === n).map(([, t]) => t).join(',');
    card.querySelector('.freq').textContent = DX7.freqLabel(S.voice.ops[n-1]);
    drawEnv(n);
  }
  drawAlg();
}
function setVoice(v, resetUndo = false) {
  S.voice = DX7.clampVoice(v);
  if (resetUndo) S.undo = [];
  refreshEditor();
  try { localStorage.setItem('fm1.voice', JSON.stringify(S.voice)); } catch {}
}
function pushUndo() {
  S.undo.push(DX7.clone(S.voice));
  if (S.undo.length > 50) S.undo.shift();
}

function drawEnv(n) {
  const c = $('op' + n).querySelector('canvas'), x = c.getContext('2d'), o = S.voice.ops[n-1];
  const W = c.width, H = c.height;
  x.clearRect(0, 0, W, H);
  const seg = r => 6 + (99 - r) * 0.5;                // rough time per stage from rate
  const lv = l => H - 3 - (l / 99) * (H - 6);
  const pts = [[0, lv(o.L4)]]; let t = 0;
  [['R1','L1'],['R2','L2'],['R3','L3']].forEach(([r, l]) => { t += seg(o[r]); pts.push([t, lv(o[l])]); });
  t += 30; pts.push([t, lv(o.L3)]);                    // sustain hold
  t += seg(o.R4); pts.push([t, lv(o.L4)]);
  const sx = (W - 4) / t;
  x.strokeStyle = getComputedStyle(document.body).getPropertyValue('--accent'); x.lineWidth = 1.5;
  x.beginPath(); pts.forEach(([px, py], i) => i ? x.lineTo(2 + px * sx, py) : x.moveTo(2 + px * sx, py)); x.stroke();
}
function drawAlg() {
  const alg = DX7.ALGS[S.voice.g.ALG], svg = $('algSvg');
  const mods = o => alg.edges.filter(([, t]) => t === o).map(([f]) => f).sort((a, b) => b - a);
  const pos = {}, depth = {};
  let x = 0;
  const place = (o, d) => {
    if (o in pos) return;
    depth[o] = d;
    const ms = mods(o).filter(m => !(m in pos));
    if (!ms.length) { pos[o] = x++; return; }
    const start = x;
    ms.forEach(m => place(m, d + 1));
    const xs = ms.map(m => pos[m]);
    pos[o] = xs.length ? (Math.min(...xs) + Math.max(...xs)) / 2 : start;
    if (x === start) x++;
  };
  alg.carriers.forEach(c => place(c, 0));
  const maxD = Math.max(...Object.values(depth)), cols = Math.max(x, 1);
  const bw = 30, bh = 22, W = 240, H = 170;
  const cx = o => (W / (cols + 1)) * (pos[o] + 1), cy = o => H - 20 - depth[o] * Math.min(38, (H - 40) / (maxD + 1)) - bh / 2;
  let s = '';
  alg.edges.forEach(([f, t]) => s += `<line x1="${cx(f)}" y1="${cy(f) + bh / 2}" x2="${cx(t)}" y2="${cy(t) - bh / 2}" class="edge"/>`);
  const fb = alg.fb;
  s += `<path d="M${cx(fb) + bw / 2} ${cy(fb)} h8 v${-bh / 2 - 6} h${-bw / 2 - 8} v6" class="fb"/>`;
  s += `<line x1="${cx(alg.carriers[0]) - 10}" y1="${H - 8}" x2="${cx(alg.carriers[alg.carriers.length - 1]) + 10}" y2="${H - 8}" class="bus"/>`;
  alg.carriers.forEach(c => s += `<line x1="${cx(c)}" y1="${cy(c) + bh / 2}" x2="${cx(c)}" y2="${H - 8}" class="edge"/>`);
  for (let o = 1; o <= 6; o++) {
    const car = alg.carriers.includes(o), off = !S.voice.on[o-1];
    s += `<g class="opbox ${car ? 'car' : ''} ${off ? 'off' : ''}"><rect x="${cx(o) - bw / 2}" y="${cy(o) - bh / 2}" width="${bw}" height="${bh}" rx="4"/>` +
         `<text x="${cx(o)}" y="${cy(o) + 4}">${o}</text></g>`;
  }
  svg.innerHTML = s;
}

/* ---------------- generators ---------------- */
const rnd = (a, b) => a + Math.floor(Math.random() * (b - a + 1));
const pickOne = a => a[Math.floor(Math.random() * a.length)];
function randomVoice(style) {
  const v = DX7.initVoice();
  const algPool = { any: [...Array(32).keys()], keys: [4, 5, 0, 1, 20, 21], bass: [0, 1, 15, 16, 17], pad: [4, 5, 21, 22, 30], bell: [4, 5, 28, 29, 30], pluck: [0, 1, 2, 4, 15] }[style];
  v.g.ALG = pickOne(algPool);
  v.g.FB = style === 'bass' ? rnd(3, 7) : rnd(0, 6);
  const alg = DX7.ALGS[v.g.ALG];
  const ratios = style === 'bell' ? [1, 2, 3, 3, 4, 5, 7, 9, 11, 14] : [1, 1, 1, 2, 2, 3, 4, 0, 5, 6, 8];
  v.ops.forEach((o, i) => {
    const n = i + 1, car = alg.carriers.includes(n);
    o.FC = car ? pickOne([1, 1, 1, 2, style === 'bass' ? 0 : 1]) : pickOne(ratios);
    o.FF = style === 'bell' && Math.random() < 0.4 ? rnd(0, 60) : (Math.random() < 0.15 ? rnd(0, 20) : 0);
    o.DT = rnd(4, 10);
    o.OL = car ? rnd(88, 99) : rnd(style === 'pad' ? 40 : 55, 92);
    o.KVS = rnd(1, 5); o.RS = rnd(0, 4);
    const sustain = { keys: rnd(0, 70), bass: rnd(0, 80), pad: rnd(80, 99), bell: 0, pluck: 0, any: rnd(0, 99) }[style];
    o.R1 = style === 'pad' ? rnd(30, 70) : rnd(80, 99);
    o.R2 = style === 'pluck' ? rnd(55, 80) : rnd(25, 80);
    o.R3 = rnd(20, 70);
    o.R4 = style === 'pad' ? rnd(30, 60) : style === 'bell' ? rnd(20, 45) : rnd(45, 80);
    o.L1 = 99; o.L2 = car ? rnd(Math.max(sustain, 60), 99) : rnd(40, 99); o.L3 = car ? sustain : rnd(0, 90); o.L4 = 0;
  });
  v.g.LFS = rnd(20, 45); v.g.LPMD = style === 'pad' ? rnd(0, 8) : 0; v.g.LPMS = rnd(1, 3);
  v.name = { any: 'RANDOM', keys: 'RND KEYS', bass: 'RND BASS', pad: 'RND PAD', bell: 'RND BELL', pluck: 'RND PLUCK' }[style] + ' ' + rnd(10, 99);
  return DX7.clampVoice(v);
}
function mutateVoice(src, amt) {
  const v = DX7.clone(src), k = amt / 100;
  const nudge = (val, max) => Math.max(0, Math.min(max, Math.round(val + (Math.random() * 2 - 1) * max * k)));
  v.ops.forEach(o => ['OL','R1','R2','R3','R4','L2','L3','FF','KVS','DT'].forEach(f => { if (Math.random() < 0.5) o[f] = nudge(o[f], DX7.OP_MAX[f]); }));
  v.ops.forEach(o => { if (Math.random() < k * 0.5) o.FC = Math.max(0, Math.min(31, o.FC + pickOne([-1, 1]))); });
  if (Math.random() < 0.5) v.g.FB = nudge(v.g.FB, 7);
  if (Math.random() < k * 0.3) v.g.ALG = rnd(0, 31);
  return DX7.clampVoice(v);
}
const MORPH_SNAP = new Set(['ALG','OKS','LFKS','LFW','MODE','FC','LC','RC']);
function morphVoice(a, b, t) {
  const v = DX7.clone(a), lerp = (x, y, f) => MORPH_SNAP.has(f) ? (t < 0.5 ? x : y) : x + (y - x) * t;
  v.ops.forEach((o, i) => DX7.OP_FIELDS.forEach(f => o[f] = lerp(a.ops[i][f], b.ops[i][f], f)));
  DX7.G_FIELDS.forEach(f => v.g[f] = lerp(a.g[f], b.g[f], f));
  v.on = (t < 0.5 ? a : b).on.slice();
  v.name = 'MORPH ' + Math.round(t * 100);
  return DX7.clampVoice(v);
}

/* ---------------- effects ---------------- */
const FX = [
  { name: 'Filter', cc: 0, params: [['Type', 1, 2, 0, v => ['LP','BP','HP'][v]], ['Cutoff', 2, 107, 80], ['Q', 3, 10, 2]] },
  { name: 'Reverb', cc: 4, params: [['Type', 5, 2, 1, v => ['Room','Hall','Plate'][v]], ['Decay', 6, 100, 50], ['Mix', 7, 100, 30]] },
  { name: 'Delay', cc: 8, params: [['Decay', 9, 100, 40], ['Rate', 10, 100, 40], ['Mix', 11, 100, 30]] },
  { name: 'Distortion', cc: 12, params: [['Gain', 13, 100, 30], ['Tone', 14, 100, 50], ['Level', 15, 100, 50]] },
  { name: 'Chorus', cc: 16, params: [['Freq', 17, 100, 30], ['Depth', 18, 100, 50], ['Mix', 19, 100, 40]] },
  { name: 'Phaser', cc: 20, params: [['Freq', 21, 100, 30], ['Depth', 22, 100, 50], ['Mix', 23, 100, 40]] },
];
const fxState = {};
const cc = (n, v, t, ch = S.fxCh) => send([0xB0 | ch, n, v], t);
const fxSliders = {};
function buildFx() {
  try { Object.assign(fxState, JSON.parse(localStorage.getItem('fm1.fx') || '{}')); } catch {}
  const root = $('fx');
  FX.forEach(fx => {
    const box = document.createElement('div');
    box.className = 'fxbox';
    if (!(fx.cc in fxState)) fxState[fx.cc] = 0;
    box.innerHTML = `<label class="chk fxname"><input type="checkbox"> ${fx.name}</label>`;
    const on = box.querySelector('input');
    on.checked = !!fxState[fx.cc];
    box.classList.toggle('active', on.checked);
    on.addEventListener('change', () => { fxState[fx.cc] = on.checked ? 1 : 0; box.classList.toggle('active', on.checked); cc(fx.cc, fxState[fx.cc]); saveFx(); });
    fx.params.forEach(([label, n, max, def, fmt]) => {
      if (!(n in fxState)) fxState[n] = def;
      const s = slider('cc' + n, label, max, fmt, () => fxState[n], v => { fxState[n] = v; cc(n, v); saveFx(); }, false);
      s.refresh(); box.appendChild(s); fxSliders[n] = s;
    });
    root.appendChild(box);
  });
}
const saveFx = () => { try { localStorage.setItem('fm1.fx', JSON.stringify(fxState)); } catch {} };
function fxSendAll() { Object.entries(fxState).forEach(([n, v]) => cc(+n, v)); log('Sent all effect settings on ch ' + (S.fxCh + 1)); }

/* ---------------- keyboard ---------------- */
const KEYMAP = 'awsedftgyhujkolp;';
function buildKeys() {
  const k = $('keys');
  k.innerHTML = '';
  for (let i = 0; i < 25; i++) {
    const n = 12 * (S.octave + 1) + i, black = [1, 3, 6, 8, 10].includes(i % 12);
    const el = document.createElement('div');
    el.className = 'key ' + (black ? 'black' : 'white');
    el.dataset.n = n;
    if (i % 12 === 0) el.textContent = noteName(n);
    el.addEventListener('pointerdown', e => { el.setPointerCapture(e.pointerId); noteOn(n); });
    el.addEventListener('pointerup', () => noteOff(n));
    el.addEventListener('pointercancel', () => noteOff(n));
    k.appendChild(el);
  }
  $('octLbl').textContent = S.octave;
}
function keyVisual(n, down) { const el = document.querySelector(`.key[data-n="${n}"]`); if (el) el.classList.toggle('down', down); }
function typingTarget(e) { return /INPUT|SELECT|TEXTAREA/.test(e.target.tagName) && !['range', 'checkbox'].includes(e.target.type); }
document.addEventListener('keydown', e => {
  if (typingTarget(e) || e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === ' ' && S.tab !== 'editor') { Bus.dispatchEvent(new CustomEvent('transport', { detail: S.tab })); e.preventDefault(); return; }
  const i = KEYMAP.indexOf(e.key.toLowerCase());
  if (i >= 0) { const n = 12 * (S.octave + 1) + i; S.held.set(e.key.toLowerCase(), n); noteOn(n); e.preventDefault(); }
  else if (e.key === 'z') { S.octave = Math.max(0, S.octave - 1); buildKeys(); }
  else if (e.key === 'x') { S.octave = Math.min(8, S.octave + 1); buildKeys(); }
});
document.addEventListener('keyup', e => {
  const k = e.key.toLowerCase();
  if (S.held.has(k)) { noteOff(S.held.get(k)); S.held.delete(k); }
});

/* ---------------- tabs ---------------- */
function showTab(name) {
  S.tab = name;
  document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === name));
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('on', t.id === 'tab-' + name));
  document.body.dataset.tab = name;
  try { localStorage.setItem('fm1.tab', name); } catch {}
  Bus.dispatchEvent(new CustomEvent('tab', { detail: name }));
}

/* ---------------- wiring ---------------- */
function wire() {
  for (let c = 1; c <= 16; c++) { $('keyCh').add(new Option(c, c - 1)); $('fxCh').add(new Option(c, c - 1)); }
  $('keyCh').value = Engine.ch; $('fxCh').value = S.fxCh;
  $('keyCh').onchange = e => { Engine.panic(); Engine.ch = +e.target.value; Engine.forgetDevice(); };
  $('fxCh').onchange = e => { S.fxCh = +e.target.value; SoftFM1.setFxChannel(S.fxCh); if (Engine.out && Engine.out.isSoft) fxSendAll(); };
  $('connectBtn').onclick = connect;
  $('outSel').onchange = e => { setOut(outputs().get(e.target.value)); fillPorts(); };
  $('inSel').onchange = e => { if (!S.midi) return; if (S.inp) S.inp.onmidimessage = null; S.inp = S.midi.inputs.get(e.target.value); if (S.inp) S.inp.onmidimessage = onMidiIn; };
  $('panicBtn').onclick = panic;
  document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => showTab(b.dataset.tab));

  $('importInput').onchange = e => { importFiles([...e.target.files]); e.target.value = ''; };
  $('loadBundled').onclick = importBundled;
  const dz = $('dropZone');
  ['dragenter', 'dragover'].forEach(t => document.addEventListener(t, e => { e.preventDefault(); dz.classList.add('hot'); }));
  ['dragleave', 'drop'].forEach(t => document.addEventListener(t, e => { e.preventDefault(); if (t === 'drop' || e.target === dz) dz.classList.remove('hot'); }));
  document.addEventListener('drop', e => {
    const files = [...e.dataTransfer.files];
    const mids = files.filter(f => /\.midi?$/i.test(f.name)), syx = files.filter(f => !/\.midi?$/i.test(f.name) && !/\.json$/i.test(f.name));
    if (mids.length) Bus.dispatchEvent(new CustomEvent('midfile', { detail: mids[0] }));
    if (syx.length) importFiles(syx);
  });

  $('search').oninput = renderList; $('bankFilter').onchange = renderList; $('favOnly').onchange = renderList; $('catFilter').onchange = renderList;
  const ul = $('voiceList');
  ul.addEventListener('click', async e => {
    const li = e.target.closest('li[data-i]'); if (!li) return;
    const r = S.filtered[+li.dataset.i];
    if (e.target.classList.contains('pick')) {
      if (e.target.checked) { if (S.picks.size >= 32) { e.target.checked = false; log('A bank holds 32 voices', 'err'); return; } S.picks.add(r.id); }
      else S.picks.delete(r.id);
      $('pickCount').textContent = S.picks.size; return;
    }
    if (e.target.classList.contains('star')) { r.fav = !r.fav; await DB.put([r]); e.target.classList.toggle('on', r.fav); return; }
    if (e.target.classList.contains('add')) { Bus.dispatchEvent(new CustomEvent('addvoice', { detail: r })); return; }
    selectRow(+li.dataset.i);
  });
  ul.addEventListener('dblclick', e => { const li = e.target.closest('li[data-i]'); if (li && !e.target.closest('button,input')) loadRow(+li.dataset.i); });
  ul.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { selectRow(S.sel + 1); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { selectRow(S.sel - 1); e.preventDefault(); }
    else if (e.key === 'Enter') loadRow(S.sel);
  });
  $('pickClear').onclick = () => { S.picks.clear(); renderList(); };
  $('exportBank').onclick = () => {
    const vs = pickedVoices(); if (!vs.length) { log('Pick up to 32 voices first (checkboxes)', 'err'); return; }
    download('fm1-bank.syx', DX7.vmemSysex(vs)); log(`Exported bank of ${vs.length} voice(s)` + (vs.length < 32 ? `, padded with ${32 - vs.length} INIT` : ''));
  };
  $('sendBank').onclick = () => {
    const vs = pickedVoices(); if (!vs.length) { log('Pick up to 32 voices first (checkboxes)', 'err'); return; }
    if (!confirm(`Send a 32-voice bank (${vs.length} picked${vs.length < 32 ? ', rest INIT' : ''}) to the FM-1?\n\nThis may replace a bank of presets on the unit, and the FM-1 cannot send its presets back — make sure you are OK losing the current ones.`)) return;
    if (send(DX7.vmemSysex(vs, Engine.ch))) { Engine.forgetDevice(); log('Bank sent (4104 bytes)', 'ok'); }
  };
  $('deleteBank').onclick = async () => {
    if (!S.filtered.length) return;
    if (!confirm(`Delete ${S.filtered.length} voice(s) currently shown from the library?`)) return;
    await DB.del(S.filtered.map(r => r.id)); S.sel = -1; await refreshLib(); log('Deleted');
  };

  $('voiceName').oninput = e => {
    S.voice.name = DX7.cleanName(e.target.value);
    [...S.voice.name].forEach((c, i) => sendParam(145 + i, c.charCodeAt(0)));
  };
  $('algNum').onchange = e => {
    pushUndo(); S.voice.g.ALG = Math.max(0, Math.min(31, (+e.target.value || 1) - 1));
    sendParam(134, S.voice.g.ALG); refreshEditor();
  };
  $('algSvg').addEventListener('wheel', e => { e.preventDefault(); $('algNum').value = Math.max(1, Math.min(32, S.voice.g.ALG + 1 + (e.deltaY > 0 ? 1 : -1))); $('algNum').onchange({ target: $('algNum') }); });
  $('saveToTarget').onclick = () => { if (S.editTarget) { const t = S.editTarget; setEditTarget(null); t.save(DX7.toVced(S.voice)); } };
  $('sendVoice').onclick = () => { Engine.forgetDevice(); sendVoice(S.voice, true); log('Sent voice ' + S.voice.name.trim() + ' (all parameters)'); };
  $('saveVoice').onclick = async () => { await DB.put([recFromVoice(S.voice, 'My patches')]); await refreshLib(); log('Saved "' + S.voice.name.trim() + '" to My patches', 'ok'); };
  $('exportVoice').onclick = () => download((S.voice.name.trim() || 'voice').replace(/[^\w-]+/g, '_') + '.syx', DX7.vcedSysex(S.voice));
  $('initVoice').onclick = () => { pushUndo(); setVoice(DX7.initVoice()); sendVoice(S.voice, true); };
  $('randomize').onclick = () => { pushUndo(); setVoice(randomVoice($('randStyle').value)); audition(S.voice); };
  $('mutate').onclick = () => { pushUndo(); setVoice(mutateVoice(S.voice, +$('mutAmt').value)); audition(S.voice); };
  $('undo').onclick = () => { if (!S.undo.length) return; setVoice(S.undo.pop()); sendVoice(S.voice, true); };
  const morphReady = () => { $('morph').disabled = !(S.A && S.B); };
  $('setA').onclick = () => { S.A = DX7.clone(S.voice); $('aName').textContent = S.A.name.trim(); morphReady(); };
  $('setB').onclick = () => { S.B = DX7.clone(S.voice); $('bName').textContent = S.B.name.trim(); morphReady(); };
  $('morph').addEventListener('pointerdown', pushUndo);
  $('morph').oninput = e => { setVoice(morphVoice(S.A, S.B, +e.target.value / 1000)); sendVoice(); };

  $('fxSendAll').onclick = fxSendAll;
  $('octDown').onclick = () => { S.octave = Math.max(0, S.octave - 1); buildKeys(); };
  $('octUp').onclick = () => { S.octave = Math.min(8, S.octave + 1); buildKeys(); };
  $('vel').oninput = e => { S.vel = +e.target.value; $('velLbl').textContent = S.vel; };
  // firmware profile: Engine behaviour, Software FM-1 profile, master volume availability
  const setFirmware = fw => {
    Engine.firmware = fw; Engine.forgetDevice(); SoftFM1.setProfile(fw);
    $('fwSel').value = fw; $('masterWrap').classList.toggle('dim', fw !== 'va');
    try { localStorage.setItem('fm1.firmware', fw); } catch {}
  };
  let fw = 'stock'; try { fw = localStorage.getItem('fm1.firmware') || 'stock'; } catch {}
  setFirmware(fw);
  $('fwSel').onchange = e => { setFirmware(e.target.value); log(e.target.value === 'va' ? 'Firmware: FM-1+VA — voice changes also send CC 76/78 (LFO speed/delay); Volume sends CC 7' : 'Firmware: M-VAVE (stock)'); };
  $('master').oninput = e => {
    $('masterLbl').textContent = e.target.value;
    if (Engine.firmware !== 'va') return;
    if (Engine.ch === S.fxCh) { log('Volume needs Key ch and FX ch to differ (on one channel CC 7 is Reverb Mix)', 'err'); return; }
    send([0xB0 | Engine.ch, 7, +e.target.value]);
  };
  $('progSend').onclick =() => { send([0xC0 | Engine.ch, Math.max(0, Math.min(127, +$('prog').value - 1))]); Engine.forgetDevice(); log('Program change → preset ' + $('prog').value); };
}

(async function main() {
  wire(); buildEditor(); buildFx(); buildKeys();
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem('fm1.voice')); } catch {}
  setVoice(saved && saved.ops ? saved : DX7.initVoice(), true);
  let tab = 'editor'; try { tab = localStorage.getItem('fm1.tab') || 'editor'; } catch {}
  showTab(tab);
  await DB.open(); await refreshLib();
  if (!S.lib.length) log('Library is empty — drop .syx files, or click "Load built library" if you built one (see the README).');
  Bus.dispatchEvent(new Event('libready'));
  connect();
})();
