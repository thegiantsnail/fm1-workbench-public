// DX7/FM-1 operator EG timing in the software FM-1: for each rate, how long a fall (99 -> 0) takes to drop 6/20/40 dB
// and how long a rise (0 -> 99) takes to reach -20/-6 dB. Used to time staggered operator onsets (diphthong voices).
//   node speech/eg_table.cjs > speech/eg_table.json
const ctx = require('./webload.cjs')(['dx7.js', 'fm1-synth.js']), { DX7, FM1Core } = ctx;
const SR = 22050;
function env(ops) {
  const v = DX7.initVoice('EG');
  Object.assign(v.g, { ALG: 31, FB: 0, TRNP: 24, LFD: 0, LPMD: 0, LAMD: 0 });
  v.ops.forEach(o => Object.assign(o, { OL: 0, KVS: 0, DT: 7, FC: 1, FF: 0 }));
  Object.assign(v.ops[0], { MODE: 1, FC: 3, FF: 0, OL: 99 }, ops);         // 1 kHz fixed
  const d = DX7.toVced(v); d[144] = 24;
  const ev = d.map((x, p) => ({ t: p * 0.2, b: DX7.paramSysex(p, x, 0) })).filter((e, p) => p <= 144);
  ev.push({ t: 50, b: [0x90, 60, 127] }, { t: 2550, b: [0x80, 60, 0] });
  const c = new FM1Core(SR);
  for (const e of ev) c.queue(e.b, Math.round(e.t / 1000 * SR));
  const n = Math.round(2.5 * SR), out = new Float32Array(n), blk = new Float32Array(128);
  for (let f = 0; f < n; f += 128) { c.render(blk, f); out.set(blk.subarray(0, Math.min(128, n - f)), f); }
  const w = Math.round(0.002 * SR), on = Math.round(0.05 * SR), db = [];
  for (let i = on; i + w < n; i += w) { let s = 0; for (let k = 0; k < w; k++) s += out[i + k] ** 2; db.push(10 * Math.log10(s / w + 1e-20)); }
  return db;                                                                // 2 ms steps from note-on
}
const full = env({ R1: 99, L1: 99, R2: 99, L2: 99, R3: 99, L3: 99 }), ref = Math.max(...full.slice(5, 50));
const when = (db, f) => { const i = db.findIndex(f); return i < 0 ? null : i * 2; };
const rows = [];
for (let r = 0; r <= 99; r++) {
  const fall = env({ R1: 99, L1: 99, R2: r, L2: 0, R3: 99, L3: 0 }), rise = env({ R1: 99, L1: 0, R2: r, L2: 99, R3: 99, L3: 99 });
  rows.push({ r, fall6: when(fall, (d, i) => i > 3 && d < ref - 6), fall20: when(fall, (d, i) => i > 3 && d < ref - 20), fall40: when(fall, (d, i) => i > 3 && d < ref - 40),
    rise20: when(rise, d => d > ref - 20), rise6: when(rise, d => d > ref - 6) });
}
console.log(JSON.stringify(rows));
